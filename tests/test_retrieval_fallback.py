"""search() must not leave a branch with no context when the approximate
vector index comes back short. No cluster needed: the cursor is a stand-in
that answers the ANN query and the exact query differently."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

import bmem.retrieval as retrieval
from bmem.branch import AncestorScope


def _row(key: str, distance: float) -> dict[str, Any]:
    return {
        "branch_id": "root",
        "key": key,
        "content": f"code of {key}",
        "created_at_hlc": Decimal("1"),
        "tombstone": False,
        "distance": distance,
    }


class _Cursor:
    def __init__(
        self,
        ann_rows: list[dict[str, Any]],
        exact_rows: list[dict[str, Any]],
        beam: str | None = "32",
    ) -> None:
        self.ann_rows = ann_rows
        self.exact_rows = exact_rows
        self.beam = beam
        self.statements: list[str] = []
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.statements.append(sql)
        if "current_setting" in sql:
            self._result = [{"v": self.beam}]
        elif sql.startswith("SET LOCAL"):
            self._result = []
        elif "memory@primary" in sql:
            self._result = self.exact_rows
        else:
            self._result = self.ann_rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._result


@pytest.fixture(autouse=True)
def _lineage(monkeypatch):
    scopes = [
        AncestorScope(branch_id="child", horizon=None, depth=0),
        AncestorScope(branch_id="root", horizon="10", depth=1),
    ]
    monkeypatch.setattr(retrieval.ancestry_cache, "get", lambda cur, b: scopes)


def test_empty_index_result_falls_back_to_exact_scan() -> None:
    cur = _Cursor(ann_rows=[], exact_rows=[_row(f"f{i}", 0.5 + i / 100) for i in range(8)])
    hits = retrieval.search(cur, "child", [0.0], k=8)
    assert [h.key for h in hits] == [f"f{i}" for i in range(8)]
    assert any("memory@primary" in s for s in cur.statements)


def test_full_index_result_skips_the_exact_scan() -> None:
    rows = [_row(f"f{i}", 0.5) for i in range(8)]
    cur = _Cursor(ann_rows=rows, exact_rows=[])
    assert len(retrieval.search(cur, "child", [0.0], k=8)) == 8
    assert not any("memory@primary" in s for s in cur.statements)


def test_beam_is_widened_only_when_the_server_supports_it() -> None:
    cur = _Cursor(ann_rows=[_row("a", 0.1)] * 1, exact_rows=[], beam=None)
    retrieval.search(cur, "child", [0.0], k=1, mode="ann")
    assert not any(s.startswith("SET LOCAL") for s in cur.statements)
    cur = _Cursor(ann_rows=[_row("a", 0.1)], exact_rows=[], beam="32")
    retrieval.search(cur, "child", [0.0], k=1, mode="ann")
    assert any(s.startswith("SET LOCAL vector_search_beam_size") for s in cur.statements)


def test_exact_query_keeps_the_lineage_filter() -> None:
    sql = retrieval._build_query(
        [AncestorScope(branch_id="child", horizon=None, depth=0)], exact=True
    )
    assert "FROM memory@primary" in sql
    assert "WHERE branch_id IN (%(b0)s)" in sql
