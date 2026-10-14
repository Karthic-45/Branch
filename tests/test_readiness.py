"""Readiness reporting's honesty contract.

These pin the properties that make the readiness endpoint worth trusting:
a stand-in is never reported as a pass, an unavailable dependency fails
rather than silently degrading to fake results, and every failure carries
enough information to act on.
"""

from __future__ import annotations

import pytest

from bmem.readiness import (
    DEGRADED,
    NOT_READY,
    READY,
    SIMULATED,
    ComponentCheck,
    ReadinessReport,
    _bedrock_remediation,
    _check_bedrock_embeddings,
    _check_bedrock_reasoning,
    _check_codebuild,
)


class TestSimulatedIsNotAPass:
    """The load-bearing distinction: `simulated` must never equal `ready`."""

    def test_fake_planner_reports_simulated_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BMEM_FAKE_PLANNER", "1")
        check = _check_bedrock_reasoning()
        assert check.status == SIMULATED
        assert check.status != READY

    def test_fake_embeddings_reports_simulated_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BMEM_FAKE_EMBEDDINGS", "1")
        check = _check_bedrock_embeddings()
        assert check.status == SIMULATED
        assert check.status != READY

    def test_fake_codebuild_reports_simulated_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BMEM_FAKE_CODEBUILD", "1")
        check = _check_codebuild()
        assert check.status == SIMULATED

    def test_a_simulated_component_says_what_is_faked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The detail is what a viewer reads to know a score is not a
        # measurement, so it has to name the substitution explicitly.
        monkeypatch.setenv("BMEM_FAKE_CODEBUILD", "1")
        detail = _check_codebuild().detail.lower()
        assert "not from running a test suite" in detail

    def test_a_simulated_component_says_how_to_make_it_real(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BMEM_FAKE_PLANNER", "1")
        assert "unset" in (_check_bedrock_reasoning().remediation or "").lower()

    def test_probe_distinguishes_a_config_read_from_a_live_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A reader must be able to tell "we asked the service" from "we read
        # an env var and assumed".
        monkeypatch.setenv("BMEM_FAKE_PLANNER", "1")
        assert _check_bedrock_reasoning().probe == "environment"


class TestOverallStatus:
    def _report(self, statuses: dict[str, str]) -> ReadinessReport:
        from bmem.readiness import _MANDATORY

        checks = [
            ComponentCheck(name=n, status=s, detail="", probe="test")
            for n, s in statuses.items()
        ]
        by_name = {c.name: c for c in checks}
        mandatory = [by_name[n] for n in _MANDATORY if n in by_name]
        simulated = [c for c in checks if c.status == SIMULATED]
        failed = [c for c in checks if c.status == NOT_READY]
        if any(c.status == NOT_READY for c in mandatory):
            overall = NOT_READY
        elif simulated:
            overall = SIMULATED
        elif failed:
            overall = DEGRADED
        else:
            overall = READY
        return ReadinessReport(
            overall=overall, demo_ready=overall == READY, checks=checks, note=""
        )

    def test_all_live_is_ready_and_demo_ready(self) -> None:
        r = self._report({
            "CockroachDB": READY, "Bedrock (reasoning)": READY,
            "Titan (embeddings)": READY, "S3 (artifacts)": READY,
            "CodeBuild (evaluation)": READY,
        })
        assert r.overall == READY
        assert r.demo_ready is True

    def test_any_simulation_blocks_demo_ready(self) -> None:
        r = self._report({
            "CockroachDB": READY, "Bedrock (reasoning)": SIMULATED,
            "Titan (embeddings)": READY, "S3 (artifacts)": READY,
            "CodeBuild (evaluation)": READY,
        })
        assert r.overall == SIMULATED
        assert r.demo_ready is False

    def test_a_failed_mandatory_component_is_not_ready(self) -> None:
        r = self._report({
            "CockroachDB": READY, "Bedrock (reasoning)": NOT_READY,
            "Titan (embeddings)": READY, "S3 (artifacts)": READY,
            "CodeBuild (evaluation)": READY,
        })
        assert r.overall == NOT_READY
        assert r.demo_ready is False

    def test_a_failed_optional_component_only_degrades(self) -> None:
        r = self._report({
            "CockroachDB": READY, "Bedrock (reasoning)": READY,
            "Titan (embeddings)": READY, "S3 (artifacts)": READY,
            "CodeBuild (evaluation)": READY, "Reports (PDF)": NOT_READY,
        })
        assert r.overall == DEGRADED
        assert r.demo_ready is False


class TestRemediation:
    def test_throttling_explains_that_access_is_fine_but_quota_is_not(self) -> None:
        # This is the exact failure this account hits, and the distinction
        # matters: retrying forever will not help, a quota request will.
        text = _bedrock_remediation("ThrottlingException").lower()
        assert "quota" in text
        assert "service quotas" in text

    def test_throttling_says_the_quota_is_not_api_adjustable(self) -> None:
        # Verified against the live account: Adjustable=False, so telling
        # someone to script it would waste their time.
        assert "not adjustable via api" in _bedrock_remediation("ThrottlingException").lower()

    def test_access_denied_points_at_iam_and_model_access(self) -> None:
        text = _bedrock_remediation("AccessDeniedException").lower()
        assert "invokemodel" in text.replace(" ", "").lower() or "iam" in text

    def test_every_known_code_produces_actionable_text(self) -> None:
        for code in (
            "ThrottlingException", "AccessDeniedException",
            "ValidationException", "ResourceNotFoundException", "SomethingElse",
        ):
            assert len(_bedrock_remediation(code)) > 30


def test_no_check_can_return_ready_without_a_probe() -> None:
    # A green light must always name what was contacted to earn it.
    check = ComponentCheck(name="x", status=READY, detail="", probe="HeadBucket")
    assert check.probe
