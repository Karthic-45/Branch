"""The belief browser has to tell the same visibility story as retrieval.

If these two ever disagree, one of the two surfaces is lying about what a
branch believes -- so the interesting assertions here are all about the
boundaries: the fork horizon, key shadowing, and the own/inherited split that
is the copy-on-write claim in numeric form.
"""

from __future__ import annotations

from typing import Any

import pytest

from bmem.beliefs import list_beliefs
from bmem.branch import fork
from bmem.db import transaction

pytestmark = pytest.mark.cluster


def test_branch_sees_its_own_and_inherited_rows(root: str, write: Any) -> None:
    write(root, "shared", "the trunk's conclusion")
    with transaction() as cur:
        child = fork(cur, root, "child")
    write(child, "mine", "the child's own conclusion")

    page = list_beliefs(child)
    by_key = {b.key: b for b in page.beliefs}

    assert set(by_key) == {"shared", "mine"}
    assert by_key["mine"].inherited is False
    assert by_key["mine"].origin_depth == 0
    assert by_key["shared"].inherited is True
    assert by_key["shared"].origin_branch_id == root

    # The copy-on-write claim, as two numbers: two visible, one on disk here.
    assert page.total_visible == 2
    assert page.own == 1
    assert page.inherited == 1


def test_rows_written_after_the_fork_are_not_visible(root: str, write: Any) -> None:
    write(root, "before", "known at fork time")
    with transaction() as cur:
        child = fork(cur, root, "child")
    write(root, "after", "the trunk moved on without the child")

    keys = {b.key for b in list_beliefs(child).beliefs}
    assert keys == {"before"}, "a post-fork parent write leaked across the horizon"

    # The trunk itself still sees both -- the horizon is the child's, not a
    # global cutoff.
    assert {b.key for b in list_beliefs(root).beliefs} == {"before", "after"}


def test_nearest_ancestor_wins_and_the_override_is_counted(root: str, write: Any) -> None:
    write(root, "belief", "the inherited version")
    with transaction() as cur:
        child = fork(cur, root, "child")
    write(child, "belief", "the child changed its mind")

    page = list_beliefs(child)
    assert len(page.beliefs) == 1
    assert page.beliefs[0].content == "the child changed its mind"
    assert page.beliefs[0].inherited is False
    assert page.shadowed == 1, "the overridden ancestor row should still be counted"


def test_tombstoned_rows_are_hidden_but_counted(root: str, write: Any) -> None:
    write(root, "kept", "still believed")
    write(root, "retracted", "withdrawn", tombstone=True)

    page = list_beliefs(root)
    assert {b.key for b in page.beliefs} == {"kept"}
    assert page.tombstoned == 1

    with_tombstones = list_beliefs(root, include_tombstoned=True)
    assert {b.key for b in with_tombstones.beliefs} == {"kept", "retracted"}
    assert next(b for b in with_tombstones.beliefs if b.key == "retracted").tombstone is True


def test_query_filters_on_key_and_content(root: str, write: Any) -> None:
    write(root, "routing/blueprints", "blueprint registration order matters")
    write(root, "auth/session", "sessions are signed cookies")

    assert {b.key for b in list_beliefs(root, query="routing").beliefs} == {"routing/blueprints"}
    assert {b.key for b in list_beliefs(root, query="cookies").beliefs} == {"auth/session"}
    assert list_beliefs(root, query="nothing-matches-this").beliefs == []


def test_lineage_is_reported_with_horizons(root: str, write: Any) -> None:
    with transaction() as cur:
        child = fork(cur, root, "child")
        grandchild = fork(cur, child, "grandchild")

    page = list_beliefs(grandchild)
    depths = [entry["depth"] for entry in page.lineage]
    assert depths == [0, 1, 2]
    assert page.lineage[0]["branch_id"] == grandchild
    assert page.lineage[0]["horizon"] is None, "a branch sees all of its own writes"
    assert page.lineage[-1]["branch_id"] == root
    assert page.lineage[-1]["horizon"] is not None


def test_unknown_branch_raises_lookup_error(cluster: None) -> None:
    with pytest.raises(LookupError):
        list_beliefs("00000000-0000-0000-0000-000000000000")
