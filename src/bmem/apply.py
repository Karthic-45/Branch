"""Turn a winning branch's patch into a real, isolated, validated change --
the step between "the memory layer picked a winner" and "someone can act on
it without manually recreating what it did."

Everything here runs against a throwaway clone in a temp directory and never
touches the live repository directly, never force-pushes, never opens a PR:
this project has no GitHub credential configured, so "create a real PR" stops
at a local commit plus a downloadable bundle rather than silently doing
nothing or, worse, faking success. See api.py's docstring on the apply
endpoint for the exact boundary.

Same swappable-nothing shape as evaluate.py in one respect: this is real
subprocess/git/S3 work with no offline stand-in, because unlike Bedrock or
CodeBuild there is no meaningful fake for "did this patch actually apply
cleanly to a real checkout" -- a fake would test nothing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from bmem.db import transaction
from bmem.ingest import clone_repository

__all__ = [
    "ApplyResult",
    "ValidationProfile",
    "ValidationStep",
    "apply_winning_patch",
]


class ValidationStep(BaseModel):
    name: str
    command: str
    status: str  # "passed" | "failed" | "error"
    output: str


class ValidationProfile(BaseModel):
    """Per-repo commands, stored on the root branch (branches.validation_profile,
    see schema/006_apply_engine.sql) and set via PUT /api/repos/{root_id}/validation-profile.
    A field left None means "not configured" -- the apply engine must never
    guess a toolchain for a repository it wasn't told about; an unconfigured
    profile validates nothing and says so, rather than assuming pytest.
    """

    install: str | None = None
    lint: str | None = None
    typecheck: str | None = None
    build: str | None = None
    test: str | None = None


class ApplyResult(BaseModel):
    merge_id: str
    base_sha: str | None = None
    current_head_sha: str | None = None
    base_changed: bool = False
    branch: str | None = None
    patch_applied: bool = False
    conflicts: list[str] = []
    #: Why the patch did not apply, when it did not. "malformed" means the
    #: stored artifact was never a unified diff; "conflict" means it was one
    #: and the tree rejected it. Conflating the two sends a reader hunting for
    #: a merge problem that does not exist.
    patch_failure: str | None = None
    #: Files git named as conflicting, when it named any. Empty for a
    #: malformed artifact -- there is no file to point at.
    conflicting_files: list[str] = []
    validation_status: str = "skipped"  # base_changed | patch_failed | skipped | passed | failed
    validation_steps: list[ValidationStep] = []
    commit_sha: str | None = None
    applied_tree_s3_uri: str | None = None


class ApplyError(LookupError):
    pass


def _lookup_merge(merge_id: str) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute(
            """
            SELECT m.winner_id, m.parent_id, b.hypothesis
            FROM merge_log m JOIN branches b ON b.id = m.winner_id
            WHERE m.id = %(id)s
            """,
            {"id": merge_id},
        )
        row = cur.fetchone()
        if row is None:
            raise ApplyError(f"no such merge: {merge_id}")

        cur.execute(
            "SELECT s3_uri, base_sha FROM memory "
            "WHERE branch_id = %(b)s AND s3_uri IS NOT NULL LIMIT 1",
            {"b": row["winner_id"]},
        )
        patch_row = cur.fetchone()
        if patch_row is None or not patch_row["s3_uri"]:
            raise ApplyError(
                f"no patch on record for merge {merge_id} -- merged before patch tracking "
                "existed, or scored with a fake evaluator"
            )

        # Only a root (parent_id IS NULL) ever has source_repo set -- walk up
        # to it rather than assuming the immediate parent is the root.
        cur.execute(
            """
            WITH RECURSIVE ancestry AS (
                SELECT id, parent_id, source_repo, source_ref, validation_profile
                FROM branches WHERE id = %(p)s
                UNION ALL
                SELECT b.id, b.parent_id, b.source_repo, b.source_ref, b.validation_profile
                FROM branches b JOIN ancestry a ON a.parent_id = b.id
            )
            SELECT source_repo, source_ref, validation_profile
            FROM ancestry WHERE parent_id IS NULL
            """,
            {"p": row["parent_id"]},
        )
        root_row = cur.fetchone()

    if root_row is None or not root_row["source_repo"]:
        raise ApplyError(f"merge {merge_id}'s lineage has no ingested source repository")

    return {
        "hypothesis": row["hypothesis"],
        "s3_uri": patch_row["s3_uri"],
        "base_sha": patch_row["base_sha"],
        "source_repo": root_row["source_repo"],
        "source_ref": root_row["source_ref"],
        "validation_profile": root_row["validation_profile"],
    }


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _run_validation(
    work_dir: Path, profile: ValidationProfile, *, timeout: float
) -> list[ValidationStep]:
    # Cheapest/fastest checks first, so a broken patch fails fast rather than
    # waiting for a full test run to say what lint would have said in a second.
    steps: list[ValidationStep] = []
    for name, command in (
        ("install", profile.install),
        ("lint", profile.lint),
        ("typecheck", profile.typecheck),
        ("build", profile.build),
        ("test", profile.test),
    ):
        if not command:
            continue
        try:
            proc = subprocess.run(
                shlex.split(command),
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            status = "passed" if proc.returncode == 0 else "failed"
            output = (proc.stdout + proc.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            status, output = "error", f"timed out after {timeout}s"
        except FileNotFoundError as exc:
            status, output = "error", str(exc)
        steps.append(ValidationStep(name=name, command=command, status=status, output=output))
    return steps


def _persist_applied_tree(work_dir: Path, merge_id: str, s3: Any, bucket: str | None) -> str | None:
    """Bundle the applied commit (a self-contained, clonable git object) and
    upload it -- the temp directory it lives in is gone the moment this
    function's caller exits its `with` block, and a FastAPI worker thread
    restarting shouldn't mean the applied change is unrecoverable.
    """
    if not bucket:
        return None
    bundle_path = work_dir.parent / f"{merge_id}.bundle"
    proc = subprocess.run(
        ["git", "-C", str(work_dir), "bundle", "create", str(bundle_path), "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    key = f"applied/{merge_id}/applied.bundle"
    s3.put_object(Bucket=bucket, Key=key, Body=bundle_path.read_bytes())
    bundle_path.unlink()
    return f"s3://{bucket}/{key}"


def _persist_result(result: ApplyResult) -> None:
    from bmem.db import run_transaction

    def _write(cur: Any) -> None:
        cur.execute(
            """
            INSERT INTO decision_applications
                (merge_id, base_sha, current_head_sha, base_changed, branch, patch_applied,
                 conflicts, validation_status, validation_steps, commit_sha, applied_tree_s3_uri)
            VALUES
                (%(merge_id)s, %(base_sha)s, %(current_head_sha)s, %(base_changed)s, %(branch)s,
                 %(patch_applied)s, %(conflicts)s, %(validation_status)s, %(validation_steps)s,
                 %(commit_sha)s, %(applied_tree_s3_uri)s)
            """,
            {
                "merge_id": result.merge_id,
                "base_sha": result.base_sha,
                "current_head_sha": result.current_head_sha,
                "base_changed": result.base_changed,
                "branch": result.branch,
                "patch_applied": result.patch_applied,
                "conflicts": json.dumps(result.conflicts),
                "validation_status": result.validation_status,
                "validation_steps": json.dumps([s.model_dump() for s in result.validation_steps]),
                "commit_sha": result.commit_sha,
                "applied_tree_s3_uri": result.applied_tree_s3_uri,
            },
        )

    run_transaction(_write)


# git apply reports a malformed input and a genuine conflict through the same
# non-zero exit code, so the distinction has to come from the message. These
# are git's own strings, matched narrowly: anything unrecognised stays
# "unknown" rather than being forced into whichever bucket looks close.
_MALFORMED = re.compile(r"No valid patches in input|unrecognized input|corrupt patch")
_CONFLICT_FILE = re.compile(r"error: (?:patch failed: )?([^\s:]+)(?::\d+)?")


def _classify_apply_failure(stderr: str) -> tuple[str, list[str]]:
    """Say why the patch did not apply, and name files only when git named them."""
    if _MALFORMED.search(stderr):
        # An artifact that is not a diff has no conflicting file to report.
        return "malformed", []

    files: list[str] = []
    for line in stderr.splitlines():
        match = _CONFLICT_FILE.match(line.strip())
        if match and match.group(1) not in files:
            files.append(match.group(1))
    return ("conflict" if files else "unknown"), files


def apply_winning_patch(
    merge_id: str,
    *,
    validate: bool = True,
    allow_base_changed: bool = False,
    timeout: float = 300.0,
) -> ApplyResult:
    """Apply the merge's winning patch to an isolated clone, optionally
    validate it, and commit locally if validation didn't fail outright.

    Never touches the ingested repository's real remote. ``base_changed``
    mirrors the "Repository State Protection" contract: if the commit this
    patch was actually evaluated against no longer matches the repo's current
    head, applying blind would validate against a codebase nobody scored --
    the caller has to pass ``allow_base_changed=True`` to proceed anyway,
    same as choosing "attempt application" over "cancel" in the UI.
    """
    info = _lookup_merge(merge_id)

    import boto3  # lazy: offline tests and callers without AWS config never reach here

    bucket = os.environ.get("BMEM_S3_BUCKET")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    patch_bucket, _, patch_key = info["s3_uri"].removeprefix("s3://").partition("/")
    patch_text = s3.get_object(Bucket=patch_bucket, Key=patch_key)["Body"].read().decode()

    recorded_base_sha = info["base_sha"]
    result = ApplyResult(merge_id=merge_id, base_sha=recorded_base_sha)

    clone_ref = recorded_base_sha or info["source_ref"]
    with clone_repository(info["source_repo"], clone_ref) as clone_dir:
        work_dir = Path(clone_dir)
        current_head = _run(work_dir, "rev-parse", "HEAD").stdout.strip()
        result.current_head_sha = current_head
        result.base_changed = bool(recorded_base_sha) and current_head != recorded_base_sha

        if result.base_changed and not allow_base_changed:
            result.validation_status = "base_changed"
            _persist_result(result)
            return result

        branch_name = f"bmem/{merge_id[:8]}"
        _run(work_dir, "checkout", "-q", "-b", branch_name)
        result.branch = branch_name

        patch_file = work_dir / ".bmem-candidate.patch"
        patch_file.write_text(patch_text)
        apply_proc = subprocess.run(
            # --recount: model-written diffs routinely carry wrong line counts
            # in their @@ hunk headers ("corrupt patch at line N") while the
            # body itself is fine. Same flag the CodeBuild evaluation uses, so
            # a patch that scored there applies here too.
            ["git", "-C", str(work_dir), "apply", "--recount", "--verbose", str(patch_file)],
            capture_output=True,
            text=True,
        )
        patch_file.unlink()
        result.patch_applied = apply_proc.returncode == 0

        if not result.patch_applied:
            result.conflicts = [
                line for line in apply_proc.stderr.splitlines() if line.strip()
            ]
            result.patch_failure, result.conflicting_files = _classify_apply_failure(
                apply_proc.stderr
            )
            result.validation_status = "patch_failed"
            _persist_result(result)
            return result

        if validate:
            profile = ValidationProfile.model_validate(info["validation_profile"] or {})
            result.validation_steps = _run_validation(work_dir, profile, timeout=timeout)
            if not result.validation_steps:
                result.validation_status = "skipped"
            elif all(s.status == "passed" for s in result.validation_steps):
                result.validation_status = "passed"
            else:
                result.validation_status = "failed"
        else:
            result.validation_status = "skipped"

        if result.validation_status in ("passed", "skipped"):
            _run(work_dir, "add", "-A")
            _run(
                work_dir,
                "-c", "user.email=bmem@localhost",
                "-c", "user.name=Branchable Memory",
                "commit", "-q", "-m",
                f"{info['hypothesis'] or 'Apply winning strategy'}\n\n"
                f"Applied via Branchable Memory decision {merge_id}.",
            )
            result.commit_sha = _run(work_dir, "rev-parse", "HEAD").stdout.strip()
            result.applied_tree_s3_uri = _persist_applied_tree(work_dir, merge_id, s3, bucket)

    _persist_result(result)
    return result
