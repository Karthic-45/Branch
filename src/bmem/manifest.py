"""Reads the reproducibility manifest explore.py and orchestrate.py write to
``execution_manifests`` -- read-only, same role as bmem.decision but for one
branch's execution facts rather than one merge's evidence trail.
"""

from __future__ import annotations

from pydantic import BaseModel

from bmem.db import transaction

__all__ = ["ExecutionManifest", "RetrievedMemoryRef", "get_manifest"]


class RetrievedMemoryRef(BaseModel):
    key: str
    source_branch_id: str
    inherited: bool
    distance: float


class ExecutionManifest(BaseModel):
    branch_id: str
    run_id: str
    strategy_name: str
    hypothesis: str | None
    reasoning_provider: str | None
    reasoning_model_id: str | None
    explore_prompt_version: str | None
    embedding_provider: str | None
    embedding_model_id: str | None
    retrieved_memories: list[RetrievedMemoryRef]
    target_repo: str | None
    target_ref: str | None
    base_sha: str | None
    test_selector: str | None
    codebuild_project: str | None
    created_at: str | None
    updated_at: str | None


def get_manifest(branch_id: str) -> ExecutionManifest:
    with transaction() as cur:
        cur.execute(
            "SELECT * FROM execution_manifests WHERE branch_id = %(b)s",
            {"b": branch_id},
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(
            f"no execution manifest for branch {branch_id} -- explored outside a "
            "tracked run, or before this feature existed"
        )
    return ExecutionManifest(
        branch_id=str(row["branch_id"]),
        run_id=row["run_id"],
        strategy_name=row["strategy_name"],
        hypothesis=row["hypothesis"],
        reasoning_provider=row["reasoning_provider"],
        reasoning_model_id=row["reasoning_model_id"],
        explore_prompt_version=row["explore_prompt_version"],
        embedding_provider=row["embedding_provider"],
        embedding_model_id=row["embedding_model_id"],
        retrieved_memories=[
            RetrievedMemoryRef.model_validate(m) for m in (row["retrieved_memories"] or [])
        ],
        target_repo=row["target_repo"],
        target_ref=row["target_ref"],
        base_sha=row["base_sha"],
        test_selector=row["test_selector"],
        codebuild_project=row["codebuild_project"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
    )
