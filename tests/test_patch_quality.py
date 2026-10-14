"""The three fixes from the first real CTFd migration review.

1. An empty patch is recorded as the agent declining (with its reason), and
   no evaluation build is spent on it.
2. Strategies are shown whole files from a checkout, chosen by search.
3. A patch that only adds unused new files cannot win a tie against one that
   changes existing code.

All offline: the database write and the explore step are stand-ins.
"""

from __future__ import annotations

from pathlib import Path

import bmem.orchestrate as orch
from bmem.evaluate import EvaluationResult
from bmem.explore import Exploration
from bmem.orchestrate import BranchOutcome, is_inert_patch, select_winner
from bmem.plan import Strategy
from bmem.source_context import SourceTree, build_context
from bmem.verdict import explain

NEW_FILE = (
    "diff --git a/asgi.py b/asgi.py\nnew file mode 100644\n--- /dev/null\n+++ b/asgi.py\n"
    "@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
)
EDIT = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-a = 1\n+a = 2\n"


def _outcome(name: str, score: float, patch: str) -> BranchOutcome:
    return BranchOutcome(
        branch_id=f"b-{name}",
        strategy=Strategy(name=name, hypothesis=f"h {name}"),
        exploration=Exploration(belief="b", patch=patch),
        evaluation=EvaluationResult(passed=int(score * 100), total=100, score=score, reason="ok"),
    )


# ── Fix 1 ────────────────────────────────────────────────────────────────────


def test_empty_patch_is_declined_without_a_build(monkeypatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(orch, "run_transaction", lambda fn, **_: writes.append("score=0"))
    monkeypatch.setattr(
        orch,
        "explore_branch",
        lambda *a, **k: Exploration(
            belief="The context does not include CTFd/__init__.py, so I cannot edit it safely.",
            patch="   \n",
        ),
    )

    def no_build(*_a, **_k):
        raise AssertionError("an empty patch must not be sent to CodeBuild")

    monkeypatch.setattr(orch, "evaluate_branch", no_build)
    monkeypatch.setattr(orch, "_record_evaluation_environment", no_build)
    events: list[tuple[str, str]] = []

    out = orch._explore_and_evaluate(
        "b1",
        Strategy(name="s", hypothesis="h"),
        "run1",
        explore=None,
        evaluate=None,
        embed=None,
        on_event=lambda stage, msg: events.append((stage, msg)),
    )

    assert out.evaluation.score == 0.0 and out.evaluation.total == 0
    assert out.evaluation.reason.startswith("agent_declined: The context does not include")
    assert writes == ["score=0"]
    assert events[-1][0] == "scored" and "declined" in events[-1][1]


def _row(bid: str, outcome: str, passed, total, reason="ok", files=1, created=0, run="r"):
    return {
        "branch_id": bid,
        "run_id": run,
        "strategy_name": f"s-{bid}",
        "outcome": outcome,
        "score": (passed / total) if total else 0.0,
        "passed": passed,
        "total": total,
        "reason": reason,
        "patch_stats": {
            "files_changed": files,
            "files_created": created,
            "lines_added": 10,
            "lines_removed": 0,
        },
    }


ROOT = "root"


def _branches(*ids_status):
    return [
        {"id": ROOT, "parent_id": None, "hypothesis": None, "status": "exploring", "score": None}
    ] + [
        {"id": i, "parent_id": ROOT, "hypothesis": f"h {i}", "status": s, "score": None}
        for i, s in ids_status
    ]


MERGE = [{"id": "m", "parent_id": ROOT, "winner_id": "w", "merged_at": None, "reverted_at": None}]


def test_verdict_quotes_the_agents_reason_when_it_declined() -> None:
    v = {
        x.branch_id: x
        for x in explain(
            _branches(("w", "merged"), ("d", "abandoned")),
            [
                _row("w", "won", 9, 9),
                _row("d", "lost", 0, 0, reason="agent_declined: CTFd/__init__.py was not shown"),
            ],
            MERGE,
            [
                {
                    "merge_id": "m",
                    "run_id": "r",
                    "tie_detected": False,
                    "margin": 1.0,
                    "rationale": "s-w scored 100%.",
                }
            ],
        )
    }
    assert v["d"].kind == "declined"
    assert 'declined to write a patch, saying: "CTFd/__init__.py was not shown"' in v["d"].headline


def test_old_runs_with_an_empty_patch_are_relabelled_using_the_belief() -> None:
    # Before fix 1 an empty diff was built and failed as patch_did_not_apply.
    v = {
        x.branch_id: x
        for x in explain(
            _branches(("w", "merged"), ("d", "abandoned")),
            [
                _row("w", "won", 9, 9),
                _row("d", "lost", 0, 0, reason="patch_did_not_apply", files=0),
            ],
            MERGE,
            [
                {
                    "merge_id": "m",
                    "run_id": "r",
                    "tie_detected": False,
                    "margin": 1.0,
                    "rationale": "s-w scored 100%.",
                }
            ],
            beliefs={"d": "The repository context only shows test files."},
        )
    }
    assert v["d"].kind == "declined"
    assert "only shows test files" in v["d"].headline


# ── Fix 2 ────────────────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> SourceTree:
    (tmp_path / "pkg" / "api").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(
        "from flask import Flask\n\ndef create_app():\n    return Flask(__name__)\n"
    )
    (tmp_path / "pkg" / "api" / "users.py").write_text("import x\n\ndef get_user():\n    pass\n")
    (tmp_path / "pkg" / "huge.py").write_text("x = 1\n" * 20_000)
    (tmp_path / "tests" / "test_users.py").write_text("def test_u():\n    pass\n")
    return SourceTree(tmp_path)


def test_hit_files_are_shown_whole_with_imports(tmp_path: Path) -> None:
    tree = _repo(tmp_path)
    plan = build_context(
        "migrate the users API",
        [("pkg/api/users.py::get_user", "# pkg/api/users.py\ndef get_user():\n    pass")],
        tree,
    )
    assert "pkg/api/users.py" in plan.full_files
    whole = next(b for b in plan.blocks if "FULL FILE: pkg/api/users.py" in b)
    assert "import x" in whole  # module-level lines a chunk would have missed


def test_entry_point_is_included_even_when_search_found_only_tests(tmp_path: Path) -> None:
    tree = _repo(tmp_path)
    plan = build_context(
        "wrap the app in an ASGI shim",
        [("tests/test_users.py::test_u", "def test_u(): pass")],
        tree,
    )
    assert plan.full_files[0] == "pkg/__init__.py"  # the app factory, ahead of the test
    assert "tests/test_users.py" in plan.full_files


def test_files_over_the_cap_fall_back_to_excerpts_and_are_named(tmp_path: Path) -> None:
    tree = _repo(tmp_path)
    plan = build_context("edit pkg/huge.py", [("pkg/huge.py::x", "x = 1")], tree)
    assert "pkg/huge.py" in plan.skipped_large
    assert "pkg/huge.py" not in plan.full_files
    assert any("EXCERPT (not the whole file): pkg/huge.py::x" in b for b in plan.blocks)
    assert "too large to include whole" in plan.blocks[0]


def test_budget_is_respected(tmp_path: Path) -> None:
    tree = _repo(tmp_path)
    plan = build_context("x", [("pkg/api/users.py::get_user", "c")], tree, budget=60)
    assert sum(len(tree.read(p) or "") for p in plan.full_files) <= 60


def test_without_a_checkout_the_excerpts_are_used_as_before() -> None:
    plan = build_context("x", [("a.py::f", "def f(): pass"), ("b.py::g", None)], None)
    assert plan.blocks == ["def f(): pass"] and plan.full_files == []


def test_reading_outside_the_checkout_is_refused(tmp_path: Path) -> None:
    tree = _repo(tmp_path)
    assert tree.read("../../etc/passwd") is None


# ── Fix 3 ────────────────────────────────────────────────────────────────────


def test_inert_patch_detection() -> None:
    assert is_inert_patch(NEW_FILE)
    assert not is_inert_patch(EDIT)
    assert not is_inert_patch(NEW_FILE + EDIT)
    assert not is_inert_patch("")


def test_tie_goes_to_the_patch_that_changes_existing_code() -> None:
    result = select_winner(
        [
            _outcome("adds-file", 1.0, NEW_FILE),  # smaller, but changes nothing
            _outcome("edits", 1.0, EDIT + EDIT.replace("app.py", "b.py")),
        ]
    )
    assert result.winner.strategy.name == "edits"
    assert result.discriminator == "changes_existing_code"
    assert "only added new files that nothing uses" in result.rationale


def test_when_every_tied_patch_is_inert_the_rationale_says_so() -> None:
    result = select_winner([_outcome("a", 1.0, NEW_FILE), _outcome("b", 1.0, NEW_FILE * 2)])
    assert result.winner.strategy.name == "a"
    assert "not that the goal was implemented" in result.rationale


def test_clear_inert_winner_is_flagged() -> None:
    result = select_winner([_outcome("a", 1.0, NEW_FILE), _outcome("b", 0.5, EDIT)])
    assert result.winner.strategy.name == "a"
    assert "changes no existing code" in result.rationale


def test_ties_between_real_edits_still_prefer_the_smaller_change() -> None:
    result = select_winner([_outcome("big", 1.0, EDIT * 3), _outcome("small", 1.0, EDIT)])
    assert result.winner.strategy.name == "small"
    assert result.discriminator == "smaller_patch"


def test_verdict_explains_an_inert_loser_and_cautions_on_an_inert_winner() -> None:
    v = {
        x.branch_id: x
        for x in explain(
            _branches(("w", "merged"), ("i", "abandoned")),
            [_row("w", "won", 9, 9, files=2), _row("i", "lost", 9, 9, files=1, created=1)],
            MERGE,
            [
                {
                    "merge_id": "m",
                    "run_id": "r",
                    "tie_detected": True,
                    "margin": 0.0,
                    "discriminator": "changes_existing_code",
                    "rationale": "tied.",
                }
            ],
        )
    }
    assert v["i"].kind == "lost_inert"
    assert "only adds new files" in v["i"].headline

    v2 = {
        x.branch_id: x
        for x in explain(
            _branches(("w", "merged")),
            [_row("w", "won", 9, 9, files=1, created=1)],
            MERGE,
            [
                {
                    "merge_id": "m",
                    "run_id": "r",
                    "tie_detected": False,
                    "margin": None,
                    "rationale": "s-w was the only branch evaluated.",
                }
            ],
        )
    }
    assert "Caution: its patch only adds new files" in v2["w"].headline
