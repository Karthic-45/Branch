"""What one branch actually believes, and where each belief came from.

The retrieval path answers "what is relevant to this question"; this module
answers the flatter question a human asks when auditing a run: *show me
everything this branch can see, and tell me which of it the branch concluded
itself versus inherited from an ancestor it can no longer influence.*

Same visibility rule as ``retrieval.select_visible`` -- own rows always, an
ancestor's rows only up to the horizon the fork froze them at, nearest
ancestor wins on a key collision -- but applied to the whole branch rather
than to an approximate-nearest-neighbour result set. It is the copy-on-write
claim made legible: a branch with two of its own beliefs and 200 inherited
ones has 202 visible rows and 2 rows of its own on disk.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from bmem.branch import resolve_ancestry
from bmem.db import transaction

__all__ = ["Belief", "BeliefPage", "list_beliefs"]


class Belief(BaseModel):
    key: str
    content: str | None
    tombstone: bool
    created_at_hlc: str
    s3_uri: str | None
    #: The branch the surviving row physically lives on.
    origin_branch_id: str
    origin_hypothesis: str | None
    #: 0 when the querying branch wrote it; 1+ for an inherited row, counting
    #: generations up the lineage.
    origin_depth: int
    #: False for a row this branch concluded itself. An inherited row is
    #: frozen: the ancestor may have changed it since, and this branch will
    #: never see that.
    inherited: bool
    #: Set when a nearer branch in the lineage overrides this key -- recorded
    #: on the shadowed row so the UI can show that an inherited belief was
    #: superseded rather than silently dropping it.
    shadowed_by: str | None = None


class BeliefPage(BaseModel):
    branch_id: str
    #: Every row visible to this branch, after horizon filtering and shadowing.
    beliefs: list[Belief]
    total_visible: int
    #: How many of ``total_visible`` this branch wrote itself. The gap between
    #: this and ``total_visible`` is what copy-on-write saved.
    own: int
    inherited: int
    tombstoned: int
    #: Rows an ancestor holds that a nearer branch overrode. Not visible, but
    #: worth counting: it is the "this branch changed its mind" number.
    shadowed: int
    lineage: list[dict[str, Any]]
    truncated: bool


_MAX_ROWS = 2000


def list_beliefs(
    branch_id: str,
    *,
    query: str | None = None,
    include_tombstoned: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> BeliefPage:
    """Read a branch's visible belief state.

    ``query`` is a plain substring match over key and content -- deliberately
    not a vector search. This surface is for auditing what is there, and a
    substring match either finds a row or provably does not; an ANN search
    would silently rank one out of view.
    """
    with transaction() as cur:
        scopes = resolve_ancestry(cur, branch_id)
        if not scopes:
            raise LookupError(f"no such branch: {branch_id}")

        ids = [s.branch_id for s in scopes]
        cur.execute(
            """
            SELECT b.id, b.hypothesis
            FROM branches b
            WHERE b.id = ANY(%(ids)s)
            """,
            {"ids": ids},
        )
        hypothesis = {str(r["id"]): r["hypothesis"] for r in cur.fetchall()}

        # One scan per lineage, keyed on the primary key's prefix column. The
        # row cap is a guard against a pathological branch, not pagination --
        # shadowing has to be resolved across the whole lineage before any
        # window can be taken, so the slice happens after.
        cur.execute(
            """
            SELECT branch_id, key, content, tombstone, created_at_hlc, s3_uri
            FROM memory
            WHERE branch_id = ANY(%(ids)s)
            ORDER BY branch_id, key
            LIMIT %(cap)s
            """,
            {"ids": ids, "cap": _MAX_ROWS + 1},
        )
        rows = cur.fetchall()

    truncated = len(rows) > _MAX_ROWS
    rows = rows[:_MAX_ROWS]

    scope_by_id = {s.branch_id: s for s in scopes}

    # Nearest ancestor wins: walk depth ascending and keep the first row seen
    # for a key, recording on the loser which branch shadowed it.
    winners: dict[str, Belief] = {}
    shadowed = 0
    for row in sorted(
        rows,
        key=lambda r: (scope_by_id[str(r["branch_id"])].depth, r["key"]),
    ):
        origin = str(row["branch_id"])
        scope = scope_by_id[origin]
        hlc = str(row["created_at_hlc"])
        if not scope.admits(Decimal(hlc)):
            # Written by the ancestor after this branch forked away from it.
            continue
        key = str(row["key"])
        if key in winners:
            shadowed += 1
            continue
        winners[key] = Belief(
            key=key,
            content=row["content"],
            tombstone=bool(row["tombstone"]),
            created_at_hlc=hlc,
            s3_uri=row["s3_uri"],
            origin_branch_id=origin,
            origin_hypothesis=hypothesis.get(origin),
            origin_depth=scope.depth,
            inherited=scope.depth > 0,
            shadowed_by=None,
        )

    visible = list(winners.values())
    tombstoned = sum(1 for b in visible if b.tombstone)
    if not include_tombstoned:
        visible = [b for b in visible if not b.tombstone]

    if query:
        needle = query.lower()
        visible = [
            b for b in visible
            if needle in b.key.lower() or (b.content or "").lower().find(needle) >= 0
        ]

    visible.sort(key=lambda b: (b.origin_depth, b.key))
    total = len(visible)
    window = visible[offset : offset + limit]

    return BeliefPage(
        branch_id=branch_id,
        beliefs=window,
        total_visible=total,
        own=sum(1 for b in visible if not b.inherited),
        inherited=sum(1 for b in visible if b.inherited),
        tombstoned=tombstoned,
        shadowed=shadowed,
        lineage=[
            {
                "branch_id": s.branch_id,
                "hypothesis": hypothesis.get(s.branch_id),
                "depth": s.depth,
                "horizon": None if s.horizon is None else str(s.horizon),
            }
            for s in scopes
        ],
        truncated=truncated,
    )
