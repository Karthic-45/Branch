"""The recursive CTEs that walk branch relationships in both directions.

`tests/test_visibility.py` tests what the horizons *mean* using fabricated
scopes. This tests that the SQL actually produces them, against real fork points
issued by the cluster's hybrid logical clock -- resolve_ancestry (child ->
parent) and resolve_subtree (parent -> children) alike.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bmem.branch import fork, resolve_ancestry, resolve_subtree
from bmem.db import transaction
from bmem.retrieval import ancestry_cache, search


def test_root_has_a_single_unbounded_scope(root: str) -> None:
    with transaction() as cur:
        scopes = resolve_ancestry(cur, root)

    assert len(scopes) == 1
    assert scopes[0].branch_id == root
    assert scopes[0].horizon is None  # sees everything it wrote
    assert scopes[0].depth == 0


def test_three_deep_chain_tightens_horizons_going_up(root: str) -> None:
    with transaction() as cur:
        feature = fork(cur, root, "feature")
    with transaction() as cur:
        deep = fork(cur, feature, "deep")

    with transaction() as cur:
        scopes = resolve_ancestry(cur, deep)
        cur.execute(
            "SELECT id, fork_point FROM branches WHERE id = ANY(%(ids)s)",
            {"ids": [feature, deep]},
        )
        fork_points = {str(r["id"]): r["fork_point"] for r in cur.fetchall()}

    assert [s.branch_id for s in scopes] == [deep, feature, root]
    assert [s.depth for s in scopes] == [0, 1, 2]

    # The branch itself is unbounded.
    assert scopes[0].horizon is None
    # `feature` is visible only as it stood when `deep` forked from it.
    assert Decimal(scopes[1].horizon) == fork_points[deep]
    # `root` is visible only as `feature` saw it -- i.e. at feature's own fork.
    assert Decimal(scopes[2].horizon) == fork_points[feature]

    # Horizons strictly tighten with distance.
    assert Decimal(scopes[2].horizon) < Decimal(scopes[1].horizon)


def test_siblings_do_not_appear_in_each_others_ancestry(root: str) -> None:
    with transaction() as cur:
        a = fork(cur, root, "a")
        b = fork(cur, root, "b")

    with transaction() as cur:
        assert {s.branch_id for s in resolve_ancestry(cur, a)} == {a, root}
        assert {s.branch_id for s in resolve_ancestry(cur, b)} == {b, root}


def test_deep_chain_inherits_transitively_through_every_level(
    root: str, write: Any, embedder: Any
) -> None:
    """A grandchild reads what the grandparent knew before any of the forks."""
    write(root, "grandparent-fact", "Established at the very beginning")
    with transaction() as cur:
        feature = fork(cur, root, "feature")
    write(feature, "parent-fact", "Established in the middle")
    with transaction() as cur:
        deep = fork(cur, feature, "deep")
    write(deep, "own-fact", "Established at the bottom")

    ancestry_cache.clear()
    with transaction() as cur:
        keys = {
            label: {h.key for h in search(cur, deep, embedder([text])[0], k=8)}
            for label, text in [
                ("g", "Established at the very beginning"),
                ("p", "Established in the middle"),
                ("o", "Established at the bottom"),
            ]
        }

    assert "grandparent-fact" in keys["g"]
    assert "parent-fact" in keys["p"]
    assert "own-fact" in keys["o"]


def test_writes_after_an_intermediate_fork_do_not_reach_the_grandchild(
    root: str, write: Any, embedder: Any
) -> None:
    """The horizon on a distant ancestor is the *nearest* fork on the path."""
    with transaction() as cur:
        feature = fork(cur, root, "feature")
    with transaction() as cur:
        deep = fork(cur, feature, "deep")

    # Written to root after `feature` already forked away from it.
    write(root, "too-late", "Root concluded this after feature split off")
    # Written to feature after `deep` already forked away from it.
    write(feature, "also-too-late", "Feature concluded this after deep split off")

    ancestry_cache.clear()
    with transaction() as cur:
        a = {
            h.key
            for h in search(
                cur, deep, embedder(["Root concluded this after feature split off"])[0], k=8
            )
        }
        b = {
            h.key
            for h in search(
                cur, deep, embedder(["Feature concluded this after deep split off"])[0], k=8
            )
        }

    assert "too-late" not in a
    assert "also-too-late" not in b


def test_ancestry_is_stable_and_therefore_safe_to_cache(root: str) -> None:
    with transaction() as cur:
        child = fork(cur, root, "child")
        first = resolve_ancestry(cur, child)

    # Activity elsewhere in the tree must not perturb an existing lineage.
    with transaction() as cur:
        fork(cur, root, "unrelated-sibling")
    with transaction() as cur:
        second = resolve_ancestry(cur, child)

    assert first == second


def test_subtree_includes_the_root_and_every_descendant(root: str) -> None:
    with transaction() as cur:
        child = fork(cur, root, "child")
        grandchild = fork(cur, child, "grandchild")
        sibling = fork(cur, root, "sibling")
        nodes = resolve_subtree(cur, root)

    ids = {n.id for n in nodes}
    assert ids == {root, child, grandchild, sibling}


def test_subtree_depth_matches_distance_from_root(root: str) -> None:
    with transaction() as cur:
        child = fork(cur, root, "child")
        grandchild = fork(cur, child, "grandchild")
        nodes = resolve_subtree(cur, root)

    by_id = {n.id: n for n in nodes}
    assert by_id[root].depth == 0
    assert by_id[child].depth == 1
    assert by_id[grandchild].depth == 2


def test_subtree_excludes_a_sibling_lineage_outside_the_root(root: str) -> None:
    with transaction() as cur:
        unrelated_root = fork(cur, root, "unrelated")  # still under root, but...
        fork(cur, unrelated_root, "unrelated-child")
        real_child = fork(cur, root, "real-child")
        # Query the subtree from real_child, not root -- unrelated_root's
        # lineage must not appear, since it is real_child's sibling, not its
        # ancestor or descendant.
        nodes = resolve_subtree(cur, real_child)

    assert {n.id for n in nodes} == {real_child}
