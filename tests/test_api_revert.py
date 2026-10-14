"""The revert flow as the demo drives it: over HTTP, against a real cluster.

`tests/test_merge_revert.py` covers what revert does to the store. This covers
the surface in front of it -- that the merge log is fetchable, that the button
undoes the merge, and that the failure modes come back as status codes a UI can
show rather than a 500.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bmem.api import app
from bmem.branch import merge
from bmem.db import transaction

pytestmark = pytest.mark.cluster

client = TestClient(app)


def _parent_keys(parent_id: str) -> set[str]:
    with transaction() as cur:
        cur.execute(
            "SELECT key FROM memory WHERE branch_id = %(b)s AND tombstone = false",
            {"b": parent_id},
        )
        return {r["key"] for r in cur.fetchall()}


def test_merge_log_lists_the_merge_with_its_winner(
    four_way: dict[str, str], root: str, write: Any
) -> None:
    write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")
    merge_id = merge(root, four_way["codemod"])

    response = client.get(f"/api/merges/{root}")
    assert response.status_code == 200

    rows = response.json()
    assert [r["merge_id"] for r in rows] == [merge_id]
    assert rows[0]["winner_id"] == four_way["codemod"]
    assert rows[0]["reverted_at"] is None


def test_revert_restores_the_parents_pre_merge_beliefs(
    four_way: dict[str, str], root: str, write: Any
) -> None:
    write(root, "auth-fact", "Auth is a before_request hook")
    write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")

    before = _parent_keys(root)
    merge_id = merge(root, four_way["codemod"])
    assert "codemod-plan" in _parent_keys(root)  # the merge really landed

    response = client.post(f"/api/merges/{merge_id}/revert", json={})
    assert response.status_code == 200
    assert response.json()["promoted_merge_id"] is None

    assert _parent_keys(root) == before


def test_reverting_twice_is_a_conflict_not_a_silent_no_op(
    four_way: dict[str, str], root: str, write: Any
) -> None:
    """A double-click on the demo's revert button must not quietly do nothing."""
    write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")
    merge_id = merge(root, four_way["codemod"])

    assert client.post(f"/api/merges/{merge_id}/revert", json={}).status_code == 200
    second = client.post(f"/api/merges/{merge_id}/revert", json={})
    assert second.status_code == 409
    assert "already been reverted" in second.json()["detail"]


def test_reverting_an_unknown_merge_is_a_404(cluster: None) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    response = client.post(f"/api/merges/{unknown}/revert", json={})
    assert response.status_code == 404


def test_revert_can_promote_a_sibling_in_the_winners_place(
    four_way: dict[str, str], root: str, write: Any
) -> None:
    """The interesting case: un-conclude one answer and adopt a different one."""
    write(four_way["codemod"], "codemod-plan", "AST transform with generated deps")
    write(four_way["shim"], "shim-plan", "Mount the WSGI app under FastAPI")
    merge_id = merge(root, four_way["codemod"])

    response = client.post(f"/api/merges/{merge_id}/revert", json={"promote": four_way["shim"]})
    assert response.status_code == 200
    assert response.json()["promoted_merge_id"] is not None

    # The parent now holds the shim's conclusion and not the codemod's.
    keys = _parent_keys(root)
    assert "shim-plan" in keys
    assert "codemod-plan" not in keys

    with transaction() as cur:
        cur.execute(
            "SELECT id, status FROM branches WHERE parent_id = %(p)s",
            {"p": root},
        )
        status = {str(r["id"]): r["status"] for r in cur.fetchall()}

    assert status[four_way["shim"]] == "merged"
    assert status[four_way["codemod"]] == "abandoned"
