"""Project-level statistics, execution economics, and cluster health.

Everything here is derived from rows this system already writes, or read live
from CockroachDB's own introspection views. Nothing is estimated into
existence: where a number genuinely is not collected (per-call LLM tokens,
S3 request counts), the field is ``None`` and the UI renders "not measured"
rather than a plausible-looking figure.

Cost in particular is reported as *usage* -- build-minutes, embedding calls,
stored bytes -- with pricing applied only when the caller supplies rates via
BMEM_PRICE_* environment variables. AWS pricing varies by region, contract
and free-tier position; inventing a dollar figure for a hackathon demo would
be the exact kind of fabricated metric this project refuses elsewhere.
"""

from __future__ import annotations

import os

from pydantic import BaseModel

from bmem.db import follower_read, transaction

__all__ = [
    "ClusterStats",
    "CostReport",
    "ProjectStats",
    "SecurityReport",
    "cluster_stats",
    "cost_report",
    "project_stats",
    "security_report",
]


class ProjectStats(BaseModel):
    root_id: str
    # The repository this root was ingested from, so the UI can name it
    # without a second round trip. None for roots created before
    # self-service ingest existed -- rendered as "Not recorded", not guessed.
    source_repo: str | None
    source_ref: str | None
    branches: int
    branches_exploring: int
    branches_merged: int
    branches_abandoned: int
    decisions: int
    decisions_reverted: int
    decisions_tie_broken: int
    strategies_evaluated: int
    win_rate: float | None
    average_score: float | None
    best_score: float | None
    patch_available_rate: float | None
    applies_attempted: int
    applies_validated: int
    replays: int
    memory_rows: int


def project_stats(root_id: str) -> ProjectStats:
    """Aggregate everything recorded under one root.

    One round trip per table rather than a single wide join: these are
    independent aggregates over differently-shaped tables, and a join across
    all of them would fan out rows and need DISTINCT gymnastics to get the
    counts back right.
    """
    with follower_read() as cur:
        cur.execute(
            "SELECT source_repo, source_ref FROM branches WHERE id = %(root)s",
            {"root": root_id},
        )
        origin = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT
                count(*) FILTER (WHERE b.id != %(root)s)                    AS branches,
                count(*) FILTER (WHERE b.status = 'exploring'
                                   AND b.id != %(root)s)                    AS exploring,
                count(*) FILTER (WHERE b.status = 'merged')                 AS merged,
                count(*) FILTER (WHERE b.status = 'abandoned')              AS abandoned,
                avg(b.score) FILTER (WHERE b.score IS NOT NULL)             AS avg_score,
                max(b.score)                                                AS best_score
            FROM branches b WHERE b.id IN (SELECT id FROM subtree)
            """,
            {"root": root_id},
        )
        tree = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT
                count(*)                                        AS decisions,
                count(*) FILTER (WHERE m.reverted_at IS NOT NULL) AS reverted
            FROM merge_log m WHERE m.parent_id IN (SELECT id FROM subtree)
            """,
            {"root": root_id},
        )
        merges = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT
                count(*)                                          AS evaluated,
                count(*) FILTER (WHERE o.outcome = 'won')         AS won,
                count(*) FILTER (WHERE (o.patch_stats->>'files_changed')::INT > 0) AS with_patch
            FROM branch_outcomes o WHERE o.branch_id IN (SELECT id FROM subtree)
            """,
            {"root": root_id},
        )
        outcomes = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT count(*) AS rows FROM memory WHERE branch_id IN (SELECT id FROM subtree)
            """,
            {"root": root_id},
        )
        mem = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            -- count(a.id), not count(*): these are LEFT JOINs, and count(*)
            -- would count the null-filled row a merge with no application
            -- produces -- reporting an apply that never happened.
            SELECT
                count(a.id)                                                  AS attempted,
                count(*) FILTER (WHERE a.validation_status = 'passed')       AS validated,
                count(*) FILTER (WHERE r.tie_detected)                       AS tie_broken
            FROM merge_log m
            LEFT JOIN decision_applications a ON a.merge_id = m.id
            LEFT JOIN decision_rationales r ON r.merge_id = m.id
            WHERE m.parent_id IN (SELECT id FROM subtree)
            """,
            {"root": root_id},
        )
        applies = cur.fetchone() or {}

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT count(*) AS replays FROM execution_manifests
            WHERE branch_id IN (SELECT id FROM subtree) AND run_id LIKE 'replay-%%'
            """,
            {"root": root_id},
        )
        replays = cur.fetchone() or {}

    evaluated = outcomes.get("evaluated") or 0
    return ProjectStats(
        root_id=root_id,
        source_repo=origin.get("source_repo"),
        source_ref=origin.get("source_ref"),
        branches=tree.get("branches") or 0,
        branches_exploring=tree.get("exploring") or 0,
        branches_merged=tree.get("merged") or 0,
        branches_abandoned=tree.get("abandoned") or 0,
        decisions=merges.get("decisions") or 0,
        decisions_reverted=merges.get("reverted") or 0,
        decisions_tie_broken=applies.get("tie_broken") or 0,
        strategies_evaluated=evaluated,
        win_rate=((outcomes.get("won") or 0) / evaluated) if evaluated else None,
        average_score=float(tree["avg_score"]) if tree.get("avg_score") is not None else None,
        best_score=float(tree["best_score"]) if tree.get("best_score") is not None else None,
        patch_available_rate=(
            ((outcomes.get("with_patch") or 0) / evaluated) if evaluated else None
        ),
        applies_attempted=applies.get("attempted") or 0,
        applies_validated=applies.get("validated") or 0,
        replays=replays.get("replays") or 0,
        memory_rows=mem.get("rows") or 0,
    )


class CostLine(BaseModel):
    resource: str
    usage: str
    measured: bool
    estimated_usd: float | None
    note: str


class CostReport(BaseModel):
    root_id: str
    lines: list[CostLine]
    total_estimated_usd: float | None
    pricing_configured: bool


def _price(var: str) -> float | None:
    raw = os.environ.get(var)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def cost_report(root_id: str) -> CostReport:
    """Execution economics for one root, as measured usage first and dollars
    only where the operator has supplied a rate.

    Embedding calls are counted exactly (one memory row is one embed call).
    CodeBuild is counted in builds, not minutes: this project records when a
    build started and finished only inside the process that polled it, so
    durations are not durable and claiming a minute total would be inventing
    one.
    """
    with follower_read() as cur:
        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT
                (SELECT count(*) FROM memory WHERE branch_id IN (SELECT id FROM subtree))
                    AS embeddings,
                (SELECT count(*) FROM branch_outcomes
                  WHERE branch_id IN (SELECT id FROM subtree)) AS builds,
                (SELECT count(*) FROM execution_manifests
                  WHERE branch_id IN (SELECT id FROM subtree)) AS explorations,
                (SELECT count(DISTINCT run_id) FROM execution_manifests
                  WHERE branch_id IN (SELECT id FROM subtree)) AS runs
            """,
            {"root": root_id},
        )
        row = cur.fetchone() or {}

    embeddings = row.get("embeddings") or 0
    builds = row.get("builds") or 0
    explorations = row.get("explorations") or 0
    runs = row.get("runs") or 0

    price_embed = _price("BMEM_PRICE_PER_1K_EMBEDDINGS")
    price_build = _price("BMEM_PRICE_PER_BUILD")
    price_reason = _price("BMEM_PRICE_PER_REASONING_CALL")

    embed_cost = (embeddings / 1000 * price_embed) if price_embed is not None else None
    build_cost = (builds * price_build) if price_build is not None else None
    # One planning call per run, plus one exploration call per branch.
    reasoning_calls = runs + explorations
    reason_cost = (reasoning_calls * price_reason) if price_reason is not None else None

    lines = [
        CostLine(
            resource="Titan embeddings",
            usage=f"{embeddings} vectors",
            measured=True,
            estimated_usd=embed_cost,
            note="one embed call per stored memory row",
        ),
        CostLine(
            resource="CodeBuild evaluations",
            usage=f"{builds} builds",
            measured=True,
            estimated_usd=build_cost,
            note="build duration is not persisted, so minutes cannot be totalled",
        ),
        CostLine(
            resource="Bedrock reasoning",
            usage=f"{reasoning_calls} calls",
            measured=True,
            estimated_usd=reason_cost,
            note=f"{runs} planning + {explorations} exploration calls",
        ),
        CostLine(
            resource="S3 artifacts",
            usage="not measured",
            measured=False,
            estimated_usd=None,
            note="object sizes are not tracked by this application",
        ),
    ]

    priced = [c for c in (embed_cost, build_cost, reason_cost) if c is not None]
    return CostReport(
        root_id=root_id,
        lines=lines,
        total_estimated_usd=sum(priced) if priced else None,
        pricing_configured=bool(priced),
    )


class ClusterStats(BaseModel):
    branches_total: int
    memory_total: int
    merges_total: int
    manifests_total: int
    outcomes_total: int
    applications_total: int
    active_queries: int | None
    database: str | None
    vector_index_present: bool | None
    gc_ttl_seconds: int | None
    note: str


def cluster_stats() -> ClusterStats:
    """Live CockroachDB state behind the memory layer.

    The introspection reads (``crdb_internal.cluster_queries``, zone configs)
    are each wrapped individually: they need cluster privileges that a
    least-privilege application user may legitimately not have, and one
    permission error should degrade a dashboard panel to "unavailable", not
    fail the whole request.
    """
    active: int | None = None
    database: str | None = None
    vector_index: bool | None = None
    gc_ttl: int | None = None
    notes: list[str] = []

    with transaction() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM branches)               AS branches,
                (SELECT count(*) FROM memory)                 AS memory,
                (SELECT count(*) FROM merge_log)              AS merges,
                (SELECT count(*) FROM execution_manifests)    AS manifests,
                (SELECT count(*) FROM branch_outcomes)        AS outcomes,
                (SELECT count(*) FROM decision_applications)  AS applications
            """
        )
        counts = cur.fetchone() or {}

        try:
            cur.execute("SELECT current_database() AS db")
            database = (cur.fetchone() or {}).get("db")
        except Exception:  # noqa: BLE001 -- panel degrades, request does not fail
            notes.append("current_database unavailable")

    try:
        with transaction() as cur:
            cur.execute("SELECT count(*) AS n FROM crdb_internal.cluster_queries")
            active = (cur.fetchone() or {}).get("n")
    except Exception:  # noqa: BLE001 -- needs VIEWACTIVITY; not fatal
        notes.append("cluster_queries needs the VIEWACTIVITY privilege")

    try:
        with transaction() as cur:
            cur.execute(
                "SELECT index_name FROM [SHOW INDEXES FROM memory] "
                "WHERE index_name LIKE '%%embedding%%' LIMIT 1"
            )
            vector_index = cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        notes.append("index introspection unavailable")

    try:
        with transaction() as cur:
            cur.execute("SHOW ZONE CONFIGURATION FROM TABLE memory")
            zone = cur.fetchone() or {}
            raw = str(zone.get("raw_config_sql") or "")
            for line in raw.splitlines():
                if "gc.ttlseconds" in line:
                    gc_ttl = int("".join(ch for ch in line if ch.isdigit()))
                    break
    except Exception:  # noqa: BLE001
        notes.append("zone configuration unavailable")

    return ClusterStats(
        branches_total=counts.get("branches") or 0,
        memory_total=counts.get("memory") or 0,
        merges_total=counts.get("merges") or 0,
        manifests_total=counts.get("manifests") or 0,
        outcomes_total=counts.get("outcomes") or 0,
        applications_total=counts.get("applications") or 0,
        active_queries=active,
        database=database,
        vector_index_present=vector_index,
        gc_ttl_seconds=gc_ttl,
        note="; ".join(notes) if notes else "all introspection available",
    )


class SecurityCheck(BaseModel):
    name: str
    status: str  # pass | warn | unavailable
    detail: str
    verified_live: bool


class SecurityReport(BaseModel):
    checks: list[SecurityCheck]
    codebuild_env_vars: list[str]
    note: str


# Credential-shaped names. Presence of any of these in the evaluation
# sandbox's environment would mean model-generated code runs next to a
# secret, which is the single failure this architecture is shaped to prevent.
_SECRET_HINTS = (
    "PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "PRIVATE",
    "DATABASE_URL", "AWS_ACCESS_KEY", "AWS_SECRET", "ANTHROPIC_API_KEY",
)


def security_report() -> SecurityReport:
    """The project's security posture, checked live where checking is possible.

    The CodeBuild check is the load-bearing one and is a real API call, not a
    restatement of intent: it reads the evaluation project's actual
    environment configuration and reports which variables model-generated
    code can see. A claim that secrets are absent is only worth making if it
    is derived from the live configuration rather than from a comment.
    """
    from bmem.ingest import _HTTPS_URL_RE, _SAFE_REF_RE

    checks: list[SecurityCheck] = [
        SecurityCheck(
            name="Clone URL scheme allowlist",
            status="pass",
            detail=(
                f"Repository URLs must match {_HTTPS_URL_RE.pattern} -- https only. "
                "Rules out git's ext:: transport (arbitrary command execution), "
                "file:// (local disk read), and argument injection from a leading dash."
            ),
            verified_live=True,
        ),
        SecurityCheck(
            name="Git ref validation",
            status="pass",
            detail=(
                f"Refs must match {_SAFE_REF_RE.pattern}, so no shell metacharacters "
                "or --upload-pack style flags reach git."
            ),
            verified_live=True,
        ),
        SecurityCheck(
            name="No shell interpolation for git",
            status="pass",
            detail=(
                "Clone and apply run through subprocess argument lists, never a shell "
                "string, so a crafted URL cannot become a second command."
            ),
            verified_live=False,
        ),
        SecurityCheck(
            name="SQL time-travel injection guard",
            status="pass",
            detail=(
                "CockroachDB rejects placeholders in AS OF SYSTEM TIME, so every "
                "time-travel read goes through one validator (db.aost_literal) that "
                "accepts an unsigned decimal and nothing else. It is the only "
                "interpolation site in the codebase."
            ),
            verified_live=False,
        ),
        SecurityCheck(
            name="Audit surface is read-only",
            status="pass",
            detail=(
                "All nine bmem-audit MCP tools are annotated read_only_hint=True, and "
                "tests/test_mcp_audit.py fails the build if any tool's source contains "
                "a writing SQL statement or calls fork/merge/revert."
            ),
            verified_live=False,
        ),
    ]

    env_names: list[str] = []
    project = os.environ.get("BMEM_CODEBUILD_PROJECT")
    if not project:
        checks.append(
            SecurityCheck(
                name="Evaluation sandbox secret isolation",
                status="unavailable",
                detail="BMEM_CODEBUILD_PROJECT is not set, so the live project "
                "configuration could not be read.",
                verified_live=False,
            )
        )
    else:
        try:
            import boto3

            client = boto3.client(
                "codebuild", region_name=os.environ.get("AWS_REGION", "us-east-1")
            )
            projects = client.batch_get_projects(names=[project])["projects"]
            env = projects[0].get("environment", {}) if projects else {}
            env_names = sorted(v["name"] for v in env.get("environmentVariables", []))
            leaked = [n for n in env_names if any(h in n.upper() for h in _SECRET_HINTS)]
            checks.append(
                SecurityCheck(
                    name="Evaluation sandbox secret isolation",
                    status="pass" if not leaked else "warn",
                    detail=(
                        f"CodeBuild project '{project}' exposes "
                        f"{len(env_names)} environment variables to model-generated "
                        f"code, none credential-shaped."
                        if not leaked
                        else f"Credential-shaped variables present: {', '.join(leaked)}"
                    ),
                    verified_live=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- reported, not raised
            checks.append(
                SecurityCheck(
                    name="Evaluation sandbox secret isolation",
                    status="unavailable",
                    detail=f"Could not read the CodeBuild project configuration: {exc}",
                    verified_live=False,
                )
            )

    return SecurityReport(
        checks=checks,
        codebuild_env_vars=env_names,
        note=(
            "Per-build overrides supplied by bmem.evaluate add RUN_ID, BRANCH_ID, "
            "ARTIFACT_S3, TARGET_REPO, TARGET_REF and TEST_SELECTOR -- identifiers "
            "and a patch location, no credentials. The build role's S3 access is "
            "scoped to the artifact bucket."
        ),
    )
