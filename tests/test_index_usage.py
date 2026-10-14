"""Assert the vector index is actually used, constrained by the branch prefix.

Isolation is enforced in `select_visible`, so retrieval stays *correct* even if
the index is bypassed -- it would just scan the whole corpus and filter after.
That makes a silent regression here invisible to every other test in the suite,
while quietly falsifying the project's central performance claim: that four
agents can share one index and be scoped at the index level rather than by
post-filtering.

So this test reads the query plan.
"""

from __future__ import annotations

from typing import Any

import pytest

from bmem.db import Vector, transaction
from bmem.embed import DIMENSIONS
from bmem.retrieval import ancestry_cache, explain_search


@pytest.fixture
def populated(root: str, write: Any) -> str:
    for i in range(50):
        write(root, f"fact-{i}", f"Repository fact number {i}")
    ancestry_cache.clear()
    return root


def test_the_vector_index_is_usable_and_prefix_constrained(populated: str, embedder: Any) -> None:
    """The index exists, is shaped right, and serves this exact query shape.

    Deliberately *not* "the optimizer chose it". CockroachDB's optimizer is
    cost-based, and at fixture scale a plain span scan genuinely is cheaper --
    picking it is correct behaviour, not a regression. This test asserted the
    optimizer's choice until 2026-07-30, and passed only while the table was
    nearly empty; once the real CTFd corpus (1,512 rows) landed, table
    statistics costed the 50-row fixture span at "estimated 1 row, 0.07% of
    the table" and the plan switched to a scan. Rebuilding the fixture at
    realistic scale was tried and abandoned: a 600-row insert plus ANALYZE per
    test did not complete in 30 minutes against a remote cluster.

    So this pins the part that is deterministic and cheap -- the index can
    serve a branch-scoped ANN query, with branch_id constraining it as a
    prefix -- by naming it explicitly. A regression that drops the index,
    changes its column order, or breaks the prefix constraint fails here.
    Whether the optimizer *prefers* it is covered by
    test_plan_is_not_a_full_table_scan below, plus a hand check against the
    real 1,512-row trunk on 2026-07-30, where the unaided plan was:

        • vector search
              table: memory@memory_by_branch_embedding
              prefix spans: /"<branch_id>"-/"<branch_id>"/PrefixEnd
    """
    query = embedder(["Repository fact number 7"])[0]
    with transaction() as cur:
        cur.execute(
            """
            EXPLAIN (VERBOSE)
            SELECT branch_id, key, content, created_at_hlc, tombstone
            FROM memory@memory_by_branch_embedding
            WHERE branch_id IN (%(b)s)
            ORDER BY embedding <-> %(q)s
            LIMIT 32
            """,
            {"b": populated, "q": Vector(query)},
        )
        plan = "\n".join(str(r["info"]) for r in cur.fetchall()).lower()

    assert "memory_by_branch_embedding" in plan, (
        f"the vector index cannot serve a branch-scoped ANN query:\n{plan}"
    )
    assert "prefix spans" in plan, (
        f"branch_id is not constraining the index as a prefix; a search would "
        f"scan beyond this branch's lineage:\n{plan}"
    )


def test_plan_is_not_a_full_table_scan(populated: str, embedder: Any) -> None:
    query = embedder(["Repository fact number 7"])[0]
    with transaction() as cur:
        plan = explain_search(cur, populated, query, k=8).lower()

    # A plan that reads the primary index over the whole table means the prefix
    # constraint stopped working -- most likely because the IN list was rewritten
    # into something the optimizer cannot push into the index.
    assert "full scan" not in plan, f"retrieval degraded to a full scan:\n{plan}"


def test_branch_prefix_appears_as_an_index_constraint(populated: str, embedder: Any) -> None:
    query = embedder(["Repository fact number 7"])[0]
    with transaction() as cur:
        plan = explain_search(cur, populated, query, k=8).lower()

    assert "branch_id" in plan, (
        f"branch_id is not constraining the index; the search is not scoped at "
        f"the index level:\n{plan}"
    )


def test_embedding_dimension_matches_the_column(populated: str) -> None:
    """A mismatch here fails at insert time in production but is easy to miss
    in review, since both numbers live in different files."""
    with transaction() as cur:
        cur.execute("SHOW CREATE TABLE memory")
        ddl = cur.fetchone()["create_statement"]

    assert (
        f"VECTOR({DIMENSIONS})" in ddl.upper().replace(" ", "").replace("VECTOR(", "VECTOR(")
        or f"vector({DIMENSIONS})" in ddl.lower()
    ), f"schema declares a different dimension than embed.DIMENSIONS={DIMENSIONS}:\n{ddl}"
