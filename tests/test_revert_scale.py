"""revert() against a realistically-sized parent.

The existing merge/revert tests use a handful of rows, which is why the
pathological case survived them: the original implementation deleted with
`key != ALL(<every snapshot key>)`, which CockroachDB plans as a vector
mutation search over the embedding index, and re-embedded every snapshot row
whether or not the merge had touched it. Both costs are invisible at three
rows and fatal at fifteen hundred.

These tests pin the two properties that keep revert usable at real ingest
scale:

  * the DELETE is keyed to the rows the merge actually added, so its plan
    stays on the primary key;
  * only rows whose content genuinely changed get re-embedded, because with
    real Titan every re-embed is a network call.
"""

from __future__ import annotations

from typing import Any

import pytest

from bmem.branch import fork, merge, revert
from bmem.db import Vector, run_transaction, transaction
from bmem.embed import FakeEmbedder

pytestmark = pytest.mark.cluster

_PARENT_ROWS = 300
_BATCH = 25


class CountingEmbedder:
    """FakeEmbedder that records how much work revert asked it to do.

    The count is the point: with real Titan each embedded string is an
    InvokeModel call, so "how many rows did revert re-embed" is directly how
    expensive and how slow a revert is.
    """

    def __init__(self) -> None:
        self._inner = FakeEmbedder()
        self.embedded: list[str] = []

    def __call__(self, texts: Any) -> Any:
        self.embedded.extend(texts)
        return self._inner(texts)


def _seed(branch_id: str, n: int, embed: Any) -> None:
    keys = [f"chunk-{i}" for i in range(n)]
    contents = [f"parent memory chunk {i}" for i in range(n)]
    vectors = embed(contents)
    for start in range(0, n, _BATCH):
        k = keys[start : start + _BATCH]
        c = contents[start : start + _BATCH]
        e = [Vector(v).literal for v in vectors[start : start + _BATCH]]

        def _write(cur: Any, k=k, c=c, e=e) -> None:
            cur.execute(
                """
                UPSERT INTO memory (branch_id, key, content, embedding,
                                    created_at_hlc, tombstone)
                SELECT %(b)s, t.k, t.c, t.e::VECTOR(1024),
                       cluster_logical_timestamp(), false
                FROM unnest(%(keys)s::STRING[], %(contents)s::STRING[],
                            %(embeddings)s::STRING[]) AS t(k, c, e)
                """,
                {"b": branch_id, "keys": k, "contents": c, "embeddings": e},
            )

        run_transaction(_write)


def _write_belief(branch_id: str, key: str, content: str, embed: Any) -> None:
    vec = Vector(embed([content])[0])

    def _write(cur: Any) -> None:
        cur.execute(
            """
            UPSERT INTO memory (branch_id, key, content, embedding,
                                created_at_hlc, tombstone)
            VALUES (%(b)s, %(k)s, %(c)s, %(e)s, cluster_logical_timestamp(), false)
            """,
            {"b": branch_id, "k": key, "c": content, "e": vec},
        )

    run_transaction(_write)


def _keys(branch_id: str) -> set[str]:
    with transaction() as cur:
        cur.execute("SELECT key FROM memory WHERE branch_id = %(b)s", {"b": branch_id})
        return {r["key"] for r in cur.fetchall()}


def _content(branch_id: str, key: str) -> str | None:
    with transaction() as cur:
        cur.execute(
            "SELECT content FROM memory WHERE branch_id = %(b)s AND key = %(k)s",
            {"b": branch_id, "k": key},
        )
        row = cur.fetchone()
        return row["content"] if row else None


def test_revert_restores_a_large_parent_exactly(root: str) -> None:
    """The whole point, at a size where the old implementation hung."""
    embed = FakeEmbedder()
    _seed(root, _PARENT_ROWS, embed)
    before = _keys(root)
    assert len(before) == _PARENT_ROWS

    child = run_transaction(lambda cur: fork(cur, root, "adds one belief"))
    _write_belief(child, "winner-belief", "the winning conclusion", embed)

    merge_id = merge(root, child)
    assert "winner-belief" in _keys(root)

    revert(merge_id, embed=embed)
    assert _keys(root) == before
    assert "winner-belief" not in _keys(root)


def test_revert_only_reembeds_rows_the_merge_actually_changed(root: str) -> None:
    """A merge that adds one key must not cost 300 embedding calls.

    This is the regression that matters for real Titan: the original
    implementation re-embedded the entire snapshot on every revert.
    """
    seed_embed = FakeEmbedder()
    _seed(root, _PARENT_ROWS, seed_embed)

    child = run_transaction(lambda cur: fork(cur, root, "adds one belief"))
    _write_belief(child, "winner-belief", "the winning conclusion", seed_embed)
    merge_id = merge(root, child)

    counting = CountingEmbedder()
    revert(merge_id, embed=counting)

    # The merge added exactly one key and changed nothing else, so there is
    # nothing to restore -- the untouched 300 rows are already correct.
    assert len(counting.embedded) == 0, (
        f"revert re-embedded {len(counting.embedded)} rows for a merge that "
        f"changed none of them"
    )


def test_revert_restores_a_value_the_merge_overwrote(root: str) -> None:
    """An overwritten key is the one case that *does* need re-embedding."""
    embed = FakeEmbedder()
    _seed(root, 10, embed)
    original = _content(root, "chunk-3")

    child = run_transaction(lambda cur: fork(cur, root, "overwrites chunk-3"))
    _write_belief(child, "chunk-3", "the child's replacement value", embed)
    merge_id = merge(root, child)
    assert _content(root, "chunk-3") == "the child's replacement value"

    counting = CountingEmbedder()
    revert(merge_id, embed=counting)

    assert _content(root, "chunk-3") == original
    # Exactly the one changed row, not the whole snapshot.
    assert len(counting.embedded) == 1


def test_revert_delete_scans_only_the_surplus_rows(root: str) -> None:
    """The plan itself, asserted -- and the contrast with the old form.

    A `vector mutation search` step appears on *any* delete from this table:
    that is simply how CockroachDB maintains the embedding index, and it is
    not avoidable (an earlier version of this test wrongly asserted its
    absence). What matters is how many rows feed it.

    Keying the delete to the surplus produces a tight primary-key span over
    the handful of rows the merge added. The old `key != ALL(<snapshot>)`
    form has to scan the whole branch and feed every row to that same vector
    mutation step -- which is what hung. Both plans are generated here so the
    difference is asserted rather than asserted-about.
    """
    embed = FakeEmbedder()
    _seed(root, _PARENT_ROWS, embed)
    existing = sorted(_keys(root))
    # The realistic shape: the snapshot held everything except the one key
    # the merge added, so exactly one row is surplus.
    snapshot_keys, surplus = existing[:-1], existing[-1:]

    def plan_for(sql: str, params: dict[str, Any]) -> str:
        with transaction() as cur:
            cur.execute("EXPLAIN " + sql, params)
            return "\n".join(str(r["info"]) for r in cur.fetchall())

    keyed = plan_for(
        "DELETE FROM memory WHERE branch_id = %(b)s AND key = ANY(%(keys)s)",
        {"b": root, "keys": surplus},
    )
    sweeping = plan_for(
        "DELETE FROM memory WHERE branch_id = %(b)s AND key != ALL(%(keys)s)",
        {"b": root, "keys": snapshot_keys},
    )

    # Both plans estimate one row -- that is the *result* cardinality, and it
    # is identical because both delete the same single row. The cost lives in
    # how many rows each has to look at to find it, which shows up as the scan
    # span and the presence of a filter node.
    assert surplus[0] in keyed, (
        f"the keyed delete should resolve to a point lookup on the surplus "
        f"key, but its span does not mention it:\n{keyed}"
    )
    assert "filter" not in keyed.lower(), (
        f"the keyed delete should need no filter -- the key list is the "
        f"predicate:\n{keyed}"
    )
    # The old form is the regression being guarded against: it scans the whole
    # branch and filters every row, which is what fed ~1,500 rows into the
    # vector mutation step and hung.
    assert "filter" in sweeping.lower(), (
        "expected the !=ALL form to require a filter over a full branch scan; "
        f"if this changed, the comparison in this test needs revisiting:\n{sweeping}"
    )
    assert "memory_pkey" in keyed, f"keyed delete left the primary index:\n{keyed}"
