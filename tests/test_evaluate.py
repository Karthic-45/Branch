"""CodeBuild dispatch: a real boto3-shaped fake, no network call.

Runs offline -- no cluster, no AWS. Fake S3 and CodeBuild clients exercise the
actual upload/dispatch/poll/parse sequence CodeBuildEvaluator will run against
real AWS -- object keys, environment variables passed to start_build, and
result.json parsing -- the same "prove the wiring without the account" pattern
as test_plan.py and test_explore.py.

evaluate_branch()'s score write-back is covered separately in
tests/test_evaluate_branch.py, since that needs a live cluster.
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from bmem.evaluate import CodeBuildEvaluator, EvaluationResult, FakeEvaluator, get_evaluator


class TestFakeEvaluator:
    def test_default_result_is_a_perfect_score(self) -> None:
        result = FakeEvaluator()("run-1", "branch-1", "a patch")
        assert result.score == 1.0
        assert result.reason == "ok"

    def test_fixed_result_is_returned_verbatim(self) -> None:
        fixed = EvaluationResult(passed=3, total=10, score=0.3, reason="ok")
        assert FakeEvaluator(result=fixed)("run-1", "branch-1", "a patch") == fixed

    def test_custom_default_score(self) -> None:
        assert FakeEvaluator(score=0.5)("run-1", "branch-1", "p").score == 0.5


class TestFakeEvaluatorSpread:
    """spread=True is what the live UI runs on, so its promises are load-bearing."""

    def test_branches_get_different_scores(self) -> None:
        """A flat score renders a competition in which nothing competed."""
        spread = FakeEvaluator(spread=True)
        scores = {spread("run-1", f"branch-{i}", "p").score for i in range(8)}
        assert len(scores) > 1

    def test_same_branch_always_scores_the_same(self) -> None:
        spread = FakeEvaluator(spread=True)
        first = spread("run-1", "branch-a", "p")
        assert spread("run-2", "branch-a", "a different patch").score == first.score

    def test_scores_are_stable_across_processes(self) -> None:
        """The reason it hashes with hashlib rather than hash().

        str hashing is salted per interpreter, so hash() would hand the same
        branch a different score on every restart -- invisible in a single test
        run, and a rescored tree every time the server reloads.
        """
        expected = subprocess.run(
            [
                sys.executable,
                "-c",
                "from bmem.evaluate import FakeEvaluator;"
                "print(FakeEvaluator(spread=True)('r', 'branch-a', 'p').score)",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert float(expected) == FakeEvaluator(spread=True)("r", "branch-a", "p").score

    def test_scores_stay_inside_a_plausible_band(self) -> None:
        """A fake that emits 0% or 100% stops looking like a real test run."""
        spread = FakeEvaluator(spread=True)
        for i in range(200):
            result = spread("run-1", f"branch-{i}", "p")
            assert 0.5 < result.score < 1.0
            assert 0 < result.passed < result.total
            assert result.score == result.passed / result.total


class TestGetEvaluator:
    def test_fake_by_default_in_tests(self) -> None:
        # conftest.py's pytest_configure forces BMEM_FAKE_CODEBUILD=1 for the
        # whole suite, precisely so no test can silently trigger a build.
        evaluator = get_evaluator(target_repo="https://example.com/repo.git", target_ref="main")
        assert isinstance(evaluator, FakeEvaluator)

    def test_real_evaluator_when_the_escape_hatch_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BMEM_FAKE_CODEBUILD", raising=False)
        monkeypatch.setenv("BMEM_S3_BUCKET", "test-bucket")
        monkeypatch.setenv("BMEM_CODEBUILD_PROJECT", "test-project")

        # Real boto3.client() loads botocore's per-service model data at
        # construction time -- about 4s for s3 + codebuild together, measured.
        # Too slow to pay on every offline run for what this test actually
        # checks: that get_evaluator picks the real class and asks boto3 for
        # the right services, not a re-test of botocore's own construction.
        requested_services: list[str] = []
        monkeypatch.setattr(
            "boto3.client",
            lambda service_name, **kwargs: requested_services.append(service_name) or object(),
        )

        evaluator = get_evaluator(target_repo="https://example.com/repo.git", target_ref="main")

        assert isinstance(evaluator, CodeBuildEvaluator)
        assert requested_services == ["s3", "codebuild"]


class _FakeS3:
    """In-memory stand-in for boto3's S3 client -- just enough of put_object
    and get_object to prove CodeBuildEvaluator uploads and reads the right keys."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[f"{Bucket}/{Key}"] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        data = self.objects[f"{Bucket}/{Key}"]
        return {"Body": SimpleNamespace(read=lambda: data)}


class _FakeCodeBuild:
    """Stand-in for boto3's CodeBuild client. Always reports the given final
    status on the first poll -- these tests are about the request/response
    shape, not about exercising the poll loop's timing."""

    def __init__(self, *, final_status: str = "SUCCEEDED") -> None:
        self.final_status = final_status
        self.start_build_calls: list[dict[str, Any]] = []

    def start_build(self, **kwargs: Any) -> dict[str, Any]:
        self.start_build_calls.append(kwargs)
        return {"build": {"id": "fake-build-id"}}

    def batch_get_builds(self, *, ids: list[str]) -> dict[str, Any]:
        return {"builds": [{"buildStatus": self.final_status}]}


def _result_json(**kwargs: Any) -> bytes:
    defaults = {"passed": 1, "total": 1, "score": 1.0, "reason": "ok"}
    return json.dumps({**defaults, **kwargs}).encode()


class TestCodeBuildEvaluatorWiring:
    def _evaluator(self, **overrides: Any) -> tuple[CodeBuildEvaluator, _FakeS3, _FakeCodeBuild]:
        s3 = _FakeS3()
        codebuild = _FakeCodeBuild(final_status=overrides.pop("final_status", "SUCCEEDED"))
        evaluator = CodeBuildEvaluator(
            target_repo="https://github.com/CTFd/CTFd.git",
            target_ref="deadbeef",
            test_selector="tests/api/v1/",
            bucket="test-bucket",
            project="test-project",
            s3_client=s3,
            codebuild_client=codebuild,
            **overrides,
        )
        return evaluator, s3, codebuild

    def test_uploads_the_patch_and_score_results_before_starting_the_build(self) -> None:
        evaluator, s3, codebuild = self._evaluator()
        s3.objects["test-bucket/runs/run-1/branch-1/result.json"] = _result_json()

        evaluator("run-1", "branch-1", "--- a real patch ---")

        assert (
            s3.objects["test-bucket/runs/run-1/branch-1/candidate.patch"] == b"--- a real patch ---"
        )
        assert b"score_junit" in s3.objects["test-bucket/runs/run-1/branch-1/score_results.py"]

    def test_start_build_receives_the_contract_buildspec_expects(self) -> None:
        evaluator, s3, codebuild = self._evaluator()
        s3.objects["test-bucket/runs/run-1/branch-1/result.json"] = _result_json()

        evaluator("run-1", "branch-1", "a patch")

        assert len(codebuild.start_build_calls) == 1
        call = codebuild.start_build_calls[0]
        assert call["projectName"] == "test-project"
        env = {e["name"]: e["value"] for e in call["environmentVariablesOverride"]}
        assert env["RUN_ID"] == "run-1"
        assert env["BRANCH_ID"] == "branch-1"
        assert env["ARTIFACT_S3"] == "s3://test-bucket/runs/run-1/branch-1"
        assert env["TARGET_REPO"] == "https://github.com/CTFd/CTFd.git"
        assert env["TARGET_REF"] == "deadbeef"
        assert env["TEST_SELECTOR"] == "tests/api/v1/"

    def test_parses_the_real_result_json_shape(self) -> None:
        evaluator, s3, codebuild = self._evaluator()
        s3.objects["test-bucket/runs/run-1/branch-1/result.json"] = _result_json(
            passed=314, total=320, score=0.98125, reason="ok"
        )

        result = evaluator("run-1", "branch-1", "a patch")

        assert result.passed == 314
        assert result.total == 320
        assert result.score == pytest.approx(0.98125)

    def test_a_failed_build_with_no_result_json_raises_with_diagnostic_context(self) -> None:
        evaluator, s3, codebuild = self._evaluator(final_status="FAULT")
        # Deliberately leave result.json absent from the fake S3.

        with pytest.raises(RuntimeError, match="FAULT"):
            evaluator("run-1", "branch-1", "a patch")

    def test_polling_stops_as_soon_as_the_build_leaves_in_progress(self) -> None:
        evaluator, s3, codebuild = self._evaluator(final_status="FAILED")
        s3.objects["test-bucket/runs/run-1/branch-1/result.json"] = _result_json(
            score=0.0, reason="patch_did_not_apply"
        )

        # A build that failed to apply the patch is still a real, informative
        # result -- score 0.0, not an exception. This is the demo's actual
        # failure mode for a bad strategy, not an error state.
        result = evaluator("run-1", "branch-1", "a patch")
        assert result.score == 0.0
        assert result.reason == "patch_did_not_apply"
