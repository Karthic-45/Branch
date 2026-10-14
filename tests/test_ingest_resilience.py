"""Large-ingest resilience, with no AWS or cluster: Titan throttling is
retried with a longer budget, and ingest embeds+writes in windows so a late
failure keeps earlier work and a re-run skips what is already stored."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

import bmem.embed as embed_mod
import bmem.ingest as ingest_mod
from bmem.embed import DIMENSIONS, TitanEmbedder


class ThrottlingException(Exception):
    pass


class _FlakyClient:
    def __init__(self, failures: int, exc: type[Exception]) -> None:
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def invoke_model(self, **_: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("slow down")
        return {"body": io.BytesIO(json.dumps({"embedding": [0.0] * DIMENSIONS}).encode())}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(embed_mod.time, "sleep", lambda _s: None)


def test_throttling_gets_a_longer_retry_budget_than_other_errors() -> None:
    client = _FlakyClient(failures=7, exc=ThrottlingException)
    vec = TitanEmbedder(client=client, max_workers=1)(["x"])[0]
    assert len(vec) == DIMENSIONS
    assert client.calls == 8  # would have failed at 5 under the old budget


def test_non_throttle_errors_still_give_up_after_five_attempts() -> None:
    client = _FlakyClient(failures=99, exc=TimeoutError)
    with pytest.raises(RuntimeError, match="after 5 attempts"):
        TitanEmbedder(client=client, max_workers=1)(["x"])
    assert client.calls == 5


def test_default_worker_count_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("BMEM_EMBED_WORKERS", "2")
    assert TitanEmbedder(client=object()).max_workers == 2


class _Cursor:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store
        self._rows: list[dict[str, str]] = []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        if sql.lstrip().startswith("SELECT key, content"):
            self._rows = [{"key": k, "content": c} for k, c in self.store.items()]
        else:  # the batched UPSERT
            for k, c in zip(params["keys"], params["contents"], strict=True):
                self.store[k] = c

    def fetchall(self) -> list[dict[str, str]]:
        return self._rows


def _repo(tmp_path: Path, n: int) -> Path:
    src = "\n\n".join(f"def f{i}():\n    return {i}\n" for i in range(n))
    (tmp_path / "mod.py").write_text(src)
    return tmp_path


def test_ingest_writes_in_windows_and_keeps_work_done_before_a_failure(
    tmp_path, monkeypatch
) -> None:
    store: dict[str, str] = {}
    monkeypatch.setattr(ingest_mod, "run_transaction", lambda fn, **_: fn(_Cursor(store)))
    monkeypatch.setattr(ingest_mod, "_EMBED_WINDOW", 10)
    root = _repo(tmp_path, 35)

    calls = {"n": 0}

    def failing_embed(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("throttled for good")
        return [[0.0] * DIMENSIONS for _ in texts]

    with pytest.raises(RuntimeError):
        ingest_mod.ingest_repository(root, "b1", embed=failing_embed)
    kept = len(store)
    assert kept >= 20  # two full windows survived the third one failing

    embedded: list[int] = []
    progress: list[tuple[int, int]] = []

    def ok_embed(texts: list[str]) -> list[list[float]]:
        embedded.append(len(texts))
        return [[0.0] * DIMENSIONS for _ in texts]

    report = ingest_mod.ingest_repository(
        root, "b1", embed=ok_embed, on_progress=lambda d, t: progress.append((d, t))
    )
    total = report.chunks_written
    assert sum(embedded) == total - kept  # resumed: only the missing chunks
    assert len(store) == total
    assert progress[0] == (kept, total) and progress[-1] == (total, total)
