"""run_migration() end to end: plan, fork four ways, explore in parallel,
evaluate, merge the winner. Needs a live cluster -- forking and merging
cannot be faked without losing the point of the test.

Scores here match the doc's worked example exactly (shim 61%, rewrite 44%,
codemod 98%, strangler 73%) so this test doubles as a check that the
orchestrator reproduces that story mechanically, not just in the demo script.
"""

from __future__ import annotations

import pytest

from bmem.branch import MergeConflict
from bmem.db import transaction
from bmem.embed import FakeEmbedder
from bmem.evaluate import EvaluationResult
from bmem.explore import Exploration
from bmem.orchestrate import run_migration
from bmem.plan import FakePlanner, Strategy

_STRATEGIES = [
    Strategy(name="shim", hypothesis="wrap Flask hooks as dependencies"),
    Strategy(name="rewrite", hypothesis="hand-rewrite every route"),
    Strategy(name="codemod", hypothesis="AST transform with generated deps"),
    Strategy(name="strangler", hypothesis="incremental route-by-route cutover"),
]
_SCORES = {"shim": 0.61, "rewrite": 0.44, "codemod": 0.98, "strangler": 0.73}


def _explore(hypothesis: str, context: list[str]) -> Exploration:
    # patch = hypothesis verbatim, so the scripted evaluator below can key off
    # it directly rather than needing to know which branch_id is which.
    return Exploration(belief=f"Implemented: {hypothesis}", patch=hypothesis)


def _make_scripted_evaluator():
    hypothesis_to_score = {s.hypothesis: _SCORES[s.name] for s in _STRATEGIES}

    # on_event: Evaluator's real implementations (CodeBuildEvaluator,
    # FakeEvaluator) both accept it now -- this hand-written stand-in has to
    # match the same protocol, not just the three positional args, or
    # evaluate_branch()'s unconditional on_event=on_event call breaks it.
    def _evaluate(run_id: str, branch_id: str, patch: str, *, on_event=None) -> EvaluationResult:
        score = hypothesis_to_score[patch]
        return EvaluationResult(passed=round(score * 320), total=320, score=score, reason="ok")

    return _evaluate


def test_full_migration_picks_the_highest_scorer_and_merges_it(
    root: str, embedder: FakeEmbedder
) -> None:
    result = run_migration(
        root,
        "migrate a 40,000-line Flask service to FastAPI",
        plan=FakePlanner(strategies=_STRATEGIES),
        explore=_explore,
        evaluate=_make_scripted_evaluator(),
        embed=embedder,
        n=4,
    )

    assert len(result.outcomes) == 4
    winner = next(o for o in result.outcomes if o.branch_id == result.winner_branch_id)
    assert winner.strategy.name == "codemod"
    assert winner.evaluation.score == 0.98

    with transaction() as cur:
        cur.execute(
            "SELECT status, score FROM branches WHERE id = %(id)s", {"id": result.winner_branch_id}
        )
        row = cur.fetchone()
        assert row["status"] == "merged"
        assert row["score"] == 0.98

        loser_ids = [o.branch_id for o in result.outcomes if o.branch_id != result.winner_branch_id]
        cur.execute("SELECT id, status FROM branches WHERE id = ANY(%(ids)s)", {"ids": loser_ids})
        statuses = {r["id"]: r["status"] for r in cur.fetchall()}
        assert set(statuses.values()) == {"abandoned"}


def test_the_winners_belief_reaches_the_parent(root: str, embedder: FakeEmbedder) -> None:
    result = run_migration(
        root,
        "migrate a 40,000-line Flask service to FastAPI",
        plan=FakePlanner(strategies=_STRATEGIES),
        explore=_explore,
        evaluate=_make_scripted_evaluator(),
        embed=embedder,
        n=4,
    )

    winner = next(o for o in result.outcomes if o.branch_id == result.winner_branch_id)

    with transaction() as cur:
        cur.execute(
            "SELECT content FROM memory WHERE branch_id = %(b)s AND key = %(k)s",
            {"b": root, "k": f"{winner.strategy.name}-belief"},
        )
        row = cur.fetchone()
    assert row is not None
    assert winner.strategy.hypothesis in row["content"]


def test_on_event_narrates_the_whole_run_in_order(root: str, embedder: FakeEmbedder) -> None:
    """The activity feed's entire premise: every stage a run passes through
    fires, in the order it actually happened, not just a final summary."""
    seen: list[tuple[str | None, str, str]] = []

    result = run_migration(
        root,
        "migrate a 40,000-line Flask service to FastAPI",
        plan=FakePlanner(strategies=_STRATEGIES),
        explore=_explore,
        evaluate=_make_scripted_evaluator(),
        embed=embedder,
        n=4,
        on_event=lambda branch_id, stage, message: seen.append((branch_id, stage, message)),
    )

    run_level_stages = [stage for branch_id, stage, _ in seen if branch_id is None]
    # "merged" is NOT here -- it fires with the winner's own branch_id (see
    # winner_stages below), not None. Only "merging" (the decision, before the
    # write) is a run-level event; order matters: planning before anything is
    # forked, merging only after every branch has a score to compare.
    assert run_level_stages == ["planning", "planned", "merging"]

    forked_for = {bid for bid, stage, _ in seen if stage == "forked"}
    assert forked_for == set(result.outcomes[i].branch_id for i in range(4))

    winner_stages = [stage for bid, stage, _ in seen if bid == result.winner_branch_id]
    assert winner_stages == ["forked", "exploring", "explored", "evaluating", "scored", "merged"]

    loser_id = next(o.branch_id for o in result.outcomes if o.branch_id != result.winner_branch_id)
    loser_stages = [stage for bid, stage, _ in seen if bid == loser_id]
    assert loser_stages == ["forked", "exploring", "explored", "evaluating", "scored", "abandoned"]


def test_on_event_is_optional_for_a_full_run(root: str, embedder: FakeEmbedder) -> None:
    """No on_event at all -- the shape every caller before this feature used,
    and the one every existing production call site still uses."""
    result = run_migration(
        root,
        "migrate a 40,000-line Flask service to FastAPI",
        plan=FakePlanner(strategies=_STRATEGIES),
        explore=_explore,
        evaluate=_make_scripted_evaluator(),
        embed=embedder,
        n=4,
    )
    assert len(result.outcomes) == 4


def test_second_run_on_a_merged_parent_fails_before_forking(
    root: str, embedder: FakeEmbedder
) -> None:
    """A parent holds one live winner. A second run against it used to plan,
    fork and evaluate every branch, then hit MergeConflict at the last step
    and leave n branches stuck in 'exploring' forever. It must now refuse up
    front and create nothing."""
    run_migration(
        root,
        "migrate a 40,000-line Flask service to FastAPI",
        plan=FakePlanner(strategies=_STRATEGIES),
        explore=_explore,
        evaluate=_make_scripted_evaluator(),
        embed=embedder,
        n=4,
    )

    def _children() -> int:
        with transaction() as cur:
            cur.execute("SELECT count(*) AS n FROM branches WHERE parent_id = %(p)s", {"p": root})
            return cur.fetchone()["n"]

    before = _children()
    with pytest.raises(MergeConflict, match="already merged"):
        run_migration(
            root,
            "a second goal against the same parent",
            plan=FakePlanner(strategies=_STRATEGIES),
            explore=_explore,
            evaluate=_make_scripted_evaluator(),
            embed=embedder,
            n=4,
        )
    assert _children() == before
