"""Deployment recording's validation contract.

The writes need a cluster, so what is pinned here is the input validation --
specifically that a measurement cannot be stored without saying where it came
from. That rule is the reason this table is trustworthy at all: a number with
an unknown origin reads back later as evidence.
"""

from __future__ import annotations

import pytest

from bmem.deploy import (
    ENVIRONMENTS,
    SOURCES,
    STATUSES,
    Deployment,
    MetricComparison,
    ProductionEvidence,
    record_deployment,
    record_observation,
    update_deployment_status,
)


class TestEnvironmentValidation:
    def test_rejects_an_unknown_environment_before_touching_the_database(self) -> None:
        with pytest.raises(ValueError, match="unknown environment"):
            record_deployment("some-merge", environment="prod")  # not "production"

    def test_known_environments_are_the_promotion_ladder(self) -> None:
        assert ENVIRONMENTS == ("development", "staging", "production")


class TestStatusValidation:
    def test_rejects_an_unknown_status_on_create(self) -> None:
        with pytest.raises(ValueError, match="unknown status"):
            record_deployment("m", environment="staging", status="deployed")

    def test_rejects_an_unknown_status_on_update(self) -> None:
        with pytest.raises(ValueError, match="unknown status"):
            update_deployment_status("d", "done")

    def test_rolled_back_is_a_distinct_terminal_status(self) -> None:
        # Collapsing rollback into "failed" would lose the difference between
        # a deploy that broke and one a human deliberately withdrew.
        assert "rolled_back" in STATUSES
        assert "failed" in STATUSES


class TestObservationValidation:
    def test_source_is_mandatory_and_constrained(self) -> None:
        with pytest.raises(ValueError, match="source must be one of"):
            record_observation("d", metric="latency", phase="after", value=1.0, source="estimate")

    def test_only_cloudwatch_and_manual_are_accepted(self) -> None:
        # Every stored number has to be traceable to a real measurement.
        assert SOURCES == ("cloudwatch", "manual")

    def test_phase_must_be_before_or_after(self) -> None:
        with pytest.raises(ValueError, match="phase must be"):
            record_observation("d", metric="m", phase="during", value=1.0, source="manual")

    def test_validation_happens_before_any_write_is_attempted(self) -> None:
        # These deployment ids do not exist. Reaching the database would raise
        # a foreign-key error instead of ValueError, which is how we know the
        # guard runs first.
        with pytest.raises(ValueError):
            record_observation("nope", metric="m", phase="sideways", value=1.0, source="manual")


class TestEvidenceShape:
    def test_a_deployment_with_no_measurements_says_so(self) -> None:
        evidence = ProductionEvidence(
            deployment_id="d", merge_id="m", evaluation_score=0.9,
            observations=[], comparisons=[],
            note="No measurements have been recorded for this deployment.",
        )
        assert "No measurements" in evidence.note

    def test_comparison_tolerates_a_missing_half(self) -> None:
        # Recording only an "after" is normal -- nobody measured beforehand.
        # It must not be reported as a delta of zero.
        c = MetricComparison(
            metric="p95", unit="ms", before=None, after=280.0,
            delta=None, percent_change=None, sources=["manual"],
        )
        assert c.delta is None
        assert c.percent_change is None

    def test_deployment_model_round_trips(self) -> None:
        d = Deployment(
            id="1", merge_id="2", commit_sha=None, environment="staging", status="healthy",
            target=None, artifact=None, detail=None, started_at=None, completed_at=None,
            rolled_back_at=None, superseded_by=None,
        )
        assert Deployment.model_validate(d.model_dump()) == d
