"""The evaluation sandbox must actually be able to run the target's tests.

Regression for the first real CodeBuild runs against CTFd: the buildspec
installed only requirements.txt, CTFd declares pytest solely in pyproject's
[dependency-groups] dev, so pytest was never installed and every branch
scored 0 with "no_report_produced" -- even a patch that only added an unused
file. No AWS needed here.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import yaml

import bmem.evaluate as evaluate_mod

INFRA = pathlib.Path(__file__).resolve().parent.parent / "infra"


def _buildspec_commands() -> str:
    spec = yaml.safe_load((INFRA / "buildspec.yml").read_text())
    return "\n".join(cmd for phase in spec["phases"].values() for cmd in phase.get("commands", []))


def test_buildspec_installs_test_dependencies_before_running_pytest() -> None:
    cmds = _buildspec_commands()
    assert "--group dev" in cmds
    assert 'python -c "import pytest" 2>/dev/null || pip install --quiet pytest' in cmds
    assert cmds.index("--group dev") < cmds.index("python -m pytest")


def test_buildspec_keeps_pytest_output_for_diagnosis() -> None:
    cmds = _buildspec_commands()
    assert "/tmp/pytest.log" in cmds
    assert 'pytest.log "${ARTIFACT_S3}/pytest.log"' in cmds


def test_missing_report_is_explained_from_pytest_output(tmp_path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text("/usr/bin/python: No module named pytest\n")
    out = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            str(INFRA / "score_results.py"),
            str(tmp_path / "missing.xml"),
            str(out),
            "--log",
            str(log),
        ],
        check=True,
        capture_output=True,
    )
    result = json.loads(out.read_text())
    assert result["score"] == 0.0
    assert result["reason"] == "no_report_produced: /usr/bin/python: No module named pytest"


def test_explain_missing_report_never_raises(tmp_path) -> None:
    spec = importlib.util.spec_from_file_location("score_results", INFRA / "score_results.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.explain_missing_report(tmp_path / "nope.log") == ""


class _S3:
    def put_object(self, **_):
        pass

    def get_object(self, **_):
        import io

        body = json.dumps({"passed": 1, "total": 1, "score": 1.0, "reason": "ok"}).encode()
        return {"Body": io.BytesIO(body)}


class _CodeBuild:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def start_build(self, **kwargs):
        self.kwargs = kwargs
        return {"build": {"id": "proj:abc123"}}

    def batch_get_builds(self, ids):
        return {"builds": [{"buildStatus": "SUCCEEDED", "currentPhase": "COMPLETED"}]}


def test_every_build_runs_the_repos_current_buildspec() -> None:
    cb = _CodeBuild()
    ev = evaluate_mod.CodeBuildEvaluator(
        target_repo="https://example.com/r.git",
        target_ref="master",
        bucket="b",
        project="p",
        s3_client=_S3(),
        codebuild_client=cb,
        poll_interval=0,
    )
    ev("run", "branch", "diff")
    assert cb.kwargs["buildspecOverride"] == (INFRA / "buildspec.yml").read_text()
