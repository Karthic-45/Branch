"""Whether this deployment can actually do what it claims.

Every check here performs a real operation against the real dependency: a
query, an API call, a model invocation. None of them infer readiness from
configuration -- an environment variable being set says nothing about whether
the service behind it will answer.

The design rule this module exists to enforce: **a dependency that is not
available must fail readiness, not silently fall back to a stand-in.** A
dashboard that shows green while the reasoning backend is unreachable is
worse than one that shows nothing, because it converts a fixable outage into
a false claim about what the system did.

That is also why ``simulated`` is a distinct state from ``ready``. Running
with BMEM_FAKE_* set is legitimate for development, but the UI must never
present a faked score as a measured one -- so the check reports the fake
explicitly rather than passing.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import BaseModel

__all__ = ["ComponentCheck", "ReadinessReport", "readiness_report"]

# The states a dependency can be in. "simulated" is deliberately not a
# success: it means the component answered, but with a stand-in.
READY = "ready"
DEGRADED = "degraded"
NOT_READY = "not_ready"
SIMULATED = "simulated"


class ComponentCheck(BaseModel):
    name: str
    status: str
    detail: str
    remediation: str | None = None
    latency_ms: float | None = None
    # What was actually contacted, so a reader can tell a live check from a
    # configuration read.
    probe: str


class ReadinessReport(BaseModel):
    overall: str
    demo_ready: bool
    checks: list[ComponentCheck]
    note: str


def _timed(fn: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = fn()
    return result, round((time.perf_counter() - started) * 1000, 1)


def _check_cockroach() -> ComponentCheck:
    from bmem.db import transaction

    try:
        def _q() -> Any:
            with transaction() as cur:
                cur.execute("SELECT count(*) AS n FROM branches")
                return cur.fetchone()["n"]

        n, ms = _timed(_q)
        return ComponentCheck(
            name="CockroachDB",
            status=READY,
            detail=f"Connected; {n} branches visible.",
            latency_ms=ms,
            probe="SELECT against branches",
        )
    except Exception as exc:  # noqa: BLE001 -- reported, never raised
        return ComponentCheck(
            name="CockroachDB",
            status=NOT_READY,
            detail=f"{type(exc).__name__}: {exc}"[:300],
            remediation="Check BMEM_DATABASE_URL and that the cluster is reachable.",
            probe="SELECT against branches",
        )


def _check_bedrock_reasoning() -> ComponentCheck:
    if os.environ.get("BMEM_FAKE_PLANNER"):
        return ComponentCheck(
            name="Bedrock (reasoning)",
            status=SIMULATED,
            detail=(
                "BMEM_FAKE_PLANNER is set: strategies and beliefs come from a "
                "deterministic stand-in, not from a model."
            ),
            remediation="Unset BMEM_FAKE_PLANNER to use the real model.",
            probe="environment",
        )

    provider = os.environ.get("BMEM_REASONING_PROVIDER", "bedrock")
    if provider == "openai":
        return _check_openai_reasoning()

    model_id = os.environ.get("BMEM_REASONING_MODEL", "us.anthropic.claude-sonnet-4-6")
    try:
        import boto3

        client = boto3.client(
            "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

        def _invoke() -> Any:
            # One token. The point is whether the account may invoke at all,
            # and a readiness probe should not spend meaningful quota.
            return client.invoke_model(
                modelId=model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "."}],
                }),
            )

        _, ms = _timed(_invoke)
        return ComponentCheck(
            name="Bedrock (reasoning)",
            status=READY,
            detail=f"{model_id} responded.",
            latency_ms=ms,
            probe="InvokeModel",
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
        return ComponentCheck(
            name="Bedrock (reasoning)",
            status=NOT_READY,
            detail=f"{code}: {message}"[:300],
            remediation=_bedrock_remediation(code),
            probe="InvokeModel",
        )


def _check_bedrock_embeddings() -> ComponentCheck:
    if os.environ.get("BMEM_FAKE_EMBEDDINGS"):
        return ComponentCheck(
            name="Titan (embeddings)",
            status=SIMULATED,
            detail=(
                "BMEM_FAKE_EMBEDDINGS is set: vectors are deterministic but carry "
                "no semantics, so retrieval scoping is real and retrieval quality is not."
            ),
            remediation="Unset BMEM_FAKE_EMBEDDINGS to use the real model.",
            probe="environment",
        )

    if os.environ.get("BMEM_EMBEDDING_PROVIDER", "titan") == "openai":
        return _check_openai_embeddings()

    model_id = os.environ.get("BMEM_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    try:
        import boto3

        client = boto3.client(
            "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

        def _invoke() -> int:
            r = client.invoke_model(
                modelId=model_id,
                body=json.dumps({"inputText": "readiness", "dimensions": 1024, "normalize": True}),
            )
            return len(json.loads(r["body"].read())["embedding"])

        dims, ms = _timed(_invoke)
        # A wrong dimension count is a real failure: the schema declares
        # VECTOR(1024) and the index is built for it.
        if dims != 1024:
            return ComponentCheck(
                name="Titan (embeddings)",
                status=NOT_READY,
                detail=f"{model_id} returned {dims} dimensions; the schema requires 1024.",
                remediation="Set BMEM_EMBEDDING_MODEL to a model producing 1024 dimensions.",
                latency_ms=ms,
                probe="InvokeModel",
            )
        return ComponentCheck(
            name="Titan (embeddings)",
            status=READY,
            detail=f"{model_id} returned {dims} dimensions.",
            latency_ms=ms,
            probe="InvokeModel",
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
        return ComponentCheck(
            name="Titan (embeddings)",
            status=NOT_READY,
            detail=f"{code}: {message}"[:300],
            remediation=_bedrock_remediation(code),
            probe="InvokeModel",
        )



def _openai_remediation(exc: Exception) -> str:
    text = str(exc).lower()
    if "api key" in text or "authentication" in text or "401" in text:
        return "Set OPENAI_API_KEY. The provider is selected but no usable key is configured."
    if "rate limit" in text or "429" in text:
        return "The OpenAI account is rate limited or out of credit; check billing and usage."
    if "model" in text and ("not found" in text or "does not exist" in text):
        return "The configured model is unavailable to this account; check BMEM_REASONING_MODEL."
    return "Check OPENAI_API_KEY, account credit, and the configured model id."


def _check_openai_reasoning() -> ComponentCheck:
    model_id = os.environ.get("BMEM_REASONING_MODEL", "gpt-4o")
    name = "OpenAI (reasoning)"
    if not os.environ.get("OPENAI_API_KEY"):
        return ComponentCheck(
            name=name,
            status=NOT_READY,
            detail="BMEM_REASONING_PROVIDER=openai but OPENAI_API_KEY is not set.",
            remediation="Export OPENAI_API_KEY, or set BMEM_REASONING_PROVIDER=bedrock.",
            probe="environment",
        )
    try:
        from openai import OpenAI

        client = OpenAI()

        def _invoke() -> Any:
            # One token: enough to prove the key and model work without
            # spending meaningful credit on a health check.
            return client.chat.completions.create(
                model=model_id,
                max_tokens=1,
                messages=[{"role": "user", "content": "."}],
            )

        _, ms = _timed(_invoke)
        return ComponentCheck(
            name=name,
            status=READY,
            detail=f"{model_id} responded.",
            latency_ms=ms,
            probe="chat.completions.create",
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentCheck(
            name=name,
            status=NOT_READY,
            detail=f"{type(exc).__name__}: {exc}"[:300],
            remediation=_openai_remediation(exc),
            probe="chat.completions.create",
        )


def _check_openai_embeddings() -> ComponentCheck:
    model_id = os.environ.get("BMEM_EMBEDDING_MODEL_OPENAI", "text-embedding-3-small")
    name = "OpenAI (embeddings)"
    if not os.environ.get("OPENAI_API_KEY"):
        return ComponentCheck(
            name=name,
            status=NOT_READY,
            detail="BMEM_EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set.",
            remediation="Export OPENAI_API_KEY, or set BMEM_EMBEDDING_PROVIDER=titan.",
            probe="environment",
        )
    try:
        from openai import OpenAI

        client = OpenAI()

        def _invoke() -> int:
            r = client.embeddings.create(
                model=model_id, input=["readiness"], dimensions=1024
            )
            return len(r.data[0].embedding)

        dims, ms = _timed(_invoke)
        if dims != 1024:
            return ComponentCheck(
                name=name,
                status=NOT_READY,
                detail=f"{model_id} returned {dims} dimensions; the schema requires 1024.",
                remediation="Use an embedding model that supports 1024-dimension output.",
                latency_ms=ms,
                probe="embeddings.create",
            )
        return ComponentCheck(
            name=name,
            status=READY,
            detail=f"{model_id} returned {dims} dimensions.",
            latency_ms=ms,
            probe="embeddings.create",
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentCheck(
            name=name,
            status=NOT_READY,
            detail=f"{type(exc).__name__}: {exc}"[:300],
            remediation=_openai_remediation(exc),
            probe="embeddings.create",
        )


def _bedrock_remediation(code: str) -> str:
    if code == "ThrottlingException":
        return (
            "Model access is granted but the account's Bedrock quota is exhausted or zero. "
            "Request an increase in the Service Quotas console for Amazon Bedrock in this "
            "region (these quotas are not adjustable via API), and complete the Bedrock "
            "console's model-access use-case form if it is still outstanding."
        )
    if code == "AccessDeniedException":
        return (
            "This IAM principal may not invoke the model. Grant bedrock:InvokeModel and "
            "request model access in the Bedrock console."
        )
    if code == "ValidationException":
        return "The model id or request body is not valid for this region."
    if code == "ResourceNotFoundException":
        return (
            "The model id does not exist in this region; a cross-region inference "
            "profile may be required."
        )
    return "Check AWS credentials, region, and Bedrock model access."


def _check_s3() -> ComponentCheck:
    bucket = os.environ.get("BMEM_S3_BUCKET")
    if not bucket:
        return ComponentCheck(
            name="S3 (artifacts)",
            status=NOT_READY,
            detail="BMEM_S3_BUCKET is not set.",
            remediation="Set BMEM_S3_BUCKET; infra/setup_aws.sh provisions it.",
            probe="environment",
        )
    try:
        import boto3

        client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        _, ms = _timed(lambda: client.head_bucket(Bucket=bucket))
        return ComponentCheck(
            name="S3 (artifacts)",
            status=READY,
            detail=f"Bucket {bucket} reachable.",
            latency_ms=ms,
            probe="HeadBucket",
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        return ComponentCheck(
            name="S3 (artifacts)",
            status=NOT_READY,
            detail=f"{code} on bucket {bucket}",
            remediation="Verify the bucket exists in this region and the IAM user can read it.",
            probe="HeadBucket",
        )


def _check_codebuild() -> ComponentCheck:
    if os.environ.get("BMEM_FAKE_CODEBUILD"):
        return ComponentCheck(
            name="CodeBuild (evaluation)",
            status=SIMULATED,
            detail=(
                "BMEM_FAKE_CODEBUILD is set: scores are derived from the branch id, "
                "not from running a test suite."
            ),
            remediation="Unset BMEM_FAKE_CODEBUILD to evaluate for real.",
            probe="environment",
        )

    project = os.environ.get("BMEM_CODEBUILD_PROJECT")
    if not project:
        return ComponentCheck(
            name="CodeBuild (evaluation)",
            status=NOT_READY,
            detail="BMEM_CODEBUILD_PROJECT is not set.",
            remediation="Set BMEM_CODEBUILD_PROJECT; infra/setup_aws.sh provisions it.",
            probe="environment",
        )
    try:
        import boto3

        client = boto3.client(
            "codebuild", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

        def _describe() -> Any:
            found = client.batch_get_projects(names=[project])["projects"]
            if not found:
                raise LookupError(f"CodeBuild project {project} does not exist")
            return found[0]

        proj, ms = _timed(_describe)
        return ComponentCheck(
            name="CodeBuild (evaluation)",
            status=READY,
            detail=(
                f"Project {project} exists "
                f"({proj.get('environment', {}).get('type', 'unknown')})."
            ),
            latency_ms=ms,
            probe="BatchGetProjects",
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        return ComponentCheck(
            name="CodeBuild (evaluation)",
            status=NOT_READY,
            detail=f"{code}: {exc}"[:250],
            remediation=(
                "Run infra/setup_aws.sh, and verify codebuild:BatchGetProjects is permitted."
            ),
            probe="BatchGetProjects",
        )


def _check_repository_access() -> ComponentCheck:
    """Can this host reach a git remote at all? Ingest is useless otherwise."""
    import subprocess

    try:
        def _ls() -> int:
            return subprocess.run(
                ["git", "ls-remote", "--heads", "https://github.com/pypa/sampleproject.git"],
                capture_output=True,
                timeout=20,
            ).returncode

        rc, ms = _timed(_ls)
        if rc != 0:
            return ComponentCheck(
                name="Repository access",
                status=NOT_READY,
                detail=f"git ls-remote exited {rc}.",
                remediation="Check outbound HTTPS access to the git host.",
                latency_ms=ms,
                probe="git ls-remote",
            )
        return ComponentCheck(
            name="Repository access",
            status=READY,
            detail="Outbound git over HTTPS works.",
            latency_ms=ms,
            probe="git ls-remote",
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentCheck(
            name="Repository access",
            status=NOT_READY,
            detail=f"{type(exc).__name__}: {exc}"[:200],
            remediation="Check that git is installed and outbound HTTPS is allowed.",
            probe="git ls-remote",
        )


def _check_reports() -> ComponentCheck:
    """Reports must actually render, not merely import."""
    try:
        from reportlab.platypus import Paragraph

        from bmem.report import _build, _styles

        def _render() -> int:
            st = _styles()
            bundle = _build(
                [Paragraph("readiness", st["title"])], title="Readiness", subject_id="probe"
            )
            return len(bundle.content)

        size, ms = _timed(_render)
        return ComponentCheck(
            name="Reports (PDF)",
            status=READY,
            detail=f"Rendered a {size}-byte PDF.",
            latency_ms=ms,
            probe="reportlab render",
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentCheck(
            name="Reports (PDF)",
            status=NOT_READY,
            detail=f"{type(exc).__name__}: {exc}"[:200],
            remediation="Install the reportlab dependency (pip install -e .).",
            probe="reportlab render",
        )


# Components that must be genuinely live before the system may claim it is
# demonstrating real behaviour. CockroachDB and the reasoning/embedding
# backends are non-negotiable: without them there is no memory and no agent.
_MANDATORY = {
    "CockroachDB",
    "Bedrock (reasoning)",
    "OpenAI (reasoning)",
    "Titan (embeddings)",
    "OpenAI (embeddings)",
    "S3 (artifacts)",
    "CodeBuild (evaluation)",
}


def readiness_report() -> ReadinessReport:
    """Probe every dependency and report what is actually true.

    Runs the checks in sequence rather than concurrently: there are seven of
    them, several are sub-second, and a readiness endpoint that opens seven
    simultaneous connections to a struggling dependency makes the outage it
    is diagnosing slightly worse.
    """
    checks = [
        _check_cockroach(),
        _check_bedrock_reasoning(),
        _check_bedrock_embeddings(),
        _check_s3(),
        _check_codebuild(),
        _check_repository_access(),
        _check_reports(),
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

    # demo_ready is the strict gate: everything mandatory live, nothing faked.
    demo_ready = overall == READY

    if overall == READY:
        note = (
            "Every dependency answered. Results shown by this system are measured, "
            "not simulated."
        )
    elif overall == SIMULATED:
        names = ", ".join(c.name for c in simulated)
        note = (
            f"Running with stand-ins for: {names}. Values derived from these are not "
            f"measurements and must not be presented as evidence."
        )
    elif overall == NOT_READY:
        names = ", ".join(c.name for c in failed)
        note = f"Not ready: {names}. The system will not substitute simulated results."
    else:
        note = "Core dependencies are live, but one or more optional components are unavailable."

    return ReadinessReport(overall=overall, demo_ready=demo_ready, checks=checks, note=note)
