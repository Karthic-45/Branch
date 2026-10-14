"""Fork, ancestry resolution, merge, and revert.

A branch is lineage, not a copy. Forking writes one row; reading walks the
ancestry chain and applies a per-ancestor visibility horizon.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from bmem.db import (
    HLC,
    Vector,
    cluster_logical_timestamp,
    run_transaction,
    time_travel,
    transaction,
)

__all__ = [
    "AncestorScope",
    "BranchNode",
    "MemoryRow",
    "MergeConflict",
    "create_root",
    "fork",
    "merge",
    "merged_child",
    "reconstruct_pre_merge_state",
    "resolve_ancestry",
    "resolve_subtree",
    "revert",
]


# Matches ingest.py's batch size for the same reason: proven not to trip
# SQLSTATE 40001 against this project's cluster link, and it bounds the number
# of round trips a revert of a large snapshot can make.
_REVERT_BATCH = 25


class MergeConflict(RuntimeError):
    """A second branch tried to merge into a parent that already has a winner."""


def merged_child(parent_id: str) -> str | None:
    """The id of ``parent_id``'s currently merged child, or ``None``.

    The same condition :func:`merge` guards on, exposed so callers can check it
    *before* doing expensive work (planning, forking, a build per branch) that
    would only end in a :class:`MergeConflict`. This is an advisory read, not a
    lock -- :func:`merge` still re-checks inside its serializable transaction.
    """
    with transaction() as cur:
        cur.execute(
            "SELECT id FROM branches WHERE parent_id = %(p)s AND status = 'merged' LIMIT 1",
            {"p": parent_id},
        )
        row = cur.fetchone()
    return str(row["id"]) if row else None


@dataclass(frozen=True, slots=True)
class AncestorScope:
    """One branch in a lineage, and how much of it the querying branch may see.

    ``horizon`` is ``None`` for the querying branch itself, meaning "everything
    you wrote". For every ancestor it is an HLC: rows written at or before that
    instant are inherited, rows written after it are invisible.
    """

    branch_id: str
    horizon: HLC | None
    depth: int

    def admits(self, created_at_hlc: HLC) -> bool:
        """Whether a row written at ``created_at_hlc`` is visible in this scope."""
        if self.horizon is None:
            return True
        return Decimal(created_at_hlc) <= Decimal(self.horizon)


@dataclass(frozen=True, slots=True)
class MemoryRow:
    key: str
    content: str | None
    tombstone: bool


# Walk child -> parent, accumulating a running minimum of the fork points along
# the path.
#
# Branch B forked from A at fB, and A forked from main at fA. B sees all of its
# own rows; it sees A only as A was at fB; and it sees main only as A saw it,
# which is main at fA. So each ancestor's horizon is the fork point of the child
# that descends toward us -- a running minimum, since fork points strictly
# decrease going up. LEAST() rather than plain assignment so the result stays
# correct even if a fork point were ever written out of order.
_ANCESTRY_SQL = """
WITH RECURSIVE lineage(id, parent_id, fork_point, horizon, depth) AS (
        SELECT id, parent_id, fork_point, NULL::DECIMAL AS horizon, 0 AS depth
        FROM branches
        WHERE id = %(branch_id)s
    UNION ALL
        SELECT p.id,
               p.parent_id,
               p.fork_point,
               CASE WHEN l.horizon IS NULL THEN l.fork_point
                    ELSE LEAST(l.horizon, l.fork_point) END,
               l.depth + 1
        FROM lineage l
        JOIN branches p ON p.id = l.parent_id
)
SELECT id, horizon, depth FROM lineage ORDER BY depth
"""


def create_root(
    cur: psycopg.Cursor[Any],
    hypothesis: str = "main",
    *,
    source_repo: str | None = None,
    source_ref: str | None = None,
    test_selector: str | None = None,
) -> str:
    """Create the trunk branch. Parentless, and by convention forked at zero.

    ``source_repo``/``source_ref``/``test_selector`` are what a migration
    started on this root later evaluates candidate patches against -- see
    ``api.start_migration``. Left NULL, as every root before self-service
    ingest existed always was, a migration falls back to the
    BMEM_TARGET_REPO/BMEM_TARGET_REF/BMEM_TEST_SELECTOR env vars instead.
    """
    cur.execute(
        """
        INSERT INTO branches (parent_id, fork_point, hypothesis,
                              source_repo, source_ref, test_selector)
        VALUES (NULL, 0, %(hypothesis)s, %(source_repo)s, %(source_ref)s, %(test_selector)s)
        RETURNING id
        """,
        {
            "hypothesis": hypothesis,
            "source_repo": source_repo,
            "source_ref": source_ref,
            "test_selector": test_selector,
        },
    )
    return str(cur.fetchone()["id"])


def fork(cur: psycopg.Cursor[Any], parent_id: str, hypothesis: str) -> str:
    """Fork a branch. One insert; no memory is copied.

    The fork point is read inside the caller's transaction, so a batch of
    sibling forks issued together share a consistent view of the parent.
    """
    cur.execute(
        """
        INSERT INTO branches (parent_id, fork_point, hypothesis)
        VALUES (%(parent_id)s, cluster_logical_timestamp(), %(hypothesis)s)
        RETURNING id
        """,
        {"parent_id": parent_id, "hypothesis": hypothesis},
    )
    return str(cur.fetchone()["id"])


def resolve_ancestry(cur: psycopg.Cursor[Any], branch_id: str) -> list[AncestorScope]:
    """Return the branch and its ancestors, nearest first, each with its horizon.

    The result is immutable once the branch exists -- a branch's parent and fork
    point never change -- so callers are free to cache it for the branch's
    lifetime. :mod:`bmem.retrieval` does exactly that.
    """
    cur.execute(_ANCESTRY_SQL, {"branch_id": branch_id})
    return [
        AncestorScope(
            branch_id=str(row["id"]),
            horizon=None if row["horizon"] is None else format(row["horizon"], "f"),
            depth=row["depth"],
        )
        for row in cur.fetchall()
    ]


# Walk parent -> children, the opposite direction from _ANCESTRY_SQL. Written
# out by hand five separate times today -- test teardown, four orphan-cleanup
# scripts -- before it was worth promoting to a real, tested function. That
# repetition is the actual justification for this existing at all: it is not
# a speculative abstraction, it is the fifth copy of a query that kept needing
# to exist.
_SUBTREE_SQL = """
WITH RECURSIVE subtree(id, parent_id, hypothesis, status, score, depth, created_at) AS (
        SELECT id, parent_id, hypothesis, status, score, 0, created_at
        FROM branches WHERE id = %(root_id)s
    UNION ALL
        SELECT b.id, b.parent_id, b.hypothesis, b.status, b.score, s.depth + 1, b.created_at
        FROM branches b JOIN subtree s ON b.parent_id = s.id
)
SELECT id, parent_id, hypothesis, status, score, depth, created_at FROM subtree ORDER BY depth
"""


@dataclass(frozen=True, slots=True)
class BranchNode:
    """One branch as seen from above -- for rendering a tree, not for the
    horizon/visibility rules :class:`AncestorScope` exists for."""

    id: str
    parent_id: str | None
    hypothesis: str | None
    status: str
    score: float | None
    depth: int
    # When the branch was forked (ISO 8601). Defaulted so every existing
    # BranchNode(...) construction keeps working unchanged.
    created_at: str | None = None


def resolve_subtree(cur: psycopg.Cursor[Any], root_id: str) -> list[BranchNode]:
    """Every branch reachable from ``root_id``, root included, nearest first.

    Unlike :func:`resolve_ancestry`, this is not safe to cache: new branches
    fork into a subtree throughout a migration run, so a cached result would
    go stale exactly while it is most useful (a live view during a run).
    """
    cur.execute(_SUBTREE_SQL, {"root_id": root_id})
    return [
        BranchNode(
            id=str(row["id"]),
            parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
            hypothesis=row["hypothesis"],
            status=row["status"],
            score=row["score"],
            depth=row["depth"],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
        )
        for row in cur.fetchall()
    ]


def merge(parent_id: str, winner_id: str) -> str:
    """Promote a branch into its parent and retire its siblings, atomically.

    Everything below happens in one serializable transaction, so there is no
    window in which two winners exist and none in which a branch is merged while
    its rivals are still live. Returns the ``merge_log`` id.

    Raises :class:`MergeConflict` if the parent already has a merged child.

    Run through :func:`bmem.db.run_transaction`: a small transaction is
    unlikely to hit CockroachDB's SQLSTATE 40001 transaction-retry error, but
    "unlikely" is not "impossible" by CockroachDB's own account of it, and this
    is the operation the entire demo's climax depends on -- it should not be the
    one place in the codebase that is allowed to fail on a transient restart.

    Verified under deliberate racing: two callers hitting the same parent
    concurrently always converge on exactly one winner, with the loser raising
    a clean :class:`MergeConflict` rather than an unretried low-level error --
    across 13 racing pairs run during development, that held 13/13. What is
    *not* guaranteed under a tight, synchronized race is that either side wins
    within :data:`bmem.db._RETRY_MAX_ATTEMPTS`; one pair observed both exhaust
    their retries and neither commit. That is a liveness gap, not a safety
    one -- two winners never happened -- and the real orchestrator never
    produces this race in the first place, since it calls ``merge`` exactly
    once per parent after picking a single winner. Left as-is rather than
    tuned further for a scenario the system does not actually exercise.

    Two sequential transactions, not one, and the split is load-bearing rather
    than stylistic -- see the comment above ``_write_merge_log`` below for why
    a single-transaction version of this function is guaranteed to fail once
    the parent holds a real corpus.
    """

    # pre_merge_hlc and the full snapshot are captured together, in one
    # read-only transaction that commits strictly before either write
    # transaction below opens.
    #
    # The timestamp cannot be taken *inside* a write transaction here:
    # cluster_logical_timestamp() there would return that transaction's own
    # commit instant, and AS OF SYSTEM TIME is inclusive of the instant it
    # names, so reading back at it would replay the write instead of preceding
    # it. The snapshot is read here for a related but distinct reason: it has
    # to describe the parent's state at this exact moment, before this
    # function's own UPSERT changes it -- see _write_merge_log for why reading
    # it is fine here but writing it is not fine inside the retried closure
    # below.
    with transaction() as cur:
        pre_merge_hlc = cluster_logical_timestamp(cur)
        cur.execute(
            "SELECT key, content, tombstone FROM memory WHERE branch_id = %(b)s",
            {"b": parent_id},
        )
        snapshot = [
            {"key": r["key"], "content": r["content"], "tombstone": r["tombstone"]}
            for r in cur.fetchall()
        ]

    def _merge(cur: psycopg.Cursor[Any]) -> None:
        # Guard first. Under SERIALIZABLE this read participates in the
        # transaction's conflict detection, so two concurrent merges into the
        # same parent cannot both observe "no winner yet" and both commit.
        cur.execute(
            """
            SELECT id FROM branches
            WHERE parent_id = %(parent_id)s AND status = 'merged'
            """,
            {"parent_id": parent_id},
        )
        existing = cur.fetchone()
        if existing is not None:
            raise MergeConflict(f"branch {existing['id']} is already merged into {parent_id}")

        # Stamp merged rows with a fresh HLC rather than carrying the winner's
        # original write times over.
        #
        # This matters: a sibling that forked at fC has horizon fC on the parent.
        # If a merged row kept a created_at_hlc earlier than fC, that sibling
        # would retroactively start seeing the winner's conclusions -- the exact
        # cross-contamination the branching model exists to prevent. Stamping
        # "now" puts every merged row strictly after every existing fork point.
        cur.execute(
            """
            UPSERT INTO memory (branch_id, key, content, embedding, created_at_hlc,
                                tombstone, s3_uri, base_sha)
            SELECT %(parent_id)s, key, content, embedding, cluster_logical_timestamp(),
                   tombstone, s3_uri, base_sha
            FROM memory
            WHERE branch_id = %(winner_id)s
            """,
            {"parent_id": parent_id, "winner_id": winner_id},
        )

        cur.execute(
            "UPDATE branches SET status = 'merged' WHERE id = %(winner_id)s",
            {"winner_id": winner_id},
        )
        cur.execute(
            """
            UPDATE branches SET status = 'abandoned'
            WHERE parent_id = %(parent_id)s
              AND id != %(winner_id)s
              AND status = 'exploring'
            """,
            {"parent_id": parent_id, "winner_id": winner_id},
        )

    run_transaction(_merge)

    # A separate transaction for the merge_log INSERT -- deliberately, and only
    # after the winner is already durably decided above.
    #
    # Found empirically, against the demo trunk's real 1,512-row / 1.77MB
    # corpus: a *single, completely uncontended* merge() that wrote this
    # snapshot inline exhausted every one of its 8 retries and failed outright.
    # Timing each statement in isolation placed the cost precisely -- writing
    # the 1.77MB snapshot took 18 seconds on its own (over this project's
    # documented high-latency dev link; see the cluster-latency note in
    # run_transaction's own docstring), while every other statement in this
    # function took under a second. Bundled into one transaction with the
    # guard, the UPSERT, and both status UPDATEs, that duration is what
    # outlives CockroachDB's closed-timestamp interval -- and retrying the
    # whole closure cannot fix it, because every retry repeats the exact same
    # 18-second write. This is the identical failure mode run_transaction's own
    # docstring already describes for ingest_repository() at ~130 rows, now hit
    # by merge() at real corpus scale.
    #
    # Splitting the INSERT out fixes the failure but opens a narrow, deliberate
    # gap: a process crash between the two transactions leaves the branch
    # correctly merged with no merge_log row to revert or audit it by. That is
    # the same tradeoff ingest_repository() already makes for the same reason
    # -- "an ingest that is behind, not wrong" -- and it is a better failure
    # mode than the one being fixed, which is "correctly merged, but the merge
    # never happens because the audit trail's own weight kills the atomic write
    # it doesn't need to be part of."
    def _write_merge_log(cur: psycopg.Cursor[Any]) -> str:
        cur.execute(
            """
            INSERT INTO merge_log (parent_id, winner_id, pre_merge_hlc, snapshot)
            VALUES (%(parent_id)s, %(winner_id)s, %(hlc)s, %(snapshot)s)
            RETURNING id
            """,
            {
                "parent_id": parent_id,
                "winner_id": winner_id,
                "hlc": Decimal(pre_merge_hlc),
                "snapshot": json.dumps(snapshot),
            },
        )
        return str(cur.fetchone()["id"])

    return run_transaction(_write_merge_log)


def reconstruct_pre_merge_state(merge_id: str, *, via: str = "snapshot") -> list[MemoryRow]:
    """Reconstruct what the parent believed immediately before a merge.

    ``via="snapshot"`` reads the materialized copy and always works.

    ``via="aost"`` runs a time-travel query against the recorded HLC. It is the
    more striking demonstration -- the same table, queried as it existed at a
    past instant -- but it only reaches back as far as the garbage-collection
    window, so it raises once the merge falls outside it.

    Both paths must return identical state; ``tests/test_merge_revert.py``
    asserts that.
    """
    if via not in ("snapshot", "aost"):
        raise ValueError(f"unknown reconstruction path: {via!r}")

    with transaction() as cur:
        cur.execute(
            "SELECT parent_id, pre_merge_hlc, snapshot FROM merge_log WHERE id = %(id)s",
            {"id": merge_id},
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"no such merge: {merge_id}")

    if via == "snapshot":
        return [
            MemoryRow(key=e["key"], content=e["content"], tombstone=e["tombstone"])
            for e in row["snapshot"]
        ]

    # A second transaction, deliberately -- the merge_log lookup above cannot
    # share one with this read. The lookup necessarily runs at `now` (merge_log
    # is where the past timestamp is *stored*), which fixes that transaction's
    # timestamp at `now`; a time-travel read inside it is a contradiction the
    # server rejects. time_travel() opens its own transaction and pins the
    # whole thing to pre_merge_hlc before reading anything.
    with time_travel(row["pre_merge_hlc"]) as cur:
        cur.execute(
            "SELECT key, content, tombstone FROM memory WHERE branch_id = %(branch_id)s",
            {"branch_id": row["parent_id"]},
        )
        return [
            MemoryRow(key=r["key"], content=r["content"], tombstone=r["tombstone"])
            for r in cur.fetchall()
        ]


def revert(
    merge_id: str,
    *,
    embed: Callable[[Sequence[str]], Sequence[Sequence[float]]],
    promote: str | None = None,
) -> str | None:
    """Undo a merge, restoring the parent's exact pre-merge belief state.

    Reads the materialized snapshot rather than time-travelling, so this keeps
    working after the merge falls outside the GC window. Embeddings are not
    stored in the snapshot (they are large and derivable), so restored rows are
    re-embedded via ``embed``.

    If ``promote`` names a sibling, that branch is merged in place of the one
    being undone -- the agent un-concludes something and adopts a different
    answer instead.

    Run through :func:`bmem.db.run_transaction`, so ``embed`` gets called again
    on the rare retry -- unlike :func:`bmem.ingest.ingest_repository`, the
    "already reverted" guard has to be checked in the same transaction as the
    writes it guards (otherwise two concurrent reverts of the same merge could
    both pass the check before either writes), which leaves no point before the
    retry boundary to safely hoist the embedding calls out to.
    """

    def _revert(cur: psycopg.Cursor[Any]) -> str:
        cur.execute(
            """
            SELECT parent_id, winner_id, snapshot, reverted_at
            FROM merge_log WHERE id = %(id)s
            """,
            {"id": merge_id},
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"no such merge: {merge_id}")
        if row["reverted_at"] is not None:
            raise MergeConflict(f"merge {merge_id} has already been reverted")

        parent_id = str(row["parent_id"])
        snapshot: list[dict[str, Any]] = row["snapshot"]

        # Revert by *difference*, not by rewriting the whole snapshot.
        #
        # The obvious implementation -- delete everything not in the snapshot,
        # then re-UPSERT every snapshot row -- is quadratically wrong here for
        # two reasons, both found against a real 1,500-chunk ingest:
        #
        #   1. `key != ALL(<1500 keys>)` makes CockroachDB plan a vector
        #      mutation search over memory@memory_by_branch_embedding for the
        #      delete (confirmed by EXPLAIN). That is dramatically more
        #      expensive than a btree delete and hung indefinitely on this
        #      cluster.
        #   2. Re-embedding every snapshot row means one Titan call per row.
        #      A merge only ever changes a handful of keys, so ~1,500 of those
        #      calls restore content that is already byte-identical.
        #
        # A merge UPSERTs the winner's rows into the parent, so the parent now
        # holds exactly `pre_merge ∪ winner`. Undoing it therefore touches only
        # the keys the winner added or overwrote -- typically single digits.
        # Computing that delta first turns an unbounded delete into a keyed one
        # and drops the embedding work to only the rows whose content actually
        # differs.
        cur.execute(
            "SELECT key, content, tombstone FROM memory WHERE branch_id = %(b)s",
            {"b": parent_id},
        )
        current = {r["key"]: r for r in cur.fetchall()}
        wanted = {e["key"]: e for e in snapshot}

        # Keys the merge introduced: present now, absent from the pre-merge
        # state. Deleted by explicit key list, which stays on the primary key.
        surplus = sorted(set(current) - set(wanted))
        if surplus:
            cur.execute(
                "DELETE FROM memory WHERE branch_id = %(b)s AND key = ANY(%(keys)s)",
                {"b": parent_id, "keys": surplus},
            )

        # Keys whose value the merge changed, plus any that went missing.
        # Untouched rows are left exactly as they are -- restoring a row to the
        # value it already holds would burn an embedding call to write it back
        # unchanged.
        stale = [
            entry
            for key, entry in wanted.items()
            if key not in current
            or current[key]["content"] != entry["content"]
            or current[key]["tombstone"] != entry["tombstone"]
        ]

        if stale:
            vectors = embed([e["content"] or "" for e in stale])
            for start in range(0, len(stale), _REVERT_BATCH):
                batch = stale[start : start + _REVERT_BATCH]
                batch_vectors = vectors[start : start + _REVERT_BATCH]
                cur.execute(
                    """
                    UPSERT INTO memory (branch_id, key, content, embedding,
                                        created_at_hlc, tombstone)
                    SELECT %(b)s, t.k, t.c, t.e::VECTOR(1024),
                           cluster_logical_timestamp(), t.d
                    FROM unnest(%(keys)s::STRING[], %(contents)s::STRING[],
                                %(embeddings)s::STRING[], %(tombstones)s::BOOL[])
                        AS t(k, c, e, d)
                    """,
                    {
                        "b": parent_id,
                        "keys": [e["key"] for e in batch],
                        "contents": [e["content"] for e in batch],
                        "embeddings": [Vector(v).literal for v in batch_vectors],
                        "tombstones": [e["tombstone"] for e in batch],
                    },
                )

        cur.execute(
            "UPDATE merge_log SET reverted_at = now() WHERE id = %(id)s",
            {"id": merge_id},
        )
        cur.execute(
            "UPDATE branches SET status = 'abandoned' WHERE id = %(w)s",
            {"w": row["winner_id"]},
        )
        return parent_id

    parent_id = run_transaction(_revert)

    if promote is None:
        return None
    return merge(parent_id, promote)
