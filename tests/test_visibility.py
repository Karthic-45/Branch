"""Visibility rules: horizon, shadowing, and tombstones.

These encode the semantics the whole project rests on, so they are tested as
pure functions against fabricated index output -- no cluster, no embeddings, no
network. A regression here is a correctness bug in the isolation guarantee, and
it should be catchable in 50ms.

Lineage used throughout (fork points ascend as you go down):

    main ──(forked at 100)──> feature ──(forked at 200)──> deep
      │
      └────(forked at 150)──> sibling

So for `deep`:  deep horizon=None, feature horizon=200, main horizon=100.
"""

from __future__ import annotations

from bmem.branch import AncestorScope
from bmem.retrieval import Candidate, select_visible

MAIN, FEATURE, DEEP, SIBLING = "main", "feature", "deep", "sibling"

# Ancestry as resolve_ancestry() returns it: nearest first.
DEEP_SCOPES = [
    AncestorScope(branch_id=DEEP, horizon=None, depth=0),
    AncestorScope(branch_id=FEATURE, horizon="200", depth=1),
    AncestorScope(branch_id=MAIN, horizon="100", depth=2),
]


def candidate(
    branch_id: str,
    key: str,
    *,
    hlc: str,
    distance: float = 0.5,
    tombstone: bool = False,
    content: str | None = None,
) -> Candidate:
    return Candidate(
        branch_id=branch_id,
        key=key,
        content=content if content is not None else f"{branch_id}:{key}",
        created_at_hlc=hlc,
        tombstone=tombstone,
        distance=distance,
    )


class TestHorizon:
    def test_a_branch_sees_everything_it_wrote_itself(self) -> None:
        # horizon=None means unbounded; even a very late write is visible.
        hits = select_visible([candidate(DEEP, "note", hlc="999999")], DEEP_SCOPES, k=8)
        assert [h.key for h in hits] == ["note"]
        assert hits[0].inherited is False

    def test_inherits_ancestor_rows_written_before_the_fork(self) -> None:
        hits = select_visible([candidate(FEATURE, "auth-fact", hlc="150")], DEEP_SCOPES, k=8)
        assert [h.key for h in hits] == ["auth-fact"]
        assert hits[0].inherited is True
        assert hits[0].source_branch_id == FEATURE

    def test_row_written_exactly_at_the_fork_point_is_inherited(self) -> None:
        # The horizon is inclusive: the fork captures the parent's state at that
        # instant, so a write stamped with the fork point itself is included.
        hits = select_visible([candidate(FEATURE, "edge", hlc="200")], DEEP_SCOPES, k=8)
        assert [h.key for h in hits] == ["edge"]

    def test_ancestor_writes_after_the_fork_are_invisible(self) -> None:
        # This is the guarantee: once forked, a branch is immune to anything the
        # parent concludes afterwards.
        hits = select_visible([candidate(FEATURE, "later-idea", hlc="201")], DEEP_SCOPES, k=8)
        assert hits == []

    def test_horizon_tightens_with_depth(self) -> None:
        # main's horizon is 100, not 200: `deep` only ever saw main through
        # `feature`, which itself forked at 100.
        candidates = [
            candidate(MAIN, "old", hlc="99"),
            candidate(MAIN, "mid", hlc="150"),  # after main->feature fork
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert [h.key for h in hits] == ["old"]


class TestSiblingIsolation:
    def test_rows_from_outside_the_lineage_are_dropped(self) -> None:
        """The claim the whole project makes.

        `sibling` forked from main independently. Nothing it writes is ever
        reachable from `deep`, regardless of timestamp or similarity.
        """
        candidates = [
            candidate(SIBLING, "false-belief", hlc="1", distance=0.0),
            candidate(DEEP, "real-note", hlc="500", distance=0.9),
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert [h.key for h in hits] == ["real-note"]

    def test_a_closer_sibling_match_never_outranks_a_worse_own_match(self) -> None:
        # Sibling content is not down-weighted, it is absent. Even a perfect
        # distance of 0.0 does not surface it.
        candidates = [candidate(SIBLING, "perfect", hlc="1", distance=0.0)]
        assert select_visible(candidates, DEEP_SCOPES, k=8) == []


class TestShadowing:
    def test_nearest_branch_wins_a_contested_key(self) -> None:
        candidates = [
            candidate(MAIN, "auth", hlc="50", content="main's version"),
            candidate(FEATURE, "auth", hlc="150", content="feature's version"),
            candidate(DEEP, "auth", hlc="300", content="deep's version"),
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert len(hits) == 1
        assert hits[0].content == "deep's version"
        assert hits[0].source_branch_id == DEEP

    def test_shadowing_ignores_candidates_the_horizon_already_excluded(self) -> None:
        # feature's row is out of scope, so main's older one is what gets seen --
        # not "nothing, because a nearer branch had the key".
        candidates = [
            candidate(MAIN, "auth", hlc="50", content="main's version"),
            candidate(FEATURE, "auth", hlc="900", content="written after fork"),
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert [h.content for h in hits] == ["main's version"]


class TestTombstones:
    def test_a_branch_can_delete_an_inherited_key_for_itself(self) -> None:
        candidates = [
            candidate(MAIN, "bad-fact", hlc="50"),
            candidate(DEEP, "bad-fact", hlc="300", tombstone=True),
        ]
        assert select_visible(candidates, DEEP_SCOPES, k=8) == []

    def test_tombstone_must_win_shadowing_before_it_applies(self) -> None:
        # A tombstone on a FARTHER ancestor loses to a nearer live row, so the
        # key stays visible.
        candidates = [
            candidate(MAIN, "fact", hlc="50", tombstone=True),
            candidate(FEATURE, "fact", hlc="150", content="revived"),
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert [h.content for h in hits] == ["revived"]

    def test_out_of_scope_tombstone_does_not_delete_anything(self) -> None:
        candidates = [
            candidate(MAIN, "fact", hlc="50", content="still here"),
            candidate(FEATURE, "fact", hlc="900", tombstone=True),  # after fork
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=8)
        assert [h.content for h in hits] == ["still here"]

    def test_a_tombstone_never_appears_as_a_hit(self) -> None:
        candidates = [candidate(DEEP, "gone", hlc="300", tombstone=True)]
        assert select_visible(candidates, DEEP_SCOPES, k=8) == []


class TestRanking:
    def test_orders_by_distance_and_truncates_to_k(self) -> None:
        candidates = [
            candidate(DEEP, f"k{i}", hlc="300", distance=d)
            for i, d in enumerate([0.9, 0.1, 0.5, 0.3])
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=2)
        assert [h.key for h in hits] == ["k1", "k3"]

    def test_truncation_happens_after_filtering_not_before(self) -> None:
        # Over-fetched rows that are then filtered out must not consume slots in
        # the final top-k -- otherwise a branch with many out-of-scope neighbours
        # would silently return a short result.
        candidates = [
            candidate(SIBLING, "noise1", hlc="1", distance=0.01),
            candidate(SIBLING, "noise2", hlc="1", distance=0.02),
            candidate(DEEP, "signal", hlc="300", distance=0.80),
        ]
        hits = select_visible(candidates, DEEP_SCOPES, k=2)
        assert [h.key for h in hits] == ["signal"]


class TestAncestorScopeAdmits:
    def test_unbounded_scope_admits_any_timestamp(self) -> None:
        scope = AncestorScope(branch_id=DEEP, horizon=None, depth=0)
        assert scope.admits("0")
        assert scope.admits("99999999999999999999.9999999999")

    def test_comparison_is_decimal_not_string(self) -> None:
        # "9" > "10" lexicographically but 9 < 10 numerically. Comparing HLCs as
        # strings would misjudge every boundary that crosses a digit count.
        scope = AncestorScope(branch_id=FEATURE, horizon="10", depth=1)
        assert scope.admits("9") is True
        assert scope.admits("10") is True
        assert scope.admits("11") is False

    def test_comparison_keeps_full_precision(self) -> None:
        # HLCs carry a logical counter well past float's 53 bits of mantissa;
        # going through float would collapse these three onto one value.
        scope = AncestorScope(branch_id=FEATURE, horizon="1755712043123456789.0000000002", depth=1)
        assert scope.admits("1755712043123456789.0000000001") is True
        assert scope.admits("1755712043123456789.0000000002") is True
        assert scope.admits("1755712043123456789.0000000003") is False
