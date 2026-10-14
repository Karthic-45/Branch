"""Deployment records, rollback, and the production evidence that follows.

Deploying is a human-clicked action here, never policy-triggered. An agent
that can promote its own belief *and* ship it to production unattended is a
much larger safety claim than this project makes, and nothing in this module
fires a deployment on its own -- it records what a person chose to do, and
what happened afterwards.

The observation side is deliberately strict: every recorded metric carries a
mandatory ``source``, and there is no code path that invents one. A
deployment with no measurements reports "not measured", which is the honest
answer when nobody wired up CloudWatch.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel

from bmem.db import run_transaction, transaction

__all__ = [
    "Deployment",
    "DeploymentEnvironment",
    "MetricComparison",
    "Observation",
    "ProductionEvidence",
    "get_deployments",
    "get_production_evidence",
    "record_deployment",
    "record_observation",
    "rollback_deployment",
    "update_deployment_status",
]

ENVIRONMENTS = ("development", "staging", "production")
STATUSES = ("pending", "running", "healthy", "failed", "rolled_back")
SOURCES = ("cloudwatch", "manual")


class DeploymentEnvironment(BaseModel):
    name: str
    reached: bool
    latest_status: str | None
    latest_deployment_id: str | None


class Deployment(BaseModel):
    id: str
    merge_id: str
    commit_sha: str | None
    environment: str
    status: str
    target: str | None
    artifact: str | None
    detail: str | None
    started_at: str | None
    completed_at: str | None
    rolled_back_at: str | None
    superseded_by: str | None


class Observation(BaseModel):
    id: str
    metric: str
    phase: str
    value: float
    unit: str | None
    source: str
    note: str | None
    observed_at: str | None


class MetricComparison(BaseModel):
    metric: str
    unit: str | None
    before: float | None
    after: float | None
    delta: float | None
    percent_change: float | None
    sources: list[str]


class ProductionEvidence(BaseModel):
    deployment_id: str
    merge_id: str
    evaluation_score: float | None
    observations: list[Observation]
    comparisons: list[MetricComparison]
    note: str


def _row_to_deployment(r: dict[str, Any]) -> Deployment:
    return Deployment(
        id=str(r["id"]),
        merge_id=str(r["merge_id"]),
        commit_sha=r["commit_sha"],
        environment=r["environment"],
        status=r["status"],
        target=r["target"],
        artifact=r["artifact"],
        detail=r["detail"],
        started_at=r["started_at"].isoformat() if r["started_at"] else None,
        completed_at=r["completed_at"].isoformat() if r["completed_at"] else None,
        rolled_back_at=r["rolled_back_at"].isoformat() if r["rolled_back_at"] else None,
        superseded_by=str(r["superseded_by"]) if r["superseded_by"] else None,
    )


def record_deployment(
    merge_id: str,
    *,
    environment: str,
    commit_sha: str | None = None,
    target: str | None = None,
    artifact: str | None = None,
    detail: str | None = None,
    status: str = "pending",
) -> Deployment:
    """Record that someone deployed this decision. Does not perform a deploy.

    Shipping is left to ``infra/deploy_beanstalk.sh``, run deliberately by a
    person. This records the fact, so "which decision is live" has an answer
    that does not depend on someone remembering.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError(f"unknown environment {environment!r}; expected one of {ENVIRONMENTS}")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")

    def _write(cur: Any) -> dict[str, Any]:
        cur.execute(
            """
            INSERT INTO deployments
                (merge_id, commit_sha, environment, status, target, artifact, detail)
            VALUES (%(m)s, %(sha)s, %(env)s, %(status)s, %(target)s, %(artifact)s, %(detail)s)
            RETURNING *
            """,
            {
                "m": merge_id,
                "sha": commit_sha,
                "env": environment,
                "status": status,
                "target": target or os.environ.get("BMEM_BEANSTALK_ENV"),
                "artifact": artifact,
                "detail": detail,
            },
        )
        return dict(cur.fetchone())

    return _row_to_deployment(run_transaction(_write))


def update_deployment_status(
    deployment_id: str, status: str, *, detail: str | None = None
) -> Deployment:
    """Advance a deployment through pending -> running -> healthy/failed."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")

    def _write(cur: Any) -> dict[str, Any] | None:
        cur.execute(
            """
            UPDATE deployments
            SET status = %(s)s,
                detail = COALESCE(%(d)s, detail),
                completed_at = CASE
                    WHEN %(s)s IN ('healthy', 'failed', 'rolled_back') THEN now()
                    ELSE completed_at
                END
            WHERE id = %(id)s
            RETURNING *
            """,
            {"id": deployment_id, "s": status, "d": detail},
        )
        row = cur.fetchone()
        return dict(row) if row else None

    row = run_transaction(_write)
    if row is None:
        raise LookupError(f"no such deployment: {deployment_id}")
    return _row_to_deployment(row)


def rollback_deployment(deployment_id: str, *, reason: str) -> dict[str, Any]:
    """Mark a deployment rolled back and name the version it returns to.

    Identifies the previous healthy deployment in the same environment so the
    caller knows what they are going back to. This records the decision and
    the reason; performing the rollback is the operator's own deploy step,
    for the same reason deploying is.
    """

    def _write(cur: Any) -> dict[str, Any] | None:
        cur.execute("SELECT * FROM deployments WHERE id = %(id)s", {"id": deployment_id})
        current = cur.fetchone()
        if current is None:
            return None

        cur.execute(
            """
            SELECT * FROM deployments
            WHERE environment = %(env)s AND status = 'healthy' AND id != %(id)s
              AND started_at < %(started)s
            ORDER BY started_at DESC LIMIT 1
            """,
            {"env": current["environment"], "id": deployment_id, "started": current["started_at"]},
        )
        previous = cur.fetchone()

        cur.execute(
            """
            UPDATE deployments
            SET status = 'rolled_back', rolled_back_at = now(), completed_at = now(),
                detail = %(reason)s
            WHERE id = %(id)s
            RETURNING *
            """,
            {"id": deployment_id, "reason": reason},
        )
        rolled = dict(cur.fetchone())
        return {"rolled_back": rolled, "previous": dict(previous) if previous else None}

    result = run_transaction(_write)
    if result is None:
        raise LookupError(f"no such deployment: {deployment_id}")

    return {
        "rolled_back": _row_to_deployment(result["rolled_back"]).model_dump(),
        "previous_known_good": (
            _row_to_deployment(result["previous"]).model_dump() if result["previous"] else None
        ),
        "note": (
            "This records the rollback decision. Restoring the previous version is a "
            "deliberate operator step -- nothing here redeploys on its own."
            if result["previous"]
            else "No earlier healthy deployment exists in this environment to return to."
        ),
    }


def record_observation(
    deployment_id: str,
    *,
    metric: str,
    phase: str,
    value: float,
    source: str,
    unit: str | None = None,
    note: str | None = None,
) -> Observation:
    """Record a real measurement taken around a deployment.

    ``source`` is required and validated. A number whose origin is unknown is
    worse than no number: it reads back later as evidence.
    """
    if phase not in ("before", "after"):
        raise ValueError(f"phase must be 'before' or 'after', not {phase!r}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, not {source!r}")

    def _write(cur: Any) -> dict[str, Any]:
        cur.execute(
            """
            INSERT INTO production_observations
                (deployment_id, metric, phase, value, unit, source, note)
            VALUES (%(d)s, %(m)s, %(p)s, %(v)s, %(u)s, %(s)s, %(n)s)
            RETURNING *
            """,
            {
                "d": deployment_id, "m": metric, "p": phase, "v": value,
                "u": unit, "s": source, "n": note,
            },
        )
        return dict(cur.fetchone())

    r = run_transaction(_write)
    return Observation(
        id=str(r["id"]), metric=r["metric"], phase=r["phase"], value=r["value"],
        unit=r["unit"], source=r["source"], note=r["note"],
        observed_at=r["observed_at"].isoformat() if r["observed_at"] else None,
    )


def get_deployments(merge_id: str) -> list[Deployment]:
    with transaction() as cur:
        cur.execute(
            "SELECT * FROM deployments WHERE merge_id = %(m)s ORDER BY created_at DESC",
            {"m": merge_id},
        )
        return [_row_to_deployment(dict(r)) for r in cur.fetchall()]


class ProjectDeployment(BaseModel):
    """A deployment plus enough of its decision to be readable on its own."""

    deployment: Deployment
    winner_hypothesis: str | None
    winner_score: float | None
    merged_at: str | None


def list_project_deployments(root_id: str, limit: int = 100) -> list[ProjectDeployment]:
    """Every deployment made from any decision in this root's subtree.

    The per-decision view answers "where did this change get to"; this one
    answers the question an on-call engineer actually asks first -- "what went
    out, in what order, and is any of it currently rolled back".
    """
    with transaction() as cur:
        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT d.*, m.winner_hypothesis, m.winner_score, m.merged_at
            FROM deployments d
            JOIN (
                SELECT ml.id, b.hypothesis AS winner_hypothesis,
                       b.score AS winner_score, ml.merged_at
                FROM merge_log ml
                JOIN branches b ON b.id = ml.winner_id
                WHERE ml.parent_id IN (SELECT id FROM subtree)
            ) m ON m.id = d.merge_id
            ORDER BY d.created_at DESC
            LIMIT %(limit)s
            """,
            {"root": root_id, "limit": limit},
        )
        out: list[ProjectDeployment] = []
        for r in cur.fetchall():
            row = dict(r)
            merged_at = row.pop("merged_at", None)
            out.append(
                ProjectDeployment(
                    deployment=_row_to_deployment(row),
                    winner_hypothesis=row.get("winner_hypothesis"),
                    winner_score=row.get("winner_score"),
                    merged_at=merged_at.isoformat() if merged_at else None,
                )
            )
        return out


def environments_reached(merge_id: str) -> list[DeploymentEnvironment]:
    """Which environments this decision has reached, in promotion order."""
    with transaction() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (environment) environment, status, id
            FROM deployments WHERE merge_id = %(m)s
            ORDER BY environment, created_at DESC
            """,
            {"m": merge_id},
        )
        latest = {r["environment"]: r for r in cur.fetchall()}

    return [
        DeploymentEnvironment(
            name=env,
            reached=env in latest,
            latest_status=latest[env]["status"] if env in latest else None,
            latest_deployment_id=str(latest[env]["id"]) if env in latest else None,
        )
        for env in ENVIRONMENTS
    ]


def get_production_evidence(deployment_id: str) -> ProductionEvidence:
    """Before/after measurements for one deployment, alongside what the
    evaluation scored.

    The two are presented side by side and deliberately not combined into a
    single "prediction accuracy" number: the evaluation score is a test pass
    rate, and a production observation is a latency or error rate. Asserting
    that one predicted the other would be inventing a causal claim the data
    does not support.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT d.merge_id, b.score
            FROM deployments d
            JOIN merge_log m ON m.id = d.merge_id
            JOIN branches b ON b.id = m.winner_id
            WHERE d.id = %(id)s
            """,
            {"id": deployment_id},
        )
        head = cur.fetchone()
        if head is None:
            raise LookupError(f"no such deployment: {deployment_id}")

        cur.execute(
            """
            SELECT * FROM production_observations
            WHERE deployment_id = %(id)s ORDER BY metric, phase, observed_at
            """,
            {"id": deployment_id},
        )
        rows = [dict(r) for r in cur.fetchall()]

    observations = [
        Observation(
            id=str(r["id"]), metric=r["metric"], phase=r["phase"], value=r["value"],
            unit=r["unit"], source=r["source"], note=r["note"],
            observed_at=r["observed_at"].isoformat() if r["observed_at"] else None,
        )
        for r in rows
    ]

    by_metric: dict[str, dict[str, Any]] = {}
    for o in observations:
        entry = by_metric.setdefault(o.metric, {"unit": o.unit, "sources": set()})
        entry[o.phase] = o.value
        entry["sources"].add(o.source)

    comparisons: list[MetricComparison] = []
    for metric, entry in sorted(by_metric.items()):
        before, after = entry.get("before"), entry.get("after")
        delta = (after - before) if before is not None and after is not None else None
        comparisons.append(
            MetricComparison(
                metric=metric,
                unit=entry["unit"],
                before=before,
                after=after,
                delta=delta,
                percent_change=(delta / before * 100) if delta is not None and before else None,
                sources=sorted(entry["sources"]),
            )
        )

    return ProductionEvidence(
        deployment_id=deployment_id,
        merge_id=str(head["merge_id"]),
        evaluation_score=head["score"],
        observations=observations,
        comparisons=comparisons,
        note=(
            "The evaluation score is a test pass rate; the observations below are whatever "
            "was measured in the environment. They are shown side by side, not combined -- "
            "this system does not claim one predicted the other."
            if observations
            else "No measurements have been recorded for this deployment."
        ),
    )
