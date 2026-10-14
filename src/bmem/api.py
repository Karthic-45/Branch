"""FastAPI backend for the live demo: a branch-tree snapshot, a live SSE
stream of it, and an endpoint to kick off a migration run.

Runs against whatever get_planner() / get_explorer() / get_evaluator()
resolve to -- fake by default outside pytest, real once BMEM_FAKE_PLANNER and
BMEM_FAKE_CODEBUILD are unset and Bedrock/CodeBuild access exists. Nothing
here is AWS-specific; the whole demo is runnable against the live cluster
today with fakes standing in.

SSE, not a changefeed-fed WebSocket: pyproject.toml already committed to
fastapi + sse-starlette from early in this project, and a changefeed needs
either a reachable webhook sink or a dedicated consumer connection -- more
moving parts than this demo's actual requirement, which is "the browser sees
the tree change." Polling infra/preflight.py's own confirmed capability
(plain SELECT against branches) every second is indistinguishable from a true
push at demo timescales and has none of that setup.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from bmem.apply import ApplyError, ValidationProfile, apply_winning_patch
from bmem.aws_inventory import codebuild_builds, read_object, storage_inventory
from bmem.beliefs import BeliefPage, list_beliefs
from bmem.branch import MergeConflict, create_root, merged_child, resolve_subtree, revert
from bmem.db import follower_read, run_transaction, transaction
from bmem.decision import (
    Counterfactual,
    DecisionEvidence,
    DecisionNode,
    MemoryDiff,
    StrategyComparison,
    decision_graph,
    get_counterfactual,
    get_decision_evidence,
    historical_failures,
    memory_diff,
    pr_description,
)
from bmem.deploy import (
    Deployment,
    DeploymentEnvironment,
    ProductionEvidence,
    ProjectDeployment,
    environments_reached,
    get_deployments,
    get_production_evidence,
    list_project_deployments,
    record_deployment,
    record_observation,
    rollback_deployment,
    update_deployment_status,
)
from bmem.embed import get_embedder
from bmem.evaluate import get_evaluator
from bmem.explore import get_explorer
from bmem.ingest import clone_repository, ingest_repository, validate_repo_ref
from bmem.manifest import ExecutionManifest, get_manifest
from bmem.observability import (
    ClusterStats,
    CostReport,
    ProjectStats,
    SecurityReport,
    cluster_stats,
    cost_report,
    project_stats,
    security_report,
)
from bmem.orchestrate import already_merged_message, replay_branch, run_migration
from bmem.plan import get_planner
from bmem.proofs import (
    CopyOnWriteProof,
    IsolationProof,
    ResilienceProof,
    TemporalProof,
    prove_copy_on_write,
    prove_isolation,
    prove_resilience,
    prove_temporal,
)
from bmem.readiness import ReadinessReport, readiness_report
from bmem.report import (
    ArchiveEntry,
    archive_listing,
    decision_pdf,
    engineering_pdf,
    executive_pdf,
    project_archive,
)
from bmem.retrieval import search as search_memory
from bmem.verdict import BranchVerdict, branch_verdict, branch_verdicts

app = FastAPI(title="Branchable Memory")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    # Mounted at /static (not /), so a catch-all static prefix can never
    # shadow the /api/* routes below regardless of registration order. The
    # explicit "/" route just below is what makes the demo page reachable at
    # the plain root URL without the caller needing to know that detail.
    app.mount("/static", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


# Process events (fork/explore/evaluate/merge, one line per stage
# transition), keyed by root branch id. In-memory and per-process: this is
# the live demo's activity feed, not an audit record -- the durable audit
# trail is merge_log, in the database. Capped so a long-running server
# watching many demos doesn't grow this unboundedly.
_EVENTS: dict[str, list[dict[str, Any]]] = defaultdict(list)
_MAX_EVENTS_PER_ROOT = 400


def _record_event(root_id: str, branch_id: str | None, stage: str, message: str) -> None:
    events = _EVENTS[root_id]
    events.append({"ts": time.time(), "branch_id": branch_id, "stage": stage, "message": message})
    if len(events) > _MAX_EVENTS_PER_ROOT:
        del events[: len(events) - _MAX_EVENTS_PER_ROOT]


def _snapshot(root_id: str) -> list[dict[str, Any]]:
    # follower_read(), not transaction(): this is what the SSE loop below
    # polls once a second for as long as a browser tab is connected, over the
    # same table fork() and merge() write to. Under a plain live-read
    # transaction that poll genuinely contended with the write path -- see
    # follower_read()'s docstring for how that was actually found.
    with follower_read() as cur:
        nodes = resolve_subtree(cur, root_id)
        outcomes_map: dict[str, dict[str, Any]] = {}
        node_ids = [n.id for n in nodes]
        if node_ids:
            try:
                cur.execute(
                    """
                    SELECT branch_id, reason, passed, total, outcome
                    FROM branch_outcomes
                    WHERE branch_id = ANY(%(ids)s::UUID[])
                    """,
                    {"ids": node_ids},
                )
                for r in cur.fetchall():
                    outcomes_map[str(r["branch_id"])] = {
                        "reason": r["reason"],
                        "passed": r["passed"],
                        "total": r["total"],
                        "outcome": r["outcome"],
                    }
            except Exception:
                pass
    return [
        {
            "id": n.id,
            "parent_id": n.parent_id,
            "hypothesis": n.hypothesis,
            "status": n.status,
            "score": n.score,
            "depth": n.depth,
            "created_at": n.created_at,
            "reason": outcomes_map.get(n.id, {}).get("reason"),
            "passed": outcomes_map.get(n.id, {}).get("passed"),
            "total": outcomes_map.get(n.id, {}).get("total"),
        }
        for n in nodes
    ]


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    """Which backends are stand-ins, so the UI can say so on the page.

    A faked score is indistinguishable from a real one once it is rendered as a
    percentage, and the difference is the whole basis for believing the branch
    that won deserved to. Better to publish it than to let a viewer assume.
    """
    return {
        "fake": {
            "reasoning (Bedrock)": bool(os.environ.get("BMEM_FAKE_PLANNER")),
            "embeddings (Titan)": bool(os.environ.get("BMEM_FAKE_EMBEDDINGS")),
            "evaluation (CodeBuild)": bool(os.environ.get("BMEM_FAKE_CODEBUILD")),
        }
    }


class CreateRepoRequest(BaseModel):
    repo_url: str
    ref: str | None = None
    hypothesis: str = "main"
    test_selector: str = ""


class CreateRepoResponse(BaseModel):
    root_id: str
    accepted: bool = True


@app.post("/api/repos", status_code=202)
def create_repo(request: CreateRepoRequest) -> CreateRepoResponse:
    """Self-service ingest: clone a repo, embed it into a fresh root branch.

    Mirrors start_migration()'s shape -- the fast, cheap part (minting the
    root id, so the caller can navigate to it immediately) happens
    synchronously; the slow part (clone + embed, potentially thousands of
    chunks) runs in a background thread, reporting progress through the same
    event feed /api/migrations already uses, so the existing Activity panel
    and SSE stream show ingest progress with no separate UI plumbing.

    source_repo/source_ref/test_selector are stored on the root itself (see
    schema/005_source_repo.sql) so a later start_migration() call on this
    root evaluates candidate patches against the repo actually ingested here
    -- not whatever BMEM_TARGET_REPO happens to be set to on the server.
    """
    try:
        validate_repo_ref(request.repo_url, request.ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with transaction() as cur:
        root_id = create_root(
            cur,
            hypothesis=request.hypothesis,
            source_repo=request.repo_url,
            source_ref=request.ref,
            test_selector=request.test_selector or None,
        )

    def _run() -> None:
        _record_event(root_id, None, "ingesting", f"Cloning {request.repo_url}")
        try:
            with clone_repository(request.repo_url, request.ref) as clone_dir:
                report = ingest_repository(
                    clone_dir,
                    root_id,
                    embed=get_embedder(),
                    on_progress=lambda done, total: _record_event(
                        root_id, None, "ingesting", f"Embedded {done:,}/{total:,} chunks"
                    ),
                )
            _record_event(
                root_id, None, "ingested",
                f"{report.files_scanned} files scanned, {report.chunks_written} chunks embedded"
                + (f", {len(report.files_skipped)} skipped" if report.files_skipped else ""),
            )
        except Exception as exc:
            print(f"ingest for {root_id} failed:", flush=True)
            traceback.print_exc()
            _record_event(root_id, None, "error", f"Ingest failed: {exc}")

    threading.Thread(target=_run, daemon=True).start()
    return CreateRepoResponse(root_id=root_id)


@app.get("/api/branches/{root_id}")
def get_tree(root_id: str) -> list[dict[str, Any]]:
    nodes = _snapshot(root_id)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"no such branch: {root_id}")
    return nodes


@app.get("/api/events/{root_id}")
def get_events(root_id: str) -> list[dict[str, Any]]:
    """Full process history so far -- what a client fetches once on load,
    before the stream below starts delivering only what's new."""
    return _EVENTS.get(root_id, [])


@app.get("/api/branches/{root_id}/stream")
async def stream_tree(root_id: str) -> EventSourceResponse:
    async def _events() -> AsyncIterator[dict[str, Any]]:
        last_tree: str | None = None
        sent_events = 0
        while True:
            # Off the event loop: this does real network I/O against the
            # cluster, and FastAPI's async handlers share one loop per worker.
            nodes = await asyncio.to_thread(_snapshot, root_id)
            payload = json.dumps(nodes, sort_keys=True)
            if payload != last_tree:
                yield {"event": "tree", "data": payload}
                last_tree = payload

            # Process events, alongside the tree on the same connection --
            # one fewer connection than a dedicated log stream, and a client
            # that only cares about the tree can just ignore "log" events.
            events = _EVENTS.get(root_id, [])
            if len(events) > sent_events:
                yield {"event": "log", "data": json.dumps(events[sent_events:])}
                sent_events = len(events)

            await asyncio.sleep(1.0)

    return EventSourceResponse(_events())


class StartMigrationRequest(BaseModel):
    parent_id: str
    goal: str
    n: int = 4


class StartMigrationResponse(BaseModel):
    run_id: str
    parent_id: str
    accepted: bool = True


@app.post("/api/migrations", status_code=202)
def start_migration(request: StartMigrationRequest) -> StartMigrationResponse:
    """Kick off run_migration() in the background and return immediately.

    A real run can take minutes (an LLM call per branch, then a build-and-test
    cycle per branch) -- holding the HTTP request open for that long is both
    a bad client experience and pointless, since the SSE stream above already
    shows the tree filling in as forks, then scores, then the merge land. The
    response here is just an acknowledgment that the run started.
    """
    run_id = uuid.uuid4().hex
    root_id = request.parent_id

    # Reject up front rather than with a 202 that fails minutes later in the
    # background: merge() allows one live winner per parent, so this run could
    # never land. The form shows this message directly.
    existing = merged_child(root_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=already_merged_message(root_id, existing))

    # A root ingested through /api/repos knows its own source repo; fall back
    # to the server-wide env vars for anything ingested via the CLI script
    # directly, which is every root that existed before that endpoint did.
    #
    # repo/ref/test_selector are read as a single unit, not three independent
    # ternaries -- found the hard way, by shipping the independent version
    # first: a root with source_repo set but no source_ref (a valid, common
    # case -- ingest didn't pin one) fell through to BMEM_TARGET_REF, which is
    # a commit SHA belonging to the *env var's* repo, not this one. CodeBuild
    # cloned the right repo and then failed `git checkout` on a commit that
    # repo has never heard of -- no result.json, every branch dies, and the
    # failure has nothing to do with the migration itself. If this root has
    # its own repo, its own (possibly empty) ref and test_selector travel
    # with it; empty ref is handled by buildspec.yml as "the clone's default
    # branch", not "substitute a stranger's pinned commit".
    with transaction() as cur:
        cur.execute(
            "SELECT source_repo, source_ref, test_selector FROM branches WHERE id = %(id)s",
            {"id": root_id},
        )
        row = cur.fetchone()

    if row and row["source_repo"]:
        target_repo = row["source_repo"]
        target_ref = row["source_ref"] or ""
        test_selector = row["test_selector"] or ""
    else:
        target_repo = os.environ.get("BMEM_TARGET_REPO", "")
        target_ref = os.environ.get("BMEM_TARGET_REF", "main")
        # Empty means the full suite, per CodeBuildEvaluator's own default --
        # buildspec.yml already anticipates this being scoped ("scoped for
        # the live demo, full for the recorded result").
        test_selector = os.environ.get("BMEM_TEST_SELECTOR", "")

    def _run() -> None:
        try:
            run_migration(
                request.parent_id,
                request.goal,
                plan=get_planner(),
                explore=get_explorer(),
                evaluate=get_evaluator(
                    target_repo=target_repo,
                    target_ref=target_ref,
                    test_selector=test_selector,
                ),
                embed=get_embedder(),
                n=request.n,
                run_id=run_id,
                on_event=lambda branch_id, stage, message: _record_event(
                    root_id, branch_id, stage, message
                ),
                source_repo=target_repo or None,
                source_ref=target_ref or None,
            )
        except Exception as exc:
            # The client already got its 202 and is watching the SSE stream,
            # not this thread -- a silent failure here is a hung demo with no
            # visible cause. Print loudly AND put it in the feed itself: the
            # two AWS account restrictions found during this project's own
            # development (Bedrock invoke blocked, CodeBuild quota at 0) each
            # first showed up as exactly this -- a run that just went quiet.
            print(f"migration {run_id} failed:", flush=True)
            traceback.print_exc()
            _record_event(root_id, None, "error", f"Migration failed: {exc}")

    _record_event(root_id, None, "started", f'Migration requested: "{request.goal}"')
    threading.Thread(target=_run, daemon=True).start()
    return StartMigrationResponse(run_id=run_id, parent_id=request.parent_id)


@app.get("/api/merges/{parent_id}")
def list_merges(parent_id: str) -> list[dict[str, Any]]:
    """The merge log for one branch, newest first, reverted entries included.

    The log is append-only, so a reverted merge stays visible with its
    ``reverted_at`` set -- which is what lets the UI show that a conclusion was
    reached and then withdrawn, rather than silently forgetting it happened.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT m.id, m.winner_id, m.merged_at, m.reverted_at,
                   b.hypothesis AS winner_hypothesis, b.score AS winner_score,
                   (SELECT s3_uri FROM memory
                    WHERE branch_id = m.winner_id AND s3_uri IS NOT NULL
                    LIMIT 1) AS patch_s3_uri
            FROM merge_log m
            JOIN branches b ON b.id = m.winner_id
            WHERE m.parent_id = %(p)s
            ORDER BY m.merged_at DESC
            """,
            {"p": parent_id},
        )
        rows = cur.fetchall()

    return [
        {
            "merge_id": str(r["id"]),
            "winner_id": str(r["winner_id"]),
            "winner_hypothesis": r["winner_hypothesis"],
            "winner_score": r["winner_score"],
            "merged_at": r["merged_at"].isoformat() if r["merged_at"] else None,
            "reverted_at": r["reverted_at"].isoformat() if r["reverted_at"] else None,
            "has_patch": bool(r["patch_s3_uri"]),
        }
        for r in rows
    ]


@app.get("/api/branches/{root_id}/verdicts")
def get_branch_verdicts(root_id: str) -> list[BranchVerdict]:
    """Why every branch under a project was merged or abandoned, in plain words,
    built from the outcomes and rationale recorded at decision time."""
    try:
        return branch_verdicts(root_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/branches/{branch_id}/verdict")
def get_branch_verdict(branch_id: str) -> BranchVerdict:
    """The merge/abandon verdict for one branch."""
    try:
        return branch_verdict(branch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    """Every project root, newest first, with what it holds.

    A root id is otherwise only discoverable from the URL that created it;
    this lets the dashboard and reports find a project after a reload.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT b.id, b.created_at, b.source_repo, b.source_ref,
                   (SELECT count(*) FROM branches c WHERE c.parent_id = b.id) AS children,
                   (SELECT count(*) FROM memory m
                     WHERE m.branch_id = b.id AND NOT m.tombstone) AS memories
            FROM branches b
            WHERE b.parent_id IS NULL
            ORDER BY b.created_at DESC
            """
        )
        return [
            {
                "root_id": str(r["id"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "source_repo": r["source_repo"],
                "source_ref": r["source_ref"],
                "child_branches": int(r["children"]),
                "memories": int(r["memories"]),
            }
            for r in cur.fetchall()
        ]


@app.get("/api/mcp/tools")
async def mcp_tools() -> dict[str, Any]:
    """The audit MCP server's actual tool list, read from the server object.

    The dashboard states how many tools external agents get and whether any
    can write; this is where that statement comes from, so it cannot drift
    from the code the way a hand-typed "9 tools, 0 write methods" can.
    """
    from bmem.mcp_server import mcp  # lazy: only this endpoint needs it

    tools = await mcp.list_tools()
    rows = []
    for t in tools:
        ann = t.annotations.model_dump() if t.annotations else {}
        rows.append(
            {
                "name": t.name,
                "description": (t.description or "").strip().split("\n")[0],
                "read_only": ann.get("read_only_hint"),
                "destructive": ann.get("destructive_hint"),
            }
        )
    return {
        "server": mcp.name,
        "tools": rows,
        "total": len(rows),
        "read_only": sum(1 for r in rows if r["read_only"] is True),
        "may_write": sum(1 for r in rows if r["read_only"] is not True),
    }


@app.get("/api/aws/storage/{root_id}")
def aws_storage(root_id: str, max_runs: int = 8) -> dict[str, Any]:
    """What this project has stored in S3, listed live from S3 and organised
    by run and branch using the database's own record of the runs."""
    return storage_inventory(root_id, max_runs=max(1, min(max_runs, 25)))


@app.get("/api/aws/builds/{root_id}")
def aws_builds(root_id: str) -> dict[str, Any]:
    """This project's CodeBuild evaluation builds, live from CodeBuild."""
    return codebuild_builds(root_id)


@app.get("/api/aws/object")
def aws_object(key: str) -> Response:
    """Preview one of this application's S3 objects (runs/ or applied/ only)."""
    try:
        body, content_type = read_object(key)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- e.g. NoSuchKey, AccessDenied
        raise HTTPException(status_code=404, detail=f"{type(exc).__name__}: {exc}") from exc
    if key.endswith((".patch", ".log", ".json", ".xml", ".py", ".txt")):
        content_type = "text/plain; charset=utf-8"
    return Response(content=body, media_type=content_type)


_BRANCH_ARTIFACTS = {
    "candidate.patch": "text/x-patch",
    "apply.log": "text/plain",
    "pytest.log": "text/plain",
    "result.json": "application/json",
    "results.xml": "application/xml",
}


@app.get("/api/branches/{branch_id}/artifacts/{name}")
def get_branch_artifact(branch_id: str, name: str) -> Response:
    """One of a branch's evaluation files from S3 -- for any branch, not just
    a winner: the patch Claude wrote, why ``git apply`` refused it, what pytest
    printed, and the score it produced. All live next to the patch under
    ``runs/<run>/<branch>/``; the patch's own s3_uri is the anchor.
    """
    if name not in _BRANCH_ARTIFACTS:
        raise HTTPException(
            status_code=400, detail=f"unknown artifact; one of {sorted(_BRANCH_ARTIFACTS)}"
        )
    with transaction() as cur:
        cur.execute(
            "SELECT s3_uri FROM memory WHERE branch_id = %(b)s AND s3_uri IS NOT NULL LIMIT 1",
            {"b": branch_id},
        )
        row = cur.fetchone()
    if not row or not row["s3_uri"]:
        raise HTTPException(status_code=404, detail="no evaluation artifacts for this branch")

    bucket, _, patch_key = row["s3_uri"].removeprefix("s3://").partition("/")
    key = f"{patch_key.rsplit('/', 1)[0]}/{name}"

    import boto3  # lazy: offline tests and fake-backend runs need no AWS SDK config

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001 -- missing object is the common case
        raise HTTPException(status_code=404, detail=f"{name} not found for this branch") from exc
    return Response(content=body, media_type=_BRANCH_ARTIFACTS[name])


@app.get("/api/merges/{merge_id}/patch")
def get_merge_patch(merge_id: str) -> Response:
    """Proxy the winning branch's actual code diff out of S3.

    memory.s3_uri only gets set for branches evaluated by the real
    CodeBuildEvaluator (see orchestrate.py's _record_patch_uri) -- a merge
    scored by BMEM_FAKE_CODEBUILD, or one that landed before this feature
    existed, has no object behind it, hence the 404 rather than a bucket
    lookup with a made-up key.
    """
    with transaction() as cur:
        cur.execute("SELECT winner_id FROM merge_log WHERE id = %(id)s", {"id": merge_id})
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no such merge: {merge_id}")

        cur.execute(
            "SELECT s3_uri FROM memory WHERE branch_id = %(b)s AND s3_uri IS NOT NULL LIMIT 1",
            {"b": row["winner_id"]},
        )
        patch_row = cur.fetchone()

    s3_uri = patch_row["s3_uri"] if patch_row else None
    if not s3_uri:
        raise HTTPException(
            status_code=404,
            detail="no patch on record for this merge -- merged before patch tracking "
            "existed, or scored with a fake evaluator",
        )

    bucket, _, key = s3_uri.removeprefix("s3://").partition("/")

    import boto3  # lazy: offline tests and fake-backend runs need no AWS SDK config

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
    except Exception as exc:  # noqa: BLE001 -- re-raised with real diagnostic context
        raise HTTPException(
            status_code=502, detail=f"could not fetch patch from S3: {exc}"
        ) from exc

    return Response(
        content=body,
        media_type="text/x-patch",
        headers={"Content-Disposition": f'attachment; filename="{merge_id}.patch"'},
    )


class RevertRequest(BaseModel):
    promote: str | None = None


class RevertResponse(BaseModel):
    merge_id: str
    promoted_merge_id: str | None = None


@app.post("/api/merges/{merge_id}/revert")
def revert_merge(merge_id: str, request: RevertRequest | None = None) -> RevertResponse:
    """Undo a merge, optionally adopting a sibling in the winner's place.

    Synchronous, unlike starting a migration: a revert restores a snapshot that
    is already sitting in ``merge_log`` and re-embeds a handful of rows, so it
    finishes in about the time the response takes to travel. Making the caller
    wait means the demo can show the failure -- reverting the same merge twice
    is a 409, not a silent no-op -- instead of hiding it in a background thread.
    """
    promote = request.promote if request else None

    with transaction() as cur:
        cur.execute("SELECT parent_id FROM merge_log WHERE id = %(id)s", {"id": merge_id})
        row = cur.fetchone()
    root_id = str(row["parent_id"]) if row else None

    try:
        promoted = revert(merge_id, embed=get_embedder(), promote=promote)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MergeConflict as exc:
        if root_id:
            _record_event(root_id, None, "error", f"Revert failed: {exc}")
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if root_id:
        _record_event(root_id, None, "reverted", "Merge undone -- restored the pre-merge state")
    return RevertResponse(merge_id=merge_id, promoted_merge_id=promoted)


class SetValidationProfileRequest(BaseModel):
    install: str | None = None
    lint: str | None = None
    typecheck: str | None = None
    build: str | None = None
    test: str | None = None


@app.put("/api/repos/{root_id}/validation-profile")
def set_validation_profile(root_id: str, request: SetValidationProfileRequest) -> ValidationProfile:
    """Store how to validate a winning patch against this repository.

    A field left unset stays unset, not "pytest by default" -- the apply
    engine (bmem.apply) treats an empty profile as "not configured" and skips
    validation with a visible reason rather than silently assuming Python.
    """
    profile = ValidationProfile.model_validate(request.model_dump())
    with transaction() as cur:
        cur.execute(
            "UPDATE branches SET validation_profile = %(p)s "
            "WHERE id = %(id)s AND parent_id IS NULL",
            {"p": profile.model_dump_json(), "id": root_id},
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=404, detail=f"no root branch {root_id} (or it isn't a root)"
            )
    return profile


@app.get("/api/repos/{root_id}/validation-profile")
def get_validation_profile(root_id: str) -> ValidationProfile:
    with transaction() as cur:
        cur.execute(
            "SELECT validation_profile FROM branches WHERE id = %(id)s AND parent_id IS NULL",
            {"id": root_id},
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"no root branch {root_id} (or it isn't a root)"
        )
    return ValidationProfile.model_validate(row["validation_profile"] or {})


class ApplyRequest(BaseModel):
    run_validation: bool = True
    allow_base_changed: bool = False


@app.post("/api/merges/{merge_id}/apply")
def apply_merge(merge_id: str, request: ApplyRequest | None = None) -> dict[str, Any]:
    """Apply the winning patch to an isolated clone, validate it, commit locally.

    Synchronous like revert, not backgrounded like migrations: unlike a full
    migration this touches one repository once, and the caller needs the
    conflict/validation detail immediately to decide whether to retry with
    ``allow_base_changed``. FastAPI runs sync ``def`` handlers in a thread
    pool, so a slow validation command here does not block other requests.

    Never pushes anywhere or opens a PR -- see bmem.apply's module docstring.
    A caller wanting the actual GitHub PR needs a write-scoped token this
    deployment does not have configured; that is the next thing to wire in,
    not something to fake here.
    """
    req = request or ApplyRequest()
    try:
        result = apply_winning_patch(
            merge_id, validate=req.run_validation, allow_base_changed=req.allow_base_changed
        )
    except ApplyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump()


@app.get("/api/merges/{merge_id}/applications")
def list_applications(merge_id: str) -> list[dict[str, Any]]:
    """Every past attempt to apply this merge's patch, newest first -- a
    rejected base_changed attempt and a later successful one are both kept,
    same append-only shape as merge_log itself.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT id, base_sha, current_head_sha, base_changed, branch, patch_applied,
                   conflicts, validation_status, validation_steps, commit_sha,
                   applied_tree_s3_uri, created_at
            FROM decision_applications
            WHERE merge_id = %(id)s
            ORDER BY created_at DESC
            """,
            {"id": merge_id},
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "base_sha": r["base_sha"],
            "current_head_sha": r["current_head_sha"],
            "base_changed": r["base_changed"],
            "branch": r["branch"],
            "patch_applied": r["patch_applied"],
            "conflicts": r["conflicts"] or [],
            "validation_status": r["validation_status"],
            "validation_steps": r["validation_steps"] or [],
            "commit_sha": r["commit_sha"],
            "applied_tree_s3_uri": r["applied_tree_s3_uri"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@app.get("/api/merges/{merge_id}/evidence")
def get_evidence(merge_id: str) -> DecisionEvidence:
    """Belief -> alternatives -> validation -> provenance for one merge, in
    one response -- backs both the Evidence Graph UI and PR content, from data
    that already exists rather than a second source of truth for it.
    """
    try:
        return get_decision_evidence(merge_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/merges/{merge_id}/counterfactual")
def get_merge_counterfactual(merge_id: str) -> Counterfactual:
    """Why this strategy and not the others, from the rationale recorded at
    merge time plus each branch's own measured outcome.

    Never re-derived: the rationale is read back, not recomputed, so the
    answer cannot drift from the decision that was actually made.
    """
    try:
        return get_counterfactual(merge_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/stats/{root_id}")
def get_project_stats(root_id: str) -> ProjectStats:
    """Counts and rates for everything recorded under one root."""
    return project_stats(root_id)


@app.get("/api/cost/{root_id}")
def get_cost_report(root_id: str) -> CostReport:
    """Measured usage per AWS service, with dollars only where the operator
    configured a rate (BMEM_PRICE_* env vars). See observability.cost_report
    for why no pricing is assumed.
    """
    return cost_report(root_id)


@app.get("/api/cluster")
def get_cluster_stats() -> ClusterStats:
    """Live CockroachDB state: row counts, active queries, vector index and
    GC window. Introspection a least-privilege user cannot read degrades to
    null with a note rather than failing the request.
    """
    return cluster_stats()


@app.post("/api/proofs/isolation")
def run_isolation_proof() -> IsolationProof:
    """Write a private belief to one branch, search for it from its sibling,
    and report what each could see. Creates and removes its own scratch
    branches -- see bmem.proofs.
    """
    return prove_isolation(embed=get_embedder())


@app.post("/api/proofs/copy-on-write")
def run_cow_proof(seed_rows: int = 200, forks: int = 5) -> CopyOnWriteProof:
    """Seed a parent with memory, fork it repeatedly, and count the rows
    forking actually cost.
    """
    return prove_copy_on_write(embed=get_embedder(), seed_rows=seed_rows, forks=forks)


@app.post("/api/proofs/temporal")
def run_temporal_proof() -> TemporalProof:
    """Merge a branch, then reconstruct what the parent believed beforehand."""
    return prove_temporal(embed=get_embedder())


def _pdf_response(bundle: Any, disposition: str = "attachment") -> Response:
    """Serve a report with its integrity metadata in the headers, so a
    downloaded file can still be tied back to what generated it.

    ``disposition="inline"`` lets a browser render the PDF in place instead of
    downloading it -- the same bytes and the same SHA-256 either way. Anything
    other than the two known values falls back to ``attachment`` rather than
    being interpolated into the header, which is a response-splitting vector
    for a value that arrives in the query string.

    ``Access-Control-Expose-Headers`` is what makes the hash readable from
    fetch(). Without it the browser hides both custom headers even same-origin
    once the CORS middleware has touched the response, and the preview could
    show a report without being able to state its checksum.
    """
    if disposition != "inline":
        disposition = "attachment"
    return Response(
        content=bundle.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{bundle.filename}"',
            "X-Report-SHA256": bundle.sha256,
            "X-Report-Generated-At": bundle.generated_at,
            "Access-Control-Expose-Headers": "X-Report-SHA256, X-Report-Generated-At",
        },
    )


@app.get("/api/merges/{merge_id}/report.pdf", response_class=Response)
def get_decision_report(merge_id: str, disposition: str = "attachment") -> Response:
    """One decision as a PDF: what won, why, what it changed, what followed."""
    try:
        return _pdf_response(decision_pdf(merge_id), disposition)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{root_id}/executive.pdf", response_class=Response)
def get_executive_report(root_id: str, disposition: str = "attachment") -> Response:
    """Project-level summary for a non-technical reader."""
    try:
        return _pdf_response(executive_pdf(root_id), disposition)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{root_id}/engineering.pdf", response_class=Response)
def get_engineering_report(root_id: str, disposition: str = "attachment") -> Response:
    """Everything in the executive report plus provenance and reproducibility."""
    try:
        return _pdf_response(engineering_pdf(root_id), disposition)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{root_id}/archive-listing")
def get_archive_listing(root_id: str) -> list[ArchiveEntry]:
    """The archive's own table of contents, so a reader can see what they are
    about to download before committing to it."""
    try:
        return archive_listing(root_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{root_id}/archive.zip", response_class=Response)
def get_project_archive(root_id: str) -> Response:
    """Both PDFs, a decision report per merge, and the raw JSON behind them."""
    try:
        content, digest = project_archive(root_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="branchable-memory-{root_id[:8]}.zip"',
            "X-Archive-SHA256": digest,
        },
    )


@app.post("/api/proofs/resilience")
def run_resilience_proof(branches: int = 5, failures: int = 2) -> ResilienceProof:
    """Start N branch workers, fail some of them, and show the rest still land.

    Failures are injected in application code only -- never against the
    cluster or a real build. See bmem.proofs.prove_resilience.
    """
    return prove_resilience(embed=get_embedder(), branches=branches, failures=failures)


class RecordDeploymentRequest(BaseModel):
    environment: str
    commit_sha: str | None = None
    target: str | None = None
    artifact: str | None = None
    detail: str | None = None
    status: str = "pending"


@app.post("/api/merges/{merge_id}/deployments", status_code=201)
def create_deployment(merge_id: str, request: RecordDeploymentRequest) -> Deployment:
    """Record that this decision was deployed. Does not perform a deployment.

    Shipping is `infra/deploy_beanstalk.sh`, run deliberately by a person --
    see bmem.deploy for why nothing here fires a deploy on its own.
    """
    try:
        return record_deployment(
            merge_id,
            environment=request.environment,
            commit_sha=request.commit_sha,
            target=request.target,
            artifact=request.artifact,
            detail=request.detail,
            status=request.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/merges/{merge_id}/deployments")
def list_deployments(merge_id: str) -> list[Deployment]:
    return get_deployments(merge_id)


@app.get("/api/projects/{root_id}/deployments")
def list_all_deployments(root_id: str, limit: int = 100) -> list[ProjectDeployment]:
    """Every deployment made from any decision under this root, newest first."""
    return list_project_deployments(root_id, limit=min(limit, 500))


@app.get("/api/merges/{merge_id}/environments")
def list_environments(merge_id: str) -> list[DeploymentEnvironment]:
    """Which environments this decision has reached."""
    return environments_reached(merge_id)


class DeploymentStatusRequest(BaseModel):
    status: str
    detail: str | None = None


@app.patch("/api/deployments/{deployment_id}")
def patch_deployment(deployment_id: str, request: DeploymentStatusRequest) -> Deployment:
    try:
        return update_deployment_status(deployment_id, request.status, detail=request.detail)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RollbackRequest(BaseModel):
    reason: str


@app.post("/api/deployments/{deployment_id}/rollback")
def rollback(deployment_id: str, request: RollbackRequest) -> dict[str, Any]:
    """Mark a deployment rolled back and name the previous known-good version.

    Requires a reason, and records one. Restoring the previous version is the
    operator's own deploy step -- this does not redeploy anything.
    """
    try:
        return rollback_deployment(deployment_id, reason=request.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ObservationRequest(BaseModel):
    metric: str
    phase: str
    value: float
    source: str
    unit: str | None = None
    note: str | None = None


@app.post("/api/deployments/{deployment_id}/observations", status_code=201)
def create_observation(deployment_id: str, request: ObservationRequest) -> dict[str, Any]:
    """Record a real measurement taken around a deployment.

    `source` is mandatory ('cloudwatch' or 'manual'). There is no path that
    generates a metric -- see bmem.deploy.record_observation.
    """
    try:
        return record_observation(
            deployment_id,
            metric=request.metric,
            phase=request.phase,
            value=request.value,
            source=request.source,
            unit=request.unit,
            note=request.note,
        ).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/deployments/{deployment_id}/evidence")
def deployment_evidence(deployment_id: str) -> ProductionEvidence:
    """Before/after measurements alongside what the evaluation scored."""
    try:
        return get_production_evidence(deployment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/readiness")
def get_readiness() -> ReadinessReport:
    """Probe every dependency and report what is actually true.

    Every check performs a real operation -- a query, an API call, a model
    invocation -- rather than inferring readiness from configuration. A
    component running on a stand-in reports `simulated`, which is explicitly
    not a pass: see bmem.readiness for why that distinction is load-bearing.
    """
    return readiness_report()


@app.get("/api/security")
def get_security_report() -> SecurityReport:
    """Security posture, with the evaluation-sandbox isolation check read live
    from the CodeBuild project rather than asserted from a comment.
    """
    return security_report()


@app.get("/api/merges/{merge_id}/memory-diff")
def get_memory_diff(merge_id: str) -> MemoryDiff:
    """What promoting this winner did to the parent's belief set.

    Isolates this merge specifically -- computed from the pre-merge snapshot
    plus the winner's rows, not from the parent's current state -- so it stays
    correct for a merge that has since been reverted or built on.
    """
    try:
        return memory_diff(merge_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/branches/{parent_id}/failures")
def get_branch_failures(parent_id: str, limit: int = 10) -> list[StrategyComparison]:
    """Strategies already tried against this branch and lost.

    The same list run_migration feeds to the planner before proposing new
    strategies -- exposed so a caller (or a judge) can see what the agent
    knows it has already ruled out.
    """
    return historical_failures(parent_id, limit=limit)


@app.get("/api/decisions/{root_id}")
def get_decision_graph(root_id: str) -> list[DecisionNode]:
    """Every decision in this root's subtree, oldest first -- each problem,
    the strategies that competed, the winner, and whether it reached a commit.
    """
    return decision_graph(root_id)


@app.get("/api/branches/{branch_id}/search")
def search_branch(
    branch_id: str, q: str, k: int = 8, mode: str = "auto"
) -> list[dict[str, Any]]:
    """Semantic search exactly as a branch's explore step sees it.

    The same retrieval call explore_branch makes, exposed so "what context
    would Claude get here?" can be answered without spending a migration run.
    ``mode`` is ``auto`` (index, exact fallback when short), ``ann`` (index
    only) or ``exact`` -- comparing ``ann`` with ``exact`` shows directly
    whether the vector index is returning what it should.
    """
    if mode not in ("auto", "ann", "exact"):
        raise HTTPException(status_code=400, detail="mode must be auto, ann or exact")
    if not 1 <= k <= 50:
        raise HTTPException(status_code=400, detail="k must be between 1 and 50")
    vector = get_embedder()([q])[0]
    try:
        hits = run_transaction(lambda cur: search_memory(cur, branch_id, vector, k=k, mode=mode))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        {
            "key": h.key,
            "distance": round(h.distance, 4),
            "inherited": h.inherited,
            "source_branch_id": h.source_branch_id,
            "preview": (h.content or "")[:200],
        }
        for h in hits
    ]


@app.get("/api/branches/{branch_id}/beliefs")
def get_branch_beliefs(
    branch_id: str,
    q: str | None = None,
    include_tombstoned: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> BeliefPage:
    """Everything this branch can see, with the provenance of each row.

    The point of the response is the split between ``own`` and ``inherited``:
    a branch's visible belief count is the whole lineage, while its physical
    row count is only what it concluded itself. That gap is copy-on-write,
    stated as two numbers a reader can check against the cluster.
    """
    try:
        return list_beliefs(
            branch_id,
            query=q,
            include_tombstoned=include_tombstoned,
            limit=min(limit, 500),
            offset=max(offset, 0),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/branches/{branch_id}/manifest")
def get_branch_manifest(branch_id: str) -> ExecutionManifest:
    """What this branch's attempt actually ran against: which memories it
    retrieved, which model answered, which commit and test selector scored it.

    404 rather than an empty manifest for a branch explored before this
    feature existed -- "we never recorded this" and "we recorded that nothing
    happened" are different claims, and conflating them is exactly the kind
    of fabricated provenance this table exists to prevent.
    """
    try:
        return get_manifest(branch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ReplayResponse(BaseModel):
    original_branch_id: str
    original_score: float | None
    replay_branch_id: str
    replay_run_id: str
    replay_score: float
    replay_reason: str
    score_delta: float | None


@app.post("/api/branches/{branch_id}/replay", status_code=201)
def replay(branch_id: str) -> ReplayResponse:
    """Re-run this branch's strategy against the same pinned commit, on a new
    sibling branch, and report both scores.

    Synchronous, and deliberately so despite taking as long as one branch of
    a migration: a replay's entire purpose is the comparison in the response,
    and a caller who wanted fire-and-forget would start a migration instead.
    The original branch is never modified -- see orchestrate.replay_branch.
    """
    with transaction() as cur:
        cur.execute("SELECT parent_id FROM branches WHERE id = %(id)s", {"id": branch_id})
        row = cur.fetchone()
    root_id = str(row["parent_id"]) if row and row["parent_id"] else None

    try:
        result = replay_branch(
            branch_id,
            explore=get_explorer(),
            embed=get_embedder(),
            on_event=(
                (lambda bid, stage, message: _record_event(root_id, bid, stage, message))
                if root_id
                else None
            ),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    evaluation = result.outcome.evaluation
    return ReplayResponse(
        original_branch_id=result.original_branch_id,
        original_score=result.original_score,
        replay_branch_id=result.replay_branch_id,
        replay_run_id=result.replay_run_id,
        replay_score=evaluation.score,
        replay_reason=evaluation.reason,
        score_delta=(
            evaluation.score - result.original_score
            if result.original_score is not None
            else None
        ),
    )


@app.get("/api/merges/{merge_id}/pr-description", response_class=Response)
def get_pr_description(merge_id: str) -> Response:
    """Markdown PR body, generated from recorded evidence only.

    This does not open a PR on GitHub -- that needs a write-scoped token
    this deployment has never been given, and nothing here should quietly
    pretend otherwise. It returns exactly what a human would paste into one.
    """
    try:
        evidence = get_decision_evidence(merge_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=pr_description(evidence), media_type="text/markdown")
