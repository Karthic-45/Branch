"""Whole-file repository context for the exploration step.

Memory holds one chunk per top-level function or class -- good for semantic
search, but a chunk omits the file's imports, module-level code and the lines
around it. A unified diff has to quote those surrounding lines exactly, so a
model shown only chunks either guesses (and the patch does not apply) or, as
four of eight strategies did on the first real CTFd migration, declines to
write a patch at all ("the context does not expose enough of the surrounding
files"). Search still decides *which* files matter; this module then supplies
those files whole, read from a checkout of the same ref CodeBuild tests.

Selection, in priority order, within a fixed character budget:

1. files whose path the strategy names explicitly,
2. non-test files that semantic search ranked relevant,
3. the application's entry points (``create_app``, ``Flask(``/``FastAPI(``
   constructors, ``wsgi.py``/``asgi.py``/``serve.py``), so a strategy that
   changes how the app is served can see where it is built,
4. test files search ranked relevant.

Files over the per-file cap are not truncated -- a truncated file is exactly
the partial context this exists to remove -- they fall back to their search
excerpts and the prompt says so.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ContextPlan", "SourceTree", "build_context", "checkout"]

FULL_FILE_BUDGET = 160_000  # characters, ~40k tokens: well inside the model's window
PER_FILE_CAP = 45_000
FILE_LIST_CAP = 600

_ENTRY_NAMES = {"wsgi.py", "asgi.py", "serve.py", "manage.py", "app.py", "main.py"}
_ENTRY_PATTERNS = re.compile(
    r"^\s*def create_app\b|\bFlask\(\s*__name__|\bFastAPI\(|\bStarlette\(", re.MULTILINE
)
_TEST_HINT = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$")


def _is_test(path: str) -> bool:
    return bool(_TEST_HINT.search(path))


class SourceTree:
    """Read-only view of a checked-out repository's Python files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._files = sorted(
            str(p.relative_to(root))
            for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and ".git" not in p.parts
        )
        self._entry: list[str] | None = None

    @property
    def files(self) -> list[str]:
        return self._files

    def read(self, path: str) -> str | None:
        target = (self.root / path).resolve()
        if self.root.resolve() not in target.parents or not target.is_file():
            return None  # never read outside the checkout
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def entry_points(self) -> list[str]:
        if self._entry is None:
            found: list[str] = []
            for path in self._files:
                if _is_test(path) or path.count("/") > 2:
                    continue
                name = path.rsplit("/", 1)[-1]
                text = self.read(path) or ""
                if name in _ENTRY_NAMES or _ENTRY_PATTERNS.search(text):
                    found.append(path)
            self._entry = found
        return self._entry


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """What a strategy was shown -- also reported in the run log."""

    blocks: list[str]
    full_files: list[str]
    skipped_large: list[str]


def build_context(
    hypothesis: str,
    hits: Sequence[tuple[str, str | None]],
    tree: SourceTree | None,
    *,
    budget: int = FULL_FILE_BUDGET,
    per_file_cap: int = PER_FILE_CAP,
) -> ContextPlan:
    """Assemble the prompt context from search hits ``(key, content)`` and,
    when a checkout is available, whole files.

    Without a checkout this returns the search excerpts alone -- the previous
    behaviour -- rather than failing the branch.
    """
    excerpts = [content for _, content in hits if content]
    if tree is None:
        return ContextPlan(blocks=excerpts, full_files=[], skipped_large=[])

    known = set(tree.files)
    hit_files: list[str] = []
    for key, _ in hits:
        path = key.split("::", 1)[0]
        if path in known and path not in hit_files:
            hit_files.append(path)

    mentioned = [p for p in tree.files if p in hypothesis]
    ordered: list[str] = []
    for group in (
        mentioned,
        [p for p in hit_files if not _is_test(p)],
        tree.entry_points(),
        [p for p in hit_files if _is_test(p)],
    ):
        for p in group:
            if p not in ordered:
                ordered.append(p)

    full: list[str] = []
    skipped: list[str] = []
    blocks: list[str] = []
    used = 0
    for path in ordered:
        text = tree.read(path)
        if text is None:
            continue
        if len(text) > per_file_cap:
            skipped.append(path)
            continue
        if used + len(text) > budget:
            continue
        used += len(text)
        full.append(path)
        lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        blocks.append(f"=== FULL FILE: {path} ({lines} lines, exact current contents) ===\n{text}")

    # Excerpts only for files not already shown whole.
    for key, content in hits:
        if content and key.split("::", 1)[0] not in full:
            blocks.append(f"=== EXCERPT (not the whole file): {key} ===\n{content}")

    listing = tree.files[:FILE_LIST_CAP]
    more = len(tree.files) - len(listing)
    overview = (
        f"=== REPOSITORY PYTHON FILES ({len(tree.files)}) ===\n"
        + "\n".join(listing)
        + (f"\n… and {more} more" if more > 0 else "")
    )
    note = ""
    if skipped:
        note = (
            "\n\nThese relevant files are too large to include whole and appear only as "
            "excerpts; do not edit them: " + ", ".join(skipped)
        )
    return ContextPlan(blocks=[overview + note, *blocks], full_files=full, skipped_large=skipped)


def checkout(repo_url: str, ref: str | None) -> tempfile.TemporaryDirectory[str]:
    """Shallow checkout of the ref the evaluation will test."""
    from bmem.ingest import clone_repository  # lazy: keeps this module import-light

    return clone_repository(repo_url, ref or None)
