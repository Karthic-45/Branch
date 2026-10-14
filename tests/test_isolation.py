"""The central claim, verified end to end against a real cluster and a real
vector index.

> Branch B cannot retrieve Branch A's conclusions. Same database, same index,
> same query -- a different belief state.

Every "not found" assertion here is paired with a "found" assertion from the
branch that legitimately holds the memory. Without that pairing the test would
also pass if search were simply broken.

Embeddings are deterministic fakes, and each query embeds the *exact* text of the
belief it hunts for, so the target sits at distance zero. If a row is reachable
at all it ranks first -- an empty result therefore means the row was excluded by
lineage, never that it merely ranked poorly.
"""

from __future__ import annotations

from typing import Any

import pytest

from bmem.branch import fork, merge
from bmem.db import run_transaction, transaction
from bmem.embed import FakeEmbedder
from bmem.retrieval import ancestry_cache, search

# From the worked example: true, uncontroversial, and inherited by every branch.
AUTH_FACT = "Auth middleware is implemented as a Flask before_request hook in auth/guard.py"

# Subtly wrong -- the wrapper loses request-scoped teardown -- but plausible,
# confident, and highly retrievable. In an append-only memory this sentence is
# permanent and poisons every later retrieval about middleware.
FALSE_BELIEF = "Flask hooks can be reused directly by wrapping them in a FastAPI dependency"


def find(hits: Any, key: str) -> bool:
    return any(h.key == key for h in hits)


class TestSiblingIsolation:
    def test_a_false_belief_is_invisible_to_a_sibling(
        self, four_way: dict[str, str], write: Any, embedder: FakeEmbedder
    ) -> None:
        write(four_way["shim"], "middleware-strategy", FALSE_BELIEF)
        ancestry_cache.clear()

        query = embedder([FALSE_BELIEF])[0]

        with transaction() as cur:
            from_owner = search(cur, four_way["shim"], query, k=8)
            from_sibling = search(cur, four_way["codemod"], query, k=8)

        # The branch that wrote it finds it at the top -- search works.
        assert find(from_owner, "middleware-strategy")
        assert from_owner[0].key == "middleware-strategy"

        # The sibling cannot reach it. Not down-weighted: absent.
        assert not find(from_sibling, "middleware-strategy")

    def test_no_sibling_can_reach_any_other(
        self, four_way: dict[str, str], write: Any, embedder: FakeEmbedder
    ) -> None:
        """Every branch writes a private conclusion; no branch sees another's."""
        for name, branch_id in four_way.items():
            write(branch_id, f"{name}-conclusion", f"Strategy {name} concluded X")
        ancestry_cache.clear()

        with transaction() as cur:
            for reader in four_way:
                for writer, _ in four_way.items():
                    query = embedder([f"Strategy {writer} concluded X"])[0]
                    hits = search(cur, four_way[reader], query, k=8)
                    reachable = find(hits, f"{writer}-conclusion")
                    assert reachable is (reader == writer), (
                        f"{reader} {'could' if reachable else 'could not'} reach "
                        f"{writer}'s conclusion"
                    )


class TestInheritance:
    def test_every_branch_inherits_the_parent_state_for_free(
        self, root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        write(root, "auth-fact", AUTH_FACT)

        with transaction() as cur:
            branches = {n: fork(cur, root, n) for n in ("shim", "codemod")}
        ancestry_cache.clear()

        query = embedder([AUTH_FACT])[0]
        with transaction() as cur:
            for name, branch_id in branches.items():
                hits = search(cur, branch_id, query, k=8)
                assert find(hits, "auth-fact"), f"{name} did not inherit the auth fact"
                assert next(h for h in hits if h.key == "auth-fact").inherited

    def test_no_rows_were_copied_to_achieve_that(self, root: str, write: Any) -> None:
        """Inheritance is lineage, not duplication -- forking writes no memory rows."""
        write(root, "auth-fact", AUTH_FACT)

        def _fork_fifty_and_count(cur: Any) -> tuple[int, int, int]:
            cur.execute("SELECT count(*) AS n FROM memory WHERE branch_id = %(b)s", {"b": root})
            before = cur.fetchone()["n"]

            # One INSERT for all 50, not fork() called 50 times. Fifty separate
            # round trips over a high-latency link made this transaction long
            # enough to reliably outlive CockroachDB's closed timestamp interval
            # under any concurrent write -- run_transaction's retry couldn't
            # save it, because a retry is exactly as slow as the attempt that
            # just failed. Duration is the actual problem here, not retry count.
            cur.execute(
                """
                INSERT INTO branches (parent_id, fork_point, hypothesis)
                SELECT %(parent_id)s, cluster_logical_timestamp(), h
                FROM unnest(%(hypotheses)s::STRING[]) AS h
                RETURNING id
                """,
                {"parent_id": root, "hypotheses": [f"hypothesis-{i}" for i in range(50)]},
            )
            child_ids = [str(row["id"]) for row in cur.fetchall()]

            # Scoped to exactly the 50 new branches, rather than the whole
            # shared table: cheaper, immune to contention from unrelated tests
            # writing concurrently, and the only query that actually tests the
            # claim -- a row appearing under any child's branch_id is the
            # copy-on-fork bug this test exists to catch.
            cur.execute(
                "SELECT count(*) AS n FROM memory WHERE branch_id = ANY(%(ids)s)",
                {"ids": child_ids},
            )
            in_children = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM memory WHERE branch_id = %(b)s", {"b": root})
            after_root = cur.fetchone()["n"]
            return before, after_root, in_children

        before, after_root, in_children = run_transaction(_fork_fifty_and_count)

        assert before == after_root == 1
        assert in_children == 0

    def test_a_branch_is_immune_to_what_the_parent_concludes_later(
        self, root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        write(root, "before-fork", "Known before the split")

        with transaction() as cur:
            child = fork(cur, root, "child")

        write(root, "after-fork", "Concluded by main after the split")
        ancestry_cache.clear()

        with transaction() as cur:
            before_hits = search(cur, child, embedder(["Known before the split"])[0], k=8)
            after_hits = search(cur, child, embedder(["Concluded by main after the split"])[0], k=8)

        assert find(before_hits, "before-fork")
        assert not find(after_hits, "after-fork")


class TestBranchLocalDeletion:
    def test_a_branch_can_retract_an_inherited_belief_without_affecting_siblings(
        self, root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        write(root, "shaky-fact", "The session store is Redis-backed")

        with transaction() as cur:
            doubter = fork(cur, root, "doubter")
            believer = fork(cur, root, "believer")

        # The doubter shadows the inherited key with a tombstone.
        write(doubter, "shaky-fact", "", tombstone=True)
        ancestry_cache.clear()

        query = embedder(["The session store is Redis-backed"])[0]
        with transaction() as cur:
            assert not find(search(cur, doubter, query, k=8), "shaky-fact")
            assert find(search(cur, believer, query, k=8), "shaky-fact")
            assert find(search(cur, root, query, k=8), "shaky-fact")


class TestMergeAndForget:
    def test_the_winner_reaches_main_and_the_losers_do_not(
        self, four_way: dict[str, str], root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        write(four_way["shim"], "middleware-strategy", FALSE_BELIEF)
        write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")

        merge(root, four_way["codemod"])
        ancestry_cache.clear()

        with transaction() as cur:
            winner_hits = search(cur, root, embedder(["AST transform with generated deps"])[0], k=8)
            loser_hits = search(cur, root, embedder([FALSE_BELIEF])[0], k=8)

        assert find(winner_hits, "codemod-plan")

        # The false belief is not corrected, annotated, or down-weighted. It is
        # on an abandoned branch, and main cannot see it.
        assert not find(loser_hits, "middleware-strategy")

    def test_merged_rows_do_not_leak_backwards_into_existing_siblings(
        self, four_way: dict[str, str], root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        """Guards the reason merged rows are re-stamped with a fresh timestamp.

        Every sibling forked before the merge, so each has a horizon on main that
        predates it. If a merged row carried the winner's original write time --
        which can precede those fork points -- the losing siblings would suddenly
        start seeing the winner's conclusions, reintroducing exactly the
        contamination branching exists to prevent.
        """
        write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")
        merge(root, four_way["codemod"])
        ancestry_cache.clear()

        query = embedder(["AST transform with generated deps"])[0]
        with transaction() as cur:
            for name in ("shim", "rewrite", "strangler"):
                hits = search(cur, four_way[name], query, k=8)
                assert not find(hits, "codemod-plan"), (
                    f"{name} retroactively gained visibility of the merged winner"
                )

    def test_siblings_are_retired_in_the_same_transaction(
        self, four_way: dict[str, str], root: str
    ) -> None:
        merge(root, four_way["codemod"])

        with transaction() as cur:
            cur.execute("SELECT id, status FROM branches WHERE parent_id = %(p)s", {"p": root})
            status = {str(r["id"]): r["status"] for r in cur.fetchall()}

        assert status[four_way["codemod"]] == "merged"
        for name in ("shim", "rewrite", "strangler"):
            assert status[four_way[name]] == "abandoned"

        assert sum(1 for s in status.values() if s == "merged") == 1


class TestSearchIsNotVacuous:
    """Anti-tests: prove the suite would notice if search stopped returning rows."""

    def test_search_returns_results_at_all(
        self, root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        write(root, "present", "A memory that definitely exists")
        ancestry_cache.clear()
        with transaction() as cur:
            hits = search(cur, root, embedder(["A memory that definitely exists"])[0], k=8)
        assert find(hits, "present")

    def test_unrelated_query_still_returns_the_branch_corpus(
        self, root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        # ANN search always returns nearest neighbours; it has no relevance
        # threshold. So an empty result really does mean "nothing in scope",
        # which is what the isolation assertions above rely on.
        write(root, "present", "A memory that definitely exists")
        ancestry_cache.clear()
        with transaction() as cur:
            hits = search(cur, root, embedder(["something completely unrelated"])[0], k=8)
        assert find(hits, "present")

    def test_a_branch_with_nothing_in_scope_returns_empty(self, four_way: dict[str, str]) -> None:
        embedder = FakeEmbedder()
        ancestry_cache.clear()
        with transaction() as cur:
            hits = search(cur, four_way["shim"], embedder(["anything"])[0], k=8)
        assert hits == []


@pytest.mark.cluster
def test_ttl_is_configured_on_branch_scratch(cluster: None) -> None:
    """Abandoned reasoning must expire on its own, with no cron job."""
    with transaction() as cur:
        cur.execute("SHOW CREATE TABLE branch_scratch")
        ddl = cur.fetchone()["create_statement"]
    assert "ttl_expire_after" in ddl
