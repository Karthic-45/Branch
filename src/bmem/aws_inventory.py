"""What this project has actually stored in AWS, read live from AWS.

The dashboard's AWS Storage page is built from three sources joined together:

* CockroachDB -- which runs and branches belong to a project, the strategy
  each branch tried, its score, and which branch a decision merged. This is
  the index; it says *what to look for*.
* S3 (``list_objects_v2``) -- the objects that really exist for each run and
  branch under ``runs/<run_id>/<branch_id>/``, plus applied commit bundles
  under ``applied/<merge_id>/``. Sizes and timestamps come from S3 itself,
  never from the database.
* CodeBuild (``list_builds_for_project`` + ``batch_get_builds``) -- every
  evaluation build, matched to its branch by the RUN_ID / BRANCH_ID
  environment variables it was started with, with real phase durations.

Each AWS section degrades independently: a missing IAM permission on one
call becomes an ``error`` string on that section, not a failed page.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from bmem.db import transaction

__all__ = ["READABLE_PREFIXES", "codebuild_builds", "read_object", "storage_inventory"]

# Only these prefixes are this application's own artifacts. Anything else in
# the bucket is not ours to show, so read_object() refuses it.
READABLE_PREFIXES = ("runs/", "applied/")
_MAX_OBJECT_BYTES = 512 * 1024


def _region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def _bucket() -> str | None:
    return os.environ.get("BMEM_S3_BUCKET")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _project_index(root_id: str, max_runs: int) -> dict[str, Any]:
    """Runs, branches and decisions for one project, newest run first."""
    with transaction() as cur:
        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT m.run_id, m.branch_id, m.strategy_name, m.hypothesis, m.created_at,
                   m.reasoning_model_id, m.embedding_model_id, m.codebuild_project,
                   m.target_repo, m.base_sha,
                   b.status, b.score, b.parent_id,
                   o.outcome, o.passed, o.total, o.reason
            FROM execution_manifests m
            JOIN branches b ON b.id = m.branch_id
            LEFT JOIN branch_outcomes o ON o.branch_id = m.branch_id
            WHERE m.branch_id IN (SELECT id FROM subtree)
            ORDER BY m.created_at DESC
            """,
            {"root": root_id},
        )
        rows = cur.fetchall()

        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT id, parent_id, winner_id, merged_at, reverted_at
            FROM merge_log WHERE parent_id IN (SELECT id FROM subtree)
            ORDER BY merged_at DESC
            """,
            {"root": root_id},
        )
        merges = cur.fetchall()

    runs: dict[str, dict[str, Any]] = {}
    for r in rows:
        run = runs.setdefault(
            r["run_id"],
            {
                "run_id": r["run_id"],
                "started_at": _iso(r["created_at"]),
                "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                "reasoning_model": r["reasoning_model_id"],
                "embedding_model": r["embedding_model_id"],
                "codebuild_project": r["codebuild_project"],
                "target_repo": r["target_repo"],
                "base_sha": r["base_sha"],
                "branches": [],
            },
        )
        # Rows arrive newest first; the earliest manifest marks the run start.
        run["started_at"] = _iso(r["created_at"])
        run["branches"].append(
            {
                "branch_id": str(r["branch_id"]),
                "strategy": r["strategy_name"],
                "hypothesis": r["hypothesis"],
                "status": r["status"],
                "score": r["score"],
                "outcome": r["outcome"],
                "passed": r["passed"],
                "total": r["total"],
                "reason": r["reason"],
                "objects": [],
            }
        )

    ordered = sorted(runs.values(), key=lambda x: x["started_at"] or "", reverse=True)[:max_runs]
    merge_rows = [
        {
            "merge_id": str(m["id"]),
            "parent_id": str(m["parent_id"]),
            "winner_id": str(m["winner_id"]),
            "merged_at": _iso(m["merged_at"]),
            "reverted_at": _iso(m["reverted_at"]),
        }
        for m in merges
    ]
    return {"runs": ordered, "merges": merge_rows}


def _list_prefix(s3: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            out.append(
                {
                    "key": obj["Key"],
                    "name": obj["Key"].rsplit("/", 1)[-1],
                    "size": obj["Size"],
                    "last_modified": _iso(obj.get("LastModified")),
                    "storage_class": obj.get("StorageClass"),
                }
            )
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def storage_inventory(root_id: str, *, max_runs: int = 8, s3: Any = None) -> dict[str, Any]:
    """The project's S3 contents, organised by run and branch."""
    index = _project_index(root_id, max_runs)
    bucket = _bucket()
    result: dict[str, Any] = {
        "bucket": bucket,
        "region": _region(),
        "runs": index["runs"],
        "applied": [],
        "totals": {"objects": 0, "bytes": 0},
        "error": None,
    }
    if not bucket:
        result["error"] = "BMEM_S3_BUCKET is not set on the server"
        return result

    if s3 is None:
        import boto3  # lazy: offline tests need no AWS SDK config

        s3 = boto3.client("s3", region_name=_region())
    try:
        for run in result["runs"]:
            objects = _list_prefix(s3, bucket, f"runs/{run['run_id']}/")
            by_branch: dict[str, list[dict[str, Any]]] = {}
            for obj in objects:
                parts = obj["key"].split("/")
                if len(parts) >= 3:
                    by_branch.setdefault(parts[2], []).append(obj)
            for branch in run["branches"]:
                branch["objects"] = sorted(
                    by_branch.get(branch["branch_id"], []), key=lambda o: o["name"]
                )
                result["totals"]["objects"] += len(branch["objects"])
                result["totals"]["bytes"] += sum(o["size"] for o in branch["objects"])
        for merge in index["merges"]:
            for obj in _list_prefix(s3, bucket, f"applied/{merge['merge_id']}/"):
                result["applied"].append({**obj, "merge_id": merge["merge_id"]})
                result["totals"]["objects"] += 1
                result["totals"]["bytes"] += obj["size"]
    except Exception as exc:  # noqa: BLE001 -- surfaced on the page, not raised
        result["error"] = f"S3: {type(exc).__name__}: {exc}"
    result["merges"] = index["merges"]
    return result


def codebuild_builds(
    root_id: str, *, max_builds: int = 100, codebuild: Any = None
) -> dict[str, Any]:
    """CodeBuild evaluation builds that belong to this project's runs."""
    index = _project_index(root_id, max_runs=50)
    run_ids = {r["run_id"] for r in index["runs"]}
    strategy_by_branch = {
        b["branch_id"]: b["strategy"] for r in index["runs"] for b in r["branches"]
    }
    project = os.environ.get("BMEM_CODEBUILD_PROJECT")
    result: dict[str, Any] = {"project": project, "region": _region(), "builds": [], "error": None}
    if not project:
        result["error"] = "BMEM_CODEBUILD_PROJECT is not set on the server"
        return result

    if codebuild is None:
        import boto3  # lazy: offline tests need no AWS SDK config

        codebuild = boto3.client("codebuild", region_name=_region())
    try:
        ids: list[str] = []
        token: str | None = None
        while len(ids) < max_builds:
            kwargs: dict[str, Any] = {"projectName": project, "sortOrder": "DESCENDING"}
            if token:
                kwargs["nextToken"] = token
            page = codebuild.list_builds_for_project(**kwargs)
            ids.extend(page.get("ids", []))
            token = page.get("nextToken")
            if not token:
                break
        ids = ids[:max_builds]
        for start in range(0, len(ids), 100):
            for build in codebuild.batch_get_builds(ids=ids[start : start + 100]).get("builds", []):
                env = {
                    v["name"]: v.get("value")
                    for v in build.get("environment", {}).get("environmentVariables", [])
                }
                if env.get("RUN_ID") not in run_ids:
                    continue
                branch_id = env.get("BRANCH_ID")
                result["builds"].append(
                    {
                        "id": build["id"],
                        "build_number": build.get("buildNumber"),
                        "status": build.get("buildStatus"),
                        "current_phase": build.get("currentPhase"),
                        "run_id": env.get("RUN_ID"),
                        "branch_id": branch_id,
                        "strategy": strategy_by_branch.get(branch_id or ""),
                        "start_time": _iso(build.get("startTime")),
                        "end_time": _iso(build.get("endTime")),
                        "phases": [
                            {
                                "phase": p.get("phaseType"),
                                "status": p.get("phaseStatus"),
                                "seconds": p.get("durationInSeconds"),
                            }
                            for p in build.get("phases", [])
                            if p.get("phaseType") not in ("COMPLETED",)
                        ],
                        "logs_url": (build.get("logs") or {}).get("deepLink"),
                        "artifact_s3": env.get("ARTIFACT_S3"),
                    }
                )
    except Exception as exc:  # noqa: BLE001 -- surfaced on the page, not raised
        result["error"] = f"CodeBuild: {type(exc).__name__}: {exc}"
    return result


def read_object(key: str, *, s3: Any = None) -> tuple[bytes, str]:
    """One of this application's own objects, for viewing in the dashboard.

    Restricted to READABLE_PREFIXES and a size cap: this is a viewer for the
    project's artifacts, not a general-purpose bucket proxy.
    """
    if not key.startswith(READABLE_PREFIXES) or ".." in key:
        raise PermissionError("only runs/ and applied/ objects can be read")
    bucket = _bucket()
    if not bucket:
        raise LookupError("BMEM_S3_BUCKET is not set on the server")
    if s3 is None:
        import boto3  # lazy: offline tests need no AWS SDK config

        s3 = boto3.client("s3", region_name=_region())
    head = s3.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] > _MAX_OBJECT_BYTES:
        raise ValueError(f"object is {head['ContentLength']:,} bytes; preview limit is 512 KB")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return body, head.get("ContentType") or "application/octet-stream"
