"""select_winner() -- who wins, and whether the score actually decided it.

Pure and offline. This is the function that turns four numbers into one
promoted belief, so the interesting cases are the ones where the numbers do
not speak for themselves.
"""

from __future__ import annotations

import pytest

from bmem.evaluate import EvaluationResult
from bmem.explore import Exploration
from bmem.orchestrate import TIE_EPSILON, BranchOutcome, select_winner
from bmem.plan import Strategy


def outcome(name: str, score: float, *, patch: str = "", reason: str = "ok") -> BranchOutcome:
    return BranchOutcome(
        branch_id=f"branch-{name}",
        strategy=Strategy(name=name, hypothesis=f"hypothesis for {name}"),
        exploration=Exploration(belief=f"belief for {name}", patch=patch),
        evaluation=EvaluationResult(
            passed=int(score * 100), total=100, score=score, reason=reason
        ),
    )


def diff_over(files: int, lines: int = 1) -> str:
    body = "".join(f"+line{i}\n" for i in range(lines))
    return "".join(f"--- a/f{i}.py\n+++ b/f{i}.py\n@@\n{body}" for i in range(files))


class TestClearWinner:
    def test_highest_score_wins_when_the_margin_is_decisive(self) -> None:
        result = select_winner([outcome("a", 0.60), outcome("b", 0.90), outcome("c", 0.40)])
        assert result.winner.strategy.name == "b"
        assert result.tie_detected is False
        assert result.discriminator is None

    def test_runner_up_is_the_second_highest(self) -> None:
        result = select_winner([outcome("a", 0.60), outcome("b", 0.90), outcome("c", 0.40)])
        assert result.runner_up is not None
        assert result.runner_up.strategy.name == "a"

    def test_margin_is_the_real_gap(self) -> None:
        result = select_winner([outcome("a", 0.60), outcome("b", 0.90)])
        assert result.margin == pytest.approx(0.30)

    def test_rationale_cites_both_scores(self) -> None:
        result = select_winner([outcome("a", 0.60), outcome("b", 0.90)])
        assert "90" in result.rationale and "60" in result.rationale


class TestSingleBranch:
    def test_one_outcome_wins_without_a_runner_up(self) -> None:
        result = select_winner([outcome("solo", 0.75)])
        assert result.winner.strategy.name == "solo"
        assert result.runner_up is None
        assert result.margin is None
        assert result.tie_detected is False

    def test_rationale_says_it_was_unopposed(self) -> None:
        assert "only branch" in select_winner([outcome("solo", 0.75)]).rationale

    def test_empty_outcomes_is_an_error_not_a_silent_none(self) -> None:
        with pytest.raises(ValueError):
            select_winner([])


class TestTieBreaking:
    def test_near_identical_scores_are_flagged_as_a_tie(self) -> None:
        result = select_winner([outcome("a", 0.872), outcome("b", 0.871)])
        assert result.tie_detected is True
        assert result.discriminator == "smaller_patch"

    def test_the_smaller_patch_wins_a_tie_even_when_nominally_behind(self) -> None:
        # 'a' is nominally 0.1pp ahead but rewrites 10 files; 'b' changes 2.
        result = select_winner(
            [
                outcome("a", 0.872, patch=diff_over(10)),
                outcome("b", 0.871, patch=diff_over(2)),
            ]
        )
        assert result.winner.strategy.name == "b"

    def test_a_tie_rationale_states_the_score_did_not_decide(self) -> None:
        result = select_winner(
            [outcome("a", 0.872, patch=diff_over(10)), outcome("b", 0.871, patch=diff_over(2))]
        )
        assert "did not decide" in result.rationale
        assert "2 files" in result.rationale

    def test_exactly_epsilon_apart_is_still_a_tie(self) -> None:
        # Boundary: the rule is "within epsilon", inclusive.
        result = select_winner([outcome("a", 0.90), outcome("b", 0.90 - TIE_EPSILON)])
        assert result.tie_detected is True

    def test_just_over_epsilon_is_not_a_tie(self) -> None:
        result = select_winner([outcome("a", 0.90), outcome("b", 0.90 - TIE_EPSILON - 0.001)])
        assert result.tie_detected is False
        assert result.winner.strategy.name == "a"

    def test_only_branches_tied_with_the_leader_join_the_tiebreak(self) -> None:
        # 'c' has the smallest patch of all, but is far behind on score and
        # must not be pulled into the tie-break by that alone.
        result = select_winner(
            [
                outcome("a", 0.90, patch=diff_over(10)),
                outcome("b", 0.895, patch=diff_over(4)),
                outcome("c", 0.10, patch=diff_over(1)),
            ]
        )
        assert result.winner.strategy.name == "b"

    def test_lines_changed_breaks_a_tie_at_equal_file_counts(self) -> None:
        result = select_winner(
            [
                outcome("a", 0.90, patch=diff_over(2, lines=50)),
                outcome("b", 0.90, patch=diff_over(2, lines=3)),
            ]
        )
        assert result.winner.strategy.name == "b"


class TestDegenerateScores:
    def test_all_zero_scores_still_produce_a_winner(self) -> None:
        # Every branch failing to apply is the common case under a fake
        # planner, and the run must still resolve rather than raise.
        result = select_winner(
            [outcome("a", 0.0, reason="patch_did_not_apply"), outcome("b", 0.0)]
        )
        assert result.winner is not None
        assert result.tie_detected is True

    def test_identical_everything_is_deterministic(self) -> None:
        # No patch, same score: min() must not depend on dict/set ordering,
        # or the same run would promote different branches on replay.
        pair = [outcome("a", 0.5), outcome("b", 0.5)]
        assert select_winner(pair).winner.branch_id == select_winner(pair).winner.branch_id
