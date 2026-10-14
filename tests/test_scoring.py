"""Scoring of a branch's test run.

This decides which hypothesis wins, so it is worth more than a smoke test. It
runs offline -- no cluster, no AWS.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from collections.abc import Callable

_spec = importlib.util.spec_from_file_location(
    "score_results",
    pathlib.Path(__file__).resolve().parent.parent / "infra" / "score_results.py",
)
assert _spec and _spec.loader
score_results = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_results)
score_junit = score_results.score_junit


def suite(tests: int, failures: int = 0, errors: int = 0, skipped: int = 0) -> str:
    return (
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}"/></testsuites>'
    )


class TestHappyPath:
    def test_all_passing_scores_one(self) -> None:
        assert score_junit(suite(320))["score"] == 1.0

    def test_partial_pass_rate(self) -> None:
        result = score_junit(suite(320, failures=6))
        assert result["passed"] == 314
        assert result["total"] == 320
        assert abs(result["score"] - 314 / 320) < 1e-9
        assert result["reason"] == "ok"

    def test_errors_count_against_the_branch_like_failures(self) -> None:
        # An import error means the strategy broke the codebase just as surely
        # as an assertion failure does.
        assert score_junit(suite(100, errors=25))["score"] == 0.75

    def test_failures_and_errors_are_summed(self) -> None:
        assert score_junit(suite(100, failures=10, errors=15))["score"] == 0.75

    def test_bare_testsuite_element_is_accepted(self) -> None:
        # Some pytest versions emit <testsuite> without the <testsuites> wrapper.
        xml = '<testsuite tests="10" failures="2" errors="0" skipped="0"/>'
        assert score_junit(xml)["score"] == 0.8

    def test_multiple_suites_are_aggregated(self) -> None:
        xml = (
            "<testsuites>"
            '<testsuite tests="50" failures="5" errors="0" skipped="0"/>'
            '<testsuite tests="50" failures="0" errors="5" skipped="0"/>'
            "</testsuites>"
        )
        result = score_junit(xml)
        assert result["total"] == 100
        assert result["passed"] == 90


class TestSkips:
    def test_skipped_tests_leave_the_denominator(self) -> None:
        """A branch must not be able to score well by skipping what it broke.

        Counting skips as passes would reward a strategy that disables the tests
        it cannot satisfy -- exactly the failure mode this project exists to
        rule out, since the whole point is that CI grades the agent.
        """
        result = score_junit(suite(100, failures=0, skipped=50))
        assert result["total"] == 50
        assert result["passed"] == 50
        assert result["score"] == 1.0

    def test_skipping_everything_scores_zero_rather_than_perfect(self) -> None:
        result = score_junit(suite(100, skipped=100))
        assert result["score"] == 0.0
        assert result["reason"] == "all_tests_skipped"

    def test_skips_and_failures_together(self) -> None:
        # 100 collected, 20 skipped, 10 failed -> 70/80
        result = score_junit(suite(100, failures=10, skipped=20))
        assert result["total"] == 80
        assert result["passed"] == 70


class TestDegradedInput:
    def test_unparseable_report_scores_zero_without_raising(self) -> None:
        result = score_junit("this is not xml")
        assert result["score"] == 0.0
        assert "unparseable_report" in result["reason"]

    def test_empty_report_scores_zero(self) -> None:
        result = score_junit("<testsuites></testsuites>")
        assert result["score"] == 0.0
        assert result["reason"] == "no_testsuite_in_report"

    def test_zero_tests_scores_zero(self) -> None:
        result = score_junit(suite(0))
        assert result["score"] == 0.0
        assert result["reason"] == "no_tests_ran"

    def test_missing_attributes_are_treated_as_zero(self) -> None:
        assert score_junit('<testsuite tests="10"/>')["score"] == 1.0

    def test_empty_string_attributes_do_not_crash(self) -> None:
        xml = '<testsuite tests="10" failures="" errors="" skipped=""/>'
        assert score_junit(xml)["score"] == 1.0


class TestBaseShaCli:
    """main()'s --base-sha plumbing -- the apply engine's base-changed check
    depends on this actually reaching result.json, on both the passing and the
    patch-did-not-apply path (buildspec.yml calls main() with --reason on the
    latter, and the commit is known before the patch is even attempted)."""

    def test_base_sha_is_recorded_alongside_a_normal_score(self, tmp_path: pathlib.Path) -> None:
        report = tmp_path / "results.xml"
        report.write_text(suite(10, failures=1))
        out = tmp_path / "result.json"
        argv = [
            "score_results.py", str(report), str(out), "--base-sha", "abc1234",
        ]
        monkeypatch_argv(argv, score_results.main)
        result = json.loads(out.read_text())
        assert result["base_sha"] == "abc1234"
        assert result["score"] == 0.9

    def test_base_sha_is_recorded_even_when_the_patch_did_not_apply(
        self, tmp_path: pathlib.Path
    ) -> None:
        out = tmp_path / "result.json"
        argv = [
            "score_results.py", "/nonexistent.xml", str(out),
            "--reason", "patch_did_not_apply", "--base-sha", "def5678",
        ]
        monkeypatch_argv(argv, score_results.main)
        result = json.loads(out.read_text())
        assert result["base_sha"] == "def5678"
        assert result["reason"] == "patch_did_not_apply"

    def test_base_sha_is_omitted_when_not_provided(self, tmp_path: pathlib.Path) -> None:
        report = tmp_path / "results.xml"
        report.write_text(suite(10))
        out = tmp_path / "result.json"
        argv = ["score_results.py", str(report), str(out)]
        monkeypatch_argv(argv, score_results.main)
        assert "base_sha" not in json.loads(out.read_text())


def monkeypatch_argv(argv: list[str], fn: Callable[[], int]) -> None:
    old = sys.argv
    sys.argv = argv
    try:
        fn()
    finally:
        sys.argv = old


def test_scores_are_comparable_across_branches() -> None:
    """The demo's four strategies must rank in the expected order."""
    scores = {
        "shim": score_junit(suite(320, failures=125))["score"],
        "rewrite": score_junit(suite(320, failures=179))["score"],
        "codemod": score_junit(suite(320, failures=6))["score"],
        "strangler": score_junit(suite(320, failures=86))["score"],
    }
    assert max(scores, key=lambda k: scores[k]) == "codemod"
    assert sorted(scores, key=lambda k: scores[k]) == [
        "rewrite",
        "shim",
        "strangler",
        "codemod",
    ]
