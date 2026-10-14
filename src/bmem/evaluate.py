"""Run a branch's candidate patch through the real test suite, in isolation.

The slow part -- upload, trigger CodeBuild, poll for a container to clone the
target repo, apply the patch, and run pytest -- happens entirely outside any
database transaction. Only the final ``UPDATE branches SET score`` goes
through :func:`bmem.db.run_transaction`: a build can take minutes (the CTFd
API test subset alone runs ~6 minutes single-threaded), and putting that wait
inside a retried closure would mean a transient SQLSTATE 40001 on the trivial
final write re-triggering an entire CodeBuild run instead of just retrying the
write. Same lesson as :mod:`bmem.ingest`'s batching, applied to duration
outside the database instead of duration inside a transaction.

Same swappable-backend shape as :mod:`bmem.plan` and :mod:`bmem.explore`, but
its own escape hatch (``BMEM_FAKE_CODEBUILD``, not ``BMEM_FAKE_PLANNER``):
whether Bedrock reasoning is real and whether CodeBuild evaluation is real are
independent questions, approved on different timelines, and a caller should be
able to flip one without the other.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from bmem.db import run_transaction

__all__ = [
    "CodeBuildEvaluator",
    "EvaluationResult",
    "Evaluator",
    "FakeEvaluator",
    "evaluate_branch",
    "get_evaluator",
]

# score_results.py is uploaded alongside each branch's patch because that is
# where infra/buildspec.yml's pre_build phase looks for it
# (`aws s3 cp "${ARTIFACT_S3}/score_results.py" ...`); changing that contract
# means changing the buildspec too, not just this path.
#
# Two candidate locations, checked in order, because the file has to be
# reachable from both layouts this code runs in. In a source checkout it
# lives at <repo>/infra/. Inside the deployed container the package is
# pip-installed into site-packages, where "three parents up" is the Python
# lib directory and infra/ was never copied -- so the Dockerfile also places
# a copy next to this module. Resolving to a single hardcoded path meant the
# hosted demo raised FileNotFoundError the first time a real evaluation ran.
def _find_score_results() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "score_results.py", here.parent.parent / "infra" / "score_results.py"):
        if candidate.exists():
            return candidate
    # Return the repo-checkout path anyway: the error a caller gets should
    # name where the file was expected, not where it happened to look last.
    return here.parent.parent / "infra" / "score_results.py"


_SCORE_RESULTS_PATH = _find_score_results()


def _find_buildspec() -> Path | None:
    """The repo's infra/buildspec.yml, when this code runs from a checkout.

    Sent as buildspecOverride on every build so the buildspec in the repo is
    the one that runs. Without it, the CodeBuild project keeps whatever copy
    infra/setup_aws.sh baked in at provisioning time, and a buildspec fix
    (such as installing the target repo's test dependencies) silently does
    nothing until someone re-provisions. None inside a deployed image that
    did not ship the file: the project's own buildspec is used, as before.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here / "buildspec.yml", here.parent.parent / "infra" / "buildspec.yml"):
        if candidate.exists():
            return candidate
    return None


_BUILDSPEC_PATH = _find_buildspec()


class EvaluationResult(BaseModel):
    passed: int
    total: int
    score: float
    reason: str
    # Set by CodeBuildEvaluator from values it already has locally (bucket,
    # run_id, branch_id), never from result.json -- score_results.py, which
    # writes that file from inside the throwaway build container, has no
    # reason to know its own S3 location. FakeEvaluator leaves this None:
    # there is no real object behind a fake run to download.
    patch_s3_uri: str | None = None
    # Unlike patch_s3_uri, this one *does* come from result.json -- captured
    # by buildspec.yml right after checkout (`git rev-parse HEAD`), because
    # only the container that actually cloned the repo knows which commit it
    # landed on when TARGET_REF is empty ("whatever the default branch is
    # right now"). The apply engine (bmem.apply) uses it to detect a
    # repository that has moved since this patch was scored.
    base_sha: str | None = None


class Evaluator(Protocol):
    def __call__(
        self,
        run_id: str,
        branch_id: str,
        patch: str,
        *,
        on_event: Callable[[str, str], None] | None = None,
    ) -> EvaluationResult: ...


class CodeBuildEvaluator:
    """Runs a branch's patch through infra/buildspec.yml in a real CodeBuild project.

    ``target_repo``/``target_ref``/``test_selector`` are constructor
    arguments, not call-time ones: they are the same for every branch in one
    migration run, matching how :class:`bmem.plan.ClaudePlanner` and
    :class:`bmem.explore.ClaudeExplorer` put their stable config in
    ``__init__`` and vary only the per-call arguments.
    """

    def __init__(
        self,
        *,
        target_repo: str,
        target_ref: str,
        test_selector: str = "",
        bucket: str | None = None,
        project: str | None = None,
        region: str | None = None,
        poll_interval: float = 10.0,
        timeout: float = 900.0,
        s3_client: Any = None,
        codebuild_client: Any = None,
    ) -> None:
        self.target_repo = target_repo
        self.target_ref = target_ref
        self.test_selector = test_selector
        self.bucket = bucket or os.environ["BMEM_S3_BUCKET"]
        self.project = project or os.environ["BMEM_CODEBUILD_PROJECT"]
        self.poll_interval = poll_interval
        self.timeout = timeout

        if s3_client is not None and codebuild_client is not None:
            self._s3 = s3_client
            self._codebuild = codebuild_client
        else:
            import boto3  # lazy: offline tests need no AWS SDK config

            region = region or os.environ.get("AWS_REGION", "us-east-1")
            self._s3 = s3_client or boto3.client("s3", region_name=region)
            self._codebuild = codebuild_client or boto3.client("codebuild", region_name=region)

    def __call__(
        self,
        run_id: str,
        branch_id: str,
        patch: str,
        *,
        on_event: Callable[[str, str], None] | None = None,
    ) -> EvaluationResult:
        def _emit(stage: str, message: str) -> None:
            if on_event:
                on_event(stage, message)

        prefix = f"runs/{run_id}/{branch_id}"

        _emit("uploading", "Uploading patch and scoring script to S3")
        self._s3.put_object(
            Bucket=self.bucket, Key=f"{prefix}/candidate.patch", Body=patch.encode()
        )
        self._s3.put_object(
            Bucket=self.bucket,
            Key=f"{prefix}/score_results.py",
            Body=_SCORE_RESULTS_PATH.read_bytes(),
        )

        overrides: dict[str, Any] = {}
        if _BUILDSPEC_PATH is not None:
            overrides["buildspecOverride"] = _BUILDSPEC_PATH.read_text()
        response = self._codebuild.start_build(
            projectName=self.project,
            **overrides,
            environmentVariablesOverride=[
                {"name": "RUN_ID", "value": run_id},
                {"name": "BRANCH_ID", "value": branch_id},
                {"name": "ARTIFACT_S3", "value": f"s3://{self.bucket}/{prefix}"},
                {"name": "TARGET_REPO", "value": self.target_repo},
                {"name": "TARGET_REF", "value": self.target_ref},
                {"name": "TEST_SELECTOR", "value": self.test_selector},
            ],
        )
        build_id = response["build"]["id"]
        _emit("building", f"CodeBuild build {build_id.rsplit(':', 1)[-1][:12]} started")
        status = self._wait_for_completion(build_id, on_event=_emit)

        # Fetch result.json regardless of build status. buildspec.yml's `||
        # true` guards mean most real failures (patch didn't apply, tests
        # failed) still produce a valid, low-scoring result.json rather than
        # a failed build -- a genuinely absent object is the actual error
        # case, and that raises on its own via the S3 client below.
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=f"{prefix}/result.json")
        except Exception as exc:  # noqa: BLE001 -- re-raised with real diagnostic context
            raise RuntimeError(
                f"CodeBuild build {build_id} for branch {branch_id} ended with status "
                f"{status!r} and produced no result.json: {exc}"
            ) from exc
        result = EvaluationResult.model_validate_json(obj["Body"].read())
        return result.model_copy(
            update={"patch_s3_uri": f"s3://{self.bucket}/{prefix}/candidate.patch"}
        )

    def _wait_for_completion(
        self, build_id: str, *, on_event: Callable[[str, str], None] | None = None
    ) -> str:
        # currentPhase walks SUBMITTED -> PROVISIONING -> DOWNLOAD_SOURCE ->
        # INSTALL -> PRE_BUILD -> BUILD -> POST_BUILD -> COMPLETED. Real,
        # already-returned-by-the-API progress that was simply never looked
        # at before; surfacing phase changes as they happen is the difference
        # between "waiting" and actually showing what CodeBuild is doing.
        deadline = time.monotonic() + self.timeout
        last_phase: str | None = None
        while True:
            response = self._codebuild.batch_get_builds(ids=[build_id])
            build = response["builds"][0]
            status = build["buildStatus"]
            phase = build.get("currentPhase")
            if phase and phase != last_phase and on_event:
                on_event("building", f"CodeBuild: {phase.replace('_', ' ').title()}")
            last_phase = phase
            if status != "IN_PROGRESS":
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"CodeBuild build {build_id} did not finish within {self.timeout}s"
                )
            time.sleep(self.poll_interval)


# Roughly CTFd's suite size, so a faked pass count reads like a real one.
_SPREAD_TOTAL = 320
# Band the spread scores land in. A real migration attempt rarely passes
# everything and rarely fails everything; 55%-99% is where the interesting
# comparisons live.
_SPREAD_FLOOR = 176  # 0.55
_SPREAD_CEILING = 317  # 0.990625


class FakeEvaluator:
    """Deterministic stand-in for CodeBuild, for tests and offline development.

    Two modes. By default every branch scores a flat 1.0, which is what tests
    want -- a fixed value they can assert against.

    ``spread=True`` instead derives a distinct score per branch. The flat mode
    is useless for looking at the system: four branches all scoring 100% render
    a competition in which nothing competed, so the UI ends up demonstrating
    the opposite of the point. Spread mode makes the tree read the way it will
    once CodeBuild is actually wired up, while staying a fake -- the scores
    come from the branch id, not from running anything.
    """

    def __init__(
        self,
        result: EvaluationResult | None = None,
        *,
        score: float = 1.0,
        spread: bool = False,
    ) -> None:
        self._fixed = result
        self._default_score = score
        self._spread = spread

    def __call__(
        self,
        run_id: str,
        branch_id: str,
        patch: str,
        *,
        on_event: Callable[[str, str], None] | None = None,
    ) -> EvaluationResult:
        # on_event accepted for Protocol compliance, unused: there is nothing
        # real happening here to narrate, and inventing fake progress messages
        # for an instant fake call would be exactly the kind of theater this
        # project has avoided everywhere else. evaluate_branch below still
        # brackets the call with its own real "evaluating"/"scored" events.
        if self._fixed is not None:
            return self._fixed
        if not self._spread:
            return EvaluationResult(passed=1, total=1, score=self._default_score, reason="ok")

        # hashlib, not hash(): str hashing is salted per process, so hash()
        # would hand the same branch a different score on every restart and the
        # word "deterministic" above would be a lie.
        digest = hashlib.sha256(branch_id.encode()).digest()
        span = _SPREAD_CEILING - _SPREAD_FLOOR + 1
        passed = _SPREAD_FLOOR + (int.from_bytes(digest[:4], "big") % span)
        failed = _SPREAD_TOTAL - passed
        return EvaluationResult(
            passed=passed,
            total=_SPREAD_TOTAL,
            score=passed / _SPREAD_TOTAL,
            reason="ok" if failed == 0 else f"{failed} failing",
        )


def get_evaluator(*, target_repo: str, target_ref: str, test_selector: str = "") -> Evaluator:
    """Return the real evaluator, or the fake one when ``BMEM_FAKE_CODEBUILD`` is set.

    A separate escape hatch from ``BMEM_FAKE_PLANNER``: real reasoning with a
    fake evaluator (or the reverse) is a real configuration a caller building
    against this project's actual account-access timeline needs, not a
    hypothetical one.
    """
    if os.environ.get("BMEM_FAKE_CODEBUILD"):
        # spread=True, because the caller reaching for this factory is running
        # the actual system. Tests construct FakeEvaluator directly when they
        # want a flat, assertable score.
        return FakeEvaluator(spread=True)
    return CodeBuildEvaluator(
        target_repo=target_repo, target_ref=target_ref, test_selector=test_selector
    )


def evaluate_branch(
    branch_id: str,
    run_id: str,
    patch: str,
    *,
    evaluate: Evaluator,
    on_event: Callable[[str, str], None] | None = None,
) -> EvaluationResult:
    """Run ``evaluate`` for this branch, then write its score. A complete,
    top-level operation, like :func:`bmem.explore.explore_branch` -- but note
    the slow evaluation call happens *before* :func:`bmem.db.run_transaction`
    is even entered, not inside its retried closure. See this module's
    docstring for why: a build takes minutes, and retrying minutes of work to
    retry a one-line UPDATE would be exactly the mistake ingest.py's original
    design made and had to be fixed away from.
    """
    if on_event:
        on_event("evaluating", "Submitting patch for evaluation")
    result = evaluate(run_id, branch_id, patch, on_event=on_event)

    def _write_score(cur: Any) -> None:
        cur.execute(
            "UPDATE branches SET score = %(score)s WHERE id = %(id)s",
            {"score": result.score, "id": branch_id},
        )

    run_transaction(_write_score)
    if on_event:
        on_event(
            "scored", f"{result.score:.0%} ({result.passed}/{result.total}) -- {result.reason}"
        )
    return result
