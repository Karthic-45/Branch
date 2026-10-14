"""Merge / abandon verdicts are built only from recorded rows.

Each case mirrors a path through orchestrate.select_winner or merge/revert,
and checks the verdict names the deciding measurement rather than a generic
"lost".
"""

from __future__ import annotations

from datetime import UTC, datetime

from bmem.verdict import explain

ROOT, PARENT = "r" * 8, "r" * 8


def _b(bid: str, status: str, parent: str | None = ROOT) -> dict:
    return {
        "id": bid,
        "parent_id": parent,
        "hypothesis": f"hyp {bid}",
        "status": status,
        "score": None,
    }


def _o(
    bid: str,
    outcome: str,
    passed: int | None,
    total: int | None,
    reason: str = "ok",
    files: int = 3,
    lines: int = 40,
    run: str = "run1",
) -> dict:
    return {
        "branch_id": bid,
        "run_id": run,
        "strategy_name": f"s-{bid}",
        "outcome": outcome,
        "score": (passed / total) if total else 0.0,
        "passed": passed,
        "total": total,
        "reason": reason,
        "patch_stats": {"files_changed": files, "lines_added": lines, "lines_removed": 0},
    }


T1 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 14, 11, 0, tzinfo=UTC)


def _by_id(verdicts):
    return {v.branch_id: v for v in verdicts}


def test_clear_win_and_score_loss() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("w", "merged"), _b("l", "abandoned")],
            [_o("w", "won", 358, 358), _o("l", "lost", 300, 358)],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "w",
                    "merged_at": T1,
                    "reverted_at": None,
                }
            ],
            [
                {
                    "merge_id": "m1",
                    "run_id": "run1",
                    "margin": 0.162,
                    "tie_detected": False,
                    "rationale": "s-w scored 100.0%, 16.2% ahead of s-l at 83.8%.",
                }
            ],
        )
    )
    assert ROOT not in v
    assert v["w"].decision == "merged" and v["w"].kind == "merged"
    assert v["w"].headline.startswith("Merged because s-w scored 100.0%")
    assert v["l"].kind == "lost_on_score"
    assert "300/358" in v["l"].headline and "16.2% behind s-w" in v["l"].headline


def test_tie_break_names_both_patch_sizes() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("w", "merged"), _b("t", "abandoned")],
            [
                _o("w", "won", 358, 358, files=2, lines=30),
                _o("t", "lost", 358, 358, files=9, lines=400),
            ],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "w",
                    "merged_at": T1,
                    "reverted_at": None,
                }
            ],
            [
                {
                    "merge_id": "m1",
                    "run_id": "run1",
                    "margin": 0.0,
                    "tie_detected": True,
                    "rationale": "2 strategies tied; s-w was chosen for the smallest change.",
                }
            ],
        )
    )
    t = v["t"]
    assert t.kind == "lost_tie_break"
    assert "2 files, 30 lines" in t.headline and "9 files, 400 lines" in t.headline


def test_failure_reason_is_stated_in_plain_words() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("w", "merged"), _b("f", "abandoned")],
            [_o("w", "won", 358, 358), _o("f", "lost", None, None, reason="patch_did_not_apply")],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "w",
                    "merged_at": T1,
                    "reverted_at": None,
                }
            ],
            [
                {
                    "merge_id": "m1",
                    "run_id": "run1",
                    "margin": 1.0,
                    "tie_detected": False,
                    "rationale": "s-w scored 100.0%.",
                }
            ],
        )
    )
    assert v["f"].kind == "failed"
    assert "did not apply cleanly" in v["f"].headline


def test_failing_tests_count_as_a_score_loss_not_a_failure() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("w", "merged"), _b("l", "abandoned")],
            [_o("w", "won", 10, 10), _o("l", "lost", 7, 10, reason="3 failing")],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "w",
                    "merged_at": T1,
                    "reverted_at": None,
                }
            ],
            [
                {
                    "merge_id": "m1",
                    "run_id": "run1",
                    "margin": 0.3,
                    "tie_detected": False,
                    "rationale": "s-w scored 100.0%.",
                }
            ],
        )
    )
    assert v["l"].kind == "lost_on_score"


def test_unscored_branch_names_the_merged_sibling() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("w", "merged"), _b("u", "abandoned")],
            [_o("w", "won", 5, 5)],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "w",
                    "merged_at": T1,
                    "reverted_at": None,
                }
            ],
            [],
        )
    )
    assert v["u"].kind == "abandoned_unscored"
    assert "s-w was merged into the same parent" in v["u"].headline


def test_unscored_branch_without_any_merge_says_the_run_stopped() -> None:
    v = _by_id(explain([_b(ROOT, "exploring", None), _b("u", "abandoned")], [], [], []))
    assert "run stopped before this branch was scored" in v["u"].headline


def test_reverted_winner_is_abandoned_and_names_replacement() -> None:
    v = _by_id(
        explain(
            [_b(ROOT, "exploring", None), _b("a", "abandoned"), _b("b", "merged")],
            [_o("a", "won", 5, 5), _o("b", "won", 5, 5, run="run2")],
            [
                {
                    "id": "m1",
                    "parent_id": ROOT,
                    "winner_id": "a",
                    "merged_at": T1,
                    "reverted_at": T2,
                },
                {
                    "id": "m2",
                    "parent_id": ROOT,
                    "winner_id": "b",
                    "merged_at": T2,
                    "reverted_at": None,
                },
            ],
            [
                {
                    "merge_id": "m1",
                    "run_id": "run1",
                    "margin": None,
                    "tie_detected": False,
                    "rationale": "s-a was the only branch evaluated, scoring 100.0%.",
                }
            ],
        )
    )
    assert v["a"].kind == "reverted" and v["a"].decision == "abandoned"
    assert "reverted" in v["a"].headline and "s-b was merged in its place" in v["a"].headline
    assert v["b"].decision == "merged"
    assert "no decision rationale was recorded" in v["b"].headline.lower()


def test_exploring_branch_has_no_verdict_yet() -> None:
    v = _by_id(explain([_b(ROOT, "exploring", None), _b("x", "exploring")], [], [], []))
    assert v["x"].decision == "pending"
