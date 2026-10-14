"""Deterministic structural facts about a unified diff.

Pure text analysis, no model involved and no judgement call encoded: it
counts what the diff literally says it changes. That matters because three
separate features read from it -- the tie-breaker in :mod:`bmem.orchestrate`
(which needs a defensible, reproducible discriminator when two strategies
score identically), the patch-risk summary, and the change blast radius.

The risk band is deliberately coarse and its thresholds are stated in the
code below rather than tuned: it is a size heuristic, presented as one, not
an authoritative safety verdict.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

__all__ = ["PatchStats", "analyze_patch"]

# `+++ b/path` is the post-image name git apply actually writes to. Reading
# that rather than `--- a/path` means a newly created file (whose pre-image
# is /dev/null) is still attributed to its real path.
_TARGET_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)
_SOURCE_FILE = re.compile(r"^--- (?:a/)?(.+?)\s*$", re.MULTILINE)

# Dependency and schema files are called out separately because "this patch
# touches requirements.txt" and "this patch touches one more .py file" are
# not the same kind of change to a reviewer, however similar the line counts.
_DEPENDENCY_FILES = (
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "poetry.lock", "package.json", "package-lock.json",
    "yarn.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile",
)
_SCHEMA_HINTS = ("migrations/", "migration/", "schema/", "alembic/")
_TEST_HINTS = ("test_", "_test.", "tests/", "spec/")


class PatchStats(BaseModel):
    files_changed: int
    files: list[str]
    lines_added: int
    lines_removed: int
    files_created: int
    files_deleted: int
    touches_dependencies: bool
    touches_schema: bool
    touches_tests: bool
    risk: str  # "none" | "low" | "moderate" | "high"
    risk_basis: str


def _is_dependency_file(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in _DEPENDENCY_FILES


def _matches_any(path: str, hints: tuple[str, ...]) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in hints)


def analyze_patch(diff_text: str) -> PatchStats:
    """Count what a unified diff changes. Never raises.

    A malformed or empty diff yields an all-zero result with risk "none"
    rather than an error: a strategy that produced garbage already loses on
    score, and this function's callers (a tie-breaker, a summary panel) have
    nothing useful to do with an exception.
    """
    targets = _TARGET_FILE.findall(diff_text or "")
    sources = _SOURCE_FILE.findall(diff_text or "")

    files: list[str] = []
    files_created = 0
    files_deleted = 0
    for i, target in enumerate(targets):
        source = sources[i] if i < len(sources) else ""
        if target == "/dev/null":
            files_deleted += 1
            if source and source != "/dev/null":
                files.append(source)
            continue
        if source == "/dev/null":
            files_created += 1
        files.append(target)

    # Count body lines only: a `+++`/`---` header line starts with the same
    # character as an addition/removal and would otherwise inflate both counts
    # by one per file.
    added = 0
    removed = 0
    for line in (diff_text or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1

    unique_files = sorted(set(files))
    touches_dependencies = any(_is_dependency_file(f) for f in unique_files)
    touches_schema = any(_matches_any(f, _SCHEMA_HINTS) for f in unique_files)
    touches_tests = any(_matches_any(f, _TEST_HINTS) for f in unique_files)

    risk, basis = _risk_band(
        files_changed=len(unique_files),
        churn=added + removed,
        touches_dependencies=touches_dependencies,
        touches_schema=touches_schema,
    )

    return PatchStats(
        files_changed=len(unique_files),
        files=unique_files,
        lines_added=added,
        lines_removed=removed,
        files_created=files_created,
        files_deleted=files_deleted,
        touches_dependencies=touches_dependencies,
        touches_schema=touches_schema,
        touches_tests=touches_tests,
        risk=risk,
        risk_basis=basis,
    )


def _risk_band(
    *, files_changed: int, churn: int, touches_dependencies: bool, touches_schema: bool
) -> tuple[str, str]:
    """Size-and-surface heuristic, thresholds stated inline so a reader can
    disagree with them concretely. Not a safety verdict -- see module docstring.
    """
    if files_changed == 0:
        return "none", "no files changed"
    if touches_schema:
        return "high", "changes database schema or migrations"
    if touches_dependencies:
        return "high", "changes dependency manifests"
    if files_changed > 20 or churn > 500:
        return "high", f"{files_changed} files, {churn} lines changed"
    if files_changed > 5 or churn > 120:
        return "moderate", f"{files_changed} files, {churn} lines changed"
    return "low", f"{files_changed} files, {churn} lines changed"
