"""evaluate_branch() against a real cluster: the score write-back.

The Evaluator itself is faked (FakeEvaluator) -- what this file verifies is
the part that's real regardless: evaluate_branch() writes exactly what the
evaluator returned into branches.score, on exactly the branch that was
evaluated, and nowhere else.
"""

from __future__ import annotations

from bmem.db import transaction
from bmem.evaluate import EvaluationResult, FakeEvaluator, evaluate_branch


def test_score_is_written_to_the_evaluated_branch(four_way: dict[str, str]) -> None:
    branch_id = four_way["codemod"]
    result = EvaluationResult(passed=314, total=320, score=0.98125, reason="ok")

    returned = evaluate_branch(branch_id, "run-1", "a patch", evaluate=FakeEvaluator(result=result))

    assert returned == result
    with transaction() as cur:
        cur.execute("SELECT score FROM branches WHERE id = %(id)s", {"id": branch_id})
        row = cur.fetchone()
    assert row["score"] == 0.98125


def test_score_is_isolated_to_the_evaluated_branch(four_way: dict[str, str]) -> None:
    codemod, shim = four_way["codemod"], four_way["shim"]

    evaluate_branch(codemod, "run-1", "a patch", evaluate=FakeEvaluator(score=0.98))

    with transaction() as cur:
        cur.execute("SELECT score FROM branches WHERE id = %(id)s", {"id": shim})
        row = cur.fetchone()
    assert row["score"] is None


def test_on_event_brackets_the_call_with_evaluating_then_scored(
    four_way: dict[str, str],
) -> None:
    seen: list[tuple[str, str]] = []
    result = EvaluationResult(passed=45, total=48, score=0.9375, reason="ok")

    evaluate_branch(
        four_way["codemod"],
        "run-1",
        "a patch",
        evaluate=FakeEvaluator(result=result),
        on_event=lambda stage, message: seen.append((stage, message)),
    )

    assert [stage for stage, _ in seen] == ["evaluating", "scored"]
    # The score event has to carry the real number, not just announce that
    # scoring happened -- that's the entire value of this event to a viewer.
    assert "94%" in seen[1][1]
    assert "45/48" in seen[1][1]


def test_on_event_is_optional(four_way: dict[str, str]) -> None:
    """The default parameter this whole feature rests on: every existing
    caller that never heard of on_event must keep working unchanged."""
    result = evaluate_branch(
        four_way["shim"], "run-1", "a patch", evaluate=FakeEvaluator(score=0.5)
    )
    assert result.score == 0.5
