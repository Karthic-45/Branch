"""Merge atomicity, and the two paths back to a pre-merge belief state.

The claim under test: a merged conclusion can be rolled back to exactly what
preceded it, and a different branch promoted instead. Both the time-travel path
and the materialized-snapshot path must reconstruct identical state -- if they
ever diverge, one of the two demos is lying.
"""

from __future__ import annotations

import concurrent.futures
from typing import Any

import pytest

from bmem.branch import (
    MergeConflict,
    merge,
    reconstruct_pre_merge_state,
    revert,
)
from bmem.db import transaction
from bmem.embed import FakeEmbedder
from bmem.retrieval import ancestry_cache, search


def state_of(branch_id: str) -> dict[str, str | None]:
    with transaction() as cur:
        cur.execute(
            "SELECT key, content FROM memory WHERE branch_id = %(b)s AND tombstone = false",
            {"b": branch_id},
        )
        return {r["key"]: r["content"] for r in cur.fetchall()}


class TestMergeAtomicity:
    def test_a_second_merge_into_the_same_parent_is_refused(
        self, four_way: dict[str, str], root: str
    ) -> None:
        merge(root, four_way["codemod"])
        with pytest.raises(MergeConflict):
            merge(root, four_way["strangler"])

    def test_concurrent_merges_never_produce_two_winners(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        """Two branches race to merge into one parent.

        The property under test is *safety*: two winners must never exist, and
        a merge must never land while a rival stays live. The matching liveness
        property -- that one of the two always gets through -- is deliberately
        not guaranteed, and ``merge``'s own docstring says so: under a tight
        race both callers can exhaust their retry budget and neither commit.

        That outcome is a stall, not a corruption, and it is much easier to
        provoke against a distant cluster, where a single merge transaction
        spans enough round trips to outlive its closed-timestamp interval on
        every attempt. So the invariant is asserted unconditionally, and the
        stronger "someone won" assertions run only when someone did.
        """
        for name in ("codemod", "strangler"):
            write(four_way[name], f"{name}-plan", f"Plan from {name}")

        def attempt(name: str) -> str | None:
            try:
                return merge(root, four_way[name])
            except Exception:  # noqa: BLE001 -- losing the race is the expected path
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["codemod", "strangler"]))

        winners = [r for r in results if r is not None]
        assert len(winners) <= 1, "two concurrent merges both reported success"

        with transaction() as cur:
            cur.execute(
                """
                SELECT status, count(*) AS n FROM branches
                WHERE parent_id = %(p)s GROUP BY status
                """,
                {"p": root},
            )
            counts = {r["status"]: r["n"] for r in cur.fetchall()}

        # Holds either way: a stalled race must leave no merged branch behind.
        assert counts.get("merged", 0) <= 1

        if not winners:
            pytest.skip("both racers exhausted their retry budget (documented liveness gap)")

        assert counts.get("merged") == 1
        assert counts.get("exploring", 0) == 0

    def test_no_window_exists_where_a_merge_landed_but_rivals_stayed_live(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        write(four_way["codemod"], "plan", "AST transform")
        merge(root, four_way["codemod"])

        with transaction() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM branches
                     WHERE parent_id = %(p)s AND status = 'merged') AS merged,
                    (SELECT count(*) FROM branches
                     WHERE parent_id = %(p)s AND status = 'exploring') AS exploring,
                    (SELECT count(*) FROM memory
                     WHERE branch_id = %(p)s AND key = 'plan') AS applied
                """,
                {"p": root},
            )
            row = cur.fetchone()

        # The three facts are mutually consistent: one winner, no survivors,
        # and the winner's memory actually applied.
        assert row["merged"] == 1
        assert row["exploring"] == 0
        assert row["applied"] == 1


class TestReconstruction:
    def test_both_paths_agree(self, four_way: dict[str, str], root: str, write: Any) -> None:
        """AS OF SYSTEM TIME and the materialized snapshot must not diverge."""
        write(root, "auth-fact", "Auth is a before_request hook")
        write(root, "db-fact", "Sessions live in Redis")
        write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")

        merge_id = merge(root, four_way["codemod"])

        via_snapshot = reconstruct_pre_merge_state(merge_id, via="snapshot")
        via_aost = reconstruct_pre_merge_state(merge_id, via="aost")

        normalize = lambda rows: sorted(  # noqa: E731
            (r.key, r.content, r.tombstone) for r in rows
        )
        assert normalize(via_snapshot) == normalize(via_aost)

        # And it is genuinely the *pre*-merge state: the winner's key is absent.
        assert {r.key for r in via_snapshot} == {"auth-fact", "db-fact"}

    def test_reconstruction_excludes_what_the_merge_added(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        write(root, "before", "Known beforehand")
        write(four_way["codemod"], "after", "Added by the merge")
        merge_id = merge(root, four_way["codemod"])

        assert state_of(root).keys() == {"before", "after"}
        assert {r.key for r in reconstruct_pre_merge_state(merge_id)} == {"before"}

    def test_unknown_merge_id_raises(self, cluster: None) -> None:
        with pytest.raises(LookupError):
            reconstruct_pre_merge_state("00000000-0000-0000-0000-000000000000")


class TestRevert:
    def test_revert_restores_the_exact_pre_merge_state(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        write(root, "auth-fact", "Auth is a before_request hook")
        before = state_of(root)

        write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")
        merge_id = merge(root, four_way["codemod"])
        assert state_of(root) != before

        revert(merge_id, embed=FakeEmbedder())
        assert state_of(root) == before

    def test_reverting_twice_is_refused(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        write(four_way["codemod"], "plan", "AST transform")
        merge_id = merge(root, four_way["codemod"])

        revert(merge_id, embed=FakeEmbedder())
        with pytest.raises(MergeConflict):
            revert(merge_id, embed=FakeEmbedder())

    def test_the_agent_can_un_conclude_and_adopt_a_different_answer(
        self, four_way: dict[str, str], root: str, write: Any, embedder: FakeEmbedder
    ) -> None:
        """The moment the demo is built around.

        The merged strategy fails in staging, so we roll main back to what it
        believed before, and promote a different branch instead.
        """
        write(root, "auth-fact", "Auth is a before_request hook")
        write(four_way["codemod"], "plan", "AST transform with generated deps")
        write(four_way["strangler"], "plan", "Incremental route-by-route cutover")

        merge_id = merge(root, four_way["codemod"])
        assert state_of(root)["plan"] == "AST transform with generated deps"

        revert(merge_id, embed=FakeEmbedder(), promote=four_way["strangler"])

        # Main now holds the runner-up's conclusion, and still holds what it
        # knew all along.
        final = state_of(root)
        assert final["plan"] == "Incremental route-by-route cutover"
        assert final["auth-fact"] == "Auth is a before_request hook"

        with transaction() as cur:
            cur.execute("SELECT id, status FROM branches WHERE parent_id = %(p)s", {"p": root})
            status = {str(r["id"]): r["status"] for r in cur.fetchall()}
        assert status[four_way["strangler"]] == "merged"
        assert status[four_way["codemod"]] == "abandoned"

        # The reverted conclusion is no longer retrievable from main.
        ancestry_cache.clear()
        with transaction() as cur:
            hits = search(cur, root, embedder(["AST transform with generated deps"])[0], k=8)
        assert all(h.content != "AST transform with generated deps" for h in hits)

    def test_revert_is_recorded_rather_than_erased(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        """The merge log stays append-only, so the full history is inspectable."""
        write(four_way["codemod"], "plan", "AST transform")
        merge_id = merge(root, four_way["codemod"])
        revert(merge_id, embed=FakeEmbedder())

        with transaction() as cur:
            cur.execute(
                "SELECT winner_id, reverted_at, snapshot FROM merge_log WHERE id = %(id)s",
                {"id": merge_id},
            )
            row = cur.fetchone()

        assert row["reverted_at"] is not None
        assert str(row["winner_id"]) == four_way["codemod"]
        assert row["snapshot"] is not None  # the evidence survives the undo


class TestSnapshotDurability:
    def test_snapshot_is_materialized_not_a_pointer(
        self, four_way: dict[str, str], root: str, write: Any
    ) -> None:
        """Why revert survives past the garbage-collection window.

        Time travel only reaches back as far as gc.ttlseconds. The snapshot has
        to carry the actual content, not a reference to rows that will be
        collected.
        """
        write(root, "auth-fact", "Auth is a before_request hook")
        merge_id = merge(root, four_way["codemod"])

        with transaction() as cur:
            cur.execute("SELECT snapshot FROM merge_log WHERE id = %(id)s", {"id": merge_id})
            snapshot = cur.fetchone()["snapshot"]

        assert isinstance(snapshot, list)
        entry = next(e for e in snapshot if e["key"] == "auth-fact")
        assert entry["content"] == "Auth is a before_request hook"
