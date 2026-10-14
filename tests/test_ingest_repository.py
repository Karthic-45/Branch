"""Ingesting a real tree into a real branch's memory."""

from __future__ import annotations

from pathlib import Path

from bmem.branch import fork
from bmem.db import transaction
from bmem.embed import FakeEmbedder
from bmem.ingest import ingest_repository


def _write_tree(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "guard.py").write_text(
        "def before_request():\n    return authed()\n\n\ndef teardown():\n    return None\n"
    )
    (pkg / "models.py").write_text("class User:\n    def is_admin(self):\n        return False\n")
    (pkg / "__init__.py").write_text("from .guard import before_request\n")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "guard.cpython-311.pyc").write_bytes(b"\x00\x01")  # must be skipped, not parsed
    return pkg


def _rows_for(branch_id: str) -> dict[str, str]:
    with transaction() as cur:
        cur.execute("SELECT key, content FROM memory WHERE branch_id = %(b)s", {"b": branch_id})
        return {row["key"]: row["content"] for row in cur.fetchall()}


def test_ingest_writes_one_row_per_chunk(root: str, tmp_path: Path, embedder: FakeEmbedder) -> None:
    pkg = _write_tree(tmp_path)
    report = ingest_repository(pkg, root, embed=embedder)

    # guard.py, models.py, __init__.py -- the __pycache__ .pyc is not a .py file
    # and is never counted, let alone scanned.
    assert report.files_scanned == 3
    assert report.chunks_written == 3  # before_request, teardown, User -- __init__.py has none
    assert set(_rows_for(root)) == {
        "guard.py::before_request",
        "guard.py::teardown",
        "models.py::User",
    }


def test_ingested_content_is_readable_from_the_written_row(
    root: str, tmp_path: Path, embedder: FakeEmbedder
) -> None:
    pkg = _write_tree(tmp_path)
    ingest_repository(pkg, root, embed=embedder)

    content = _rows_for(root)["guard.py::before_request"]
    assert "def before_request():" in content
    assert content.startswith("# guard.py\n")


def test_ingesting_the_same_tree_twice_does_not_duplicate_rows(
    root: str, tmp_path: Path, embedder: FakeEmbedder
) -> None:
    pkg = _write_tree(tmp_path)
    ingest_repository(pkg, root, embed=embedder)
    second = ingest_repository(pkg, root, embed=embedder)

    assert second.chunks_written == 3
    assert len(_rows_for(root)) == 3  # UPSERT, not INSERT -- re-ingest overwrites, not appends


def test_a_file_that_is_not_valid_utf8_is_skipped_not_fatal(
    root: str, tmp_path: Path, embedder: FakeEmbedder
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "good.py").write_text("def f():\n    pass\n")
    (pkg / "bad.py").write_bytes(b"\xff\xfe\x00 not utf-8 at all")

    report = ingest_repository(pkg, root, embed=embedder)

    assert report.files_skipped == ["bad.py"]
    assert report.chunks_written == 1
    assert set(_rows_for(root)) == {"good.py::f"}


def test_a_chunk_written_on_a_child_branch_does_not_appear_on_its_parent(
    root: str, tmp_path: Path, embedder: FakeEmbedder
) -> None:
    pkg = _write_tree(tmp_path)
    with transaction() as cur:
        child = fork(cur, root, "child")

    ingest_repository(pkg, child, embed=embedder)

    assert _rows_for(root) == {}
    assert len(_rows_for(child)) == 3
