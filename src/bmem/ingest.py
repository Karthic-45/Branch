"""Turn a source tree into a branch's memory.

Chunking granularity is a top-level ``def`` or ``class`` -- deliberately neither
finer nor coarser. Per-file is too coarse to retrieve precisely: a search for
"how is auth checked" should surface one function, not a thousand-line module.
Per-line destroys the unit an agent actually reasons about; the worked example's
belief sentences ("Auth middleware is implemented as a Flask before_request hook
in auth/guard.py") describe whole functions, not fragments of them. A top-level
def/class is the smallest span that is still a complete thought, so it is what
becomes one row of memory.

Decorators are captured with the node they decorate rather than dropped. A
Flask-RESTX resource's route is declared entirely in its decorator
(``@api.route("/challenges")``); the class body never repeats it. Chunking the
class without its decorator would silently discard the one fact that says what
the class is for.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from bmem.db import Vector, run_transaction
from bmem.embed import Embedder

__all__ = [
    "Chunk",
    "IngestReport",
    "chunk_python_source",
    "clone_repository",
    "discover_python_files",
    "ingest_repository",
    "validate_repo_ref",
]

# The one validation choke point for a URL that ends up as a git subprocess
# argument -- mirrors db.aost_literal's role for AS OF SYSTEM TIME. subprocess
# with an argv list (never shell=True) already rules out shell metacharacter
# injection; what it does NOT rule out is git's own alternate transports,
# most notably `ext::<command>`, a documented remote-code-execution vector
# when the URL itself is attacker-controlled input. Requiring a literal
# "https://" prefix rules out ext:: (and file://, and arbitrary ssh hosts) at
# the scheme level, which is a stronger guarantee than a denylist of
# substrings would be.
_HTTPS_URL_RE = re.compile(r"^https://\S+$")
# Real git refs (branches, tags, commit SHAs) are drawn from this charset.
# Rejecting anything else stops a ref from being read as a second flag by a
# command that consumes it positionally.
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _validate_repo_url(url: str) -> None:
    if len(url) > 500 or not _HTTPS_URL_RE.fullmatch(url):
        raise ValueError(f"refusing to clone a non-https or malformed URL: {url!r}")


def _validate_ref(ref: str) -> None:
    if not _SAFE_REF_RE.fullmatch(ref):
        raise ValueError(f"refusing to use a ref with unexpected characters: {ref!r}")


def validate_repo_ref(repo_url: str, ref: str | None) -> None:
    """The same checks ``clone_repository`` applies, exposed so a caller (the
    API endpoint) can reject a bad request with a 400 before creating any
    database state, instead of finding out only once a background clone
    attempt fails."""
    _validate_repo_url(repo_url)
    if ref is not None:
        _validate_ref(ref)


def clone_repository(
    repo_url: str, ref: str | None = None, *, timeout: float = 300.0
) -> tempfile.TemporaryDirectory[str]:
    """Shallow-clone ``repo_url`` into a fresh temp directory, checked out at ``ref``.

    Caller is responsible for the context-manager cleanup (``with
    clone_repository(...) as path:``), same as any other
    ``tempfile.TemporaryDirectory``.

    ``ref`` may be a branch/tag name (plain ``--branch`` shallow clone) or a
    commit SHA -- GitHub and most modern hosts serve an arbitrary reachable
    SHA directly on request (``uploadpack.allowReachableSHA1InWant``), so a
    SHA still gets a shallow, single-commit fetch rather than the full
    history a naive "clone everything, then checkout" would require. No
    ``ref`` clones the remote's default branch.
    """
    _validate_repo_url(repo_url)
    if ref is not None:
        _validate_ref(ref)

    tmp = tempfile.TemporaryDirectory(prefix="bmem-ingest-")
    dest = tmp.name
    try:
        if ref and _SHA_RE.fullmatch(ref):
            subprocess.run(
                ["git", "init", "-q", dest],
                check=True, timeout=timeout, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", dest, "remote", "add", "origin", repo_url],
                check=True, timeout=timeout, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", dest, "fetch", "-q", "--depth", "1", "origin", ref],
                check=True, timeout=timeout, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", dest, "checkout", "-q", "FETCH_HEAD"],
                check=True, timeout=timeout, capture_output=True, text=True,
            )
        else:
            cmd = ["git", "clone", "-q", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [repo_url, dest]
            subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
    except Exception:
        tmp.cleanup()
        raise
    return tmp

_CHUNK_NODE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True, slots=True)
class Chunk:
    key: str
    content: str


def discover_python_files(root: Path) -> list[Path]:
    """Every ``.py`` file under ``root``, in a fixed order.

    Sorted rather than left in filesystem order: the demo depends on repeated
    ingests of the same tree producing the same chunks in the same order, and
    directory iteration order is not guaranteed to be stable across runs.
    """
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def chunk_python_source(relpath: str, source: str) -> list[Chunk]:
    """Split one file's source into one chunk per top-level def/class.

    Pure function -- no filesystem, no network, no database -- so this is
    exercised directly in the offline suite.

    A file that fails to parse contributes no chunks rather than raising: one
    file with a stray syntax error (or one that is not Python at all, despite
    the ``.py`` extension) should not abort ingesting the rest of the tree.
    Nested functions and methods are not split out on their own; they travel as
    part of their enclosing def/class's span.
    """
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError:
        return []

    lines = source.splitlines()
    chunks: list[Chunk] = []
    for node in tree.body:
        if not isinstance(node, _CHUNK_NODE_TYPES):
            continue
        start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        end = node.end_lineno
        if end is None:  # pragma: no cover -- always set when parsed from text
            continue
        body = "\n".join(lines[start - 1 : end])
        chunks.append(Chunk(key=f"{relpath}::{node.name}", content=f"# {relpath}\n{body}"))
    return chunks


@dataclass(frozen=True, slots=True)
class IngestReport:
    files_scanned: int
    files_skipped: list[str]
    chunks_written: int


_BATCH_SIZE = 25

# Chunks embedded per window before that window is written. Small enough that
# a failure late in a large repository loses at most one window of embedding
# work; large enough that the embedder's thread pool stays busy.
_EMBED_WINDOW = 100


def ingest_repository(
    root: Path | str,
    branch_id: str,
    *,
    embed: Embedder,
    on_progress: Callable[[int, int], None] | None = None,
) -> IngestReport:
    """Chunk every ``.py`` file under ``root`` and write it to ``branch_id``.

    Idempotent: a chunk's key is deterministic from its path and symbol name, so
    ingesting the same tree twice UPSERTs the same rows rather than duplicating
    them -- safe to retry after a partial failure, including one that stops
    partway through.

    Written in batches of :data:`_BATCH_SIZE`, each its own
    :func:`bmem.db.run_transaction` call, rather than one transaction for the
    whole tree. That was the first design, and it does not work: retrying a
    134-UPSERT transaction eight times over this project's dev link still
    failed on every attempt, because a retry re-runs the exact same slow
    operation -- if 134 sequential round trips is long enough to outlive
    CockroachDB's closed timestamp interval once, it is long enough to do so
    again, and again, regardless of retry count. Short batches fix the actual
    problem (duration) instead of papering over it with more attempts at the
    same duration. A batch failing after N others have committed leaves an
    ingest that is behind, not wrong -- the idempotent UPSERTs make re-running
    ``ingest_repository`` from scratch the correct recovery, same as before.

    Embedding happens in windows of :data:`_EMBED_WINDOW` chunks, each written
    before the next is embedded. The first version embedded the entire tree
    in one call and wrote nothing until every vector existed: on the first
    real ingest of a full repository, Bedrock throttling failed one call five
    minutes in and every vector already paid for was discarded. Chunks whose
    key already holds identical content on this branch are skipped, so
    re-running after a partial failure resumes instead of starting over.

    ``on_progress(done, total)`` fires after each window is durably written.
    """
    root = Path(root)
    files = discover_python_files(root)

    skipped: list[str] = []
    all_chunks: list[Chunk] = []
    for path in files:
        relpath = str(path.relative_to(root))
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.append(relpath)
            continue
        all_chunks.extend(chunk_python_source(relpath, source))

    if not all_chunks:
        return IngestReport(files_scanned=len(files), files_skipped=skipped, chunks_written=0)

    existing = run_transaction(lambda cur: _existing_contents(cur, branch_id))
    pending = [c for c in all_chunks if existing.get(c.key) != c.content]
    total = len(all_chunks)
    done = total - len(pending)
    if on_progress and done:
        on_progress(done, total)

    for window_start in range(0, len(pending), _EMBED_WINDOW):
        window = pending[window_start : window_start + _EMBED_WINDOW]
        vectors = embed([c.content for c in window])
        _write_window(branch_id, window, vectors)
        done += len(window)
        if on_progress:
            on_progress(done, total)

    return IngestReport(files_scanned=len(files), files_skipped=skipped, chunks_written=total)


def _existing_contents(cur: psycopg.Cursor[Any], branch_id: str) -> dict[str, str]:
    cur.execute(
        "SELECT key, content FROM memory WHERE branch_id = %(b)s AND NOT tombstone",
        {"b": branch_id},
    )
    return {r["key"]: r["content"] for r in cur.fetchall()}


def _write_window(branch_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    for start in range(0, len(chunks), _BATCH_SIZE):
        batch_chunks = chunks[start : start + _BATCH_SIZE]
        batch_vectors = vectors[start : start + _BATCH_SIZE]
        keys = [c.key for c in batch_chunks]
        contents = [c.content for c in batch_chunks]
        # Vector literals as plain strings, cast to VECTOR inside the query,
        # rather than binding an array of Vector objects: psycopg already
        # knows how to serialize a list[str] as a STRING[] array (the same
        # pattern used throughout this codebase for ANY(%(ids)s)), so this
        # reuses trusted machinery instead of a novel array-of-vectors bind.
        embeddings = [Vector(v).literal for v in batch_vectors]

        def _write(cur: psycopg.Cursor[Any], k=keys, c=contents, e=embeddings) -> None:
            # One INSERT for the whole batch, not one UPSERT per row. Batching
            # into short transactions was not, on its own, enough: even 25
            # sequential round trips against the vector index -- which is not a
            # plain B-tree, but a hierarchy of k-means-clustered partitions --
            # still hit CockroachDB's SQLSTATE 40001 on this project's dev
            # link. Collapsing a batch to one statement, as already proven for
            # the 50-way fork in tests/test_isolation.py, removes the
            # round-trip count as a variable entirely.
            cur.execute(
                """
                UPSERT INTO memory (branch_id, key, content, embedding,
                                    created_at_hlc, tombstone)
                SELECT %(b)s, t.k, t.c, t.e::VECTOR(1024), cluster_logical_timestamp(), false
                FROM unnest(%(keys)s::STRING[], %(contents)s::STRING[], %(embeddings)s::STRING[])
                    AS t(k, c, e)
                """,
                {"b": branch_id, "keys": k, "contents": c, "embeddings": e},
            )

        run_transaction(_write)
