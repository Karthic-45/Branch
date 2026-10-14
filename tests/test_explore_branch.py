"""explore_branch() against a real cluster: search scoping, then the write-back.

The Explorer itself is faked (FakeExplorer, via get_explorer()'s default in
tests) -- what these tests verify is everything around it: that search only
ever sees this branch's own lineage, that the belief lands under the right
key, and that a sibling's exploration is invisible, exactly as
tests/test_isolation.py already proves for hand-written memory rows.
"""

from __future__ import annotations

from typing import Any

from bmem.branch import fork
from bmem.db import transaction
from bmem.embed import FakeEmbedder
from bmem.explore import FakeExplorer, explore_branch
from bmem.plan import Strategy
from bmem.retrieval import ancestry_cache


def test_belief_is_written_under_the_strategy_name(
    root: str, four_way: dict[str, str], embedder: FakeEmbedder
) -> None:
    branch_id = four_way["codemod"]
    strategy = Strategy(name="codemod", hypothesis="AST transform with generated deps")

    result = explore_branch(branch_id, strategy, explore=FakeExplorer(), embed=embedder)

    assert result.belief

    with transaction() as cur:
        cur.execute(
            "SELECT content FROM memory WHERE branch_id = %(b)s AND key = %(k)s",
            {"b": branch_id, "k": "codemod-belief"},
        )
        row = cur.fetchone()
    assert row is not None
    assert row["content"] == result.belief


def test_explore_sees_inherited_context_from_the_parent(
    root: str, write: Any, embedder: FakeEmbedder
) -> None:
    # write, then fork -- not the four_way fixture, which forks before this
    # test body runs. A fact written to the parent after the fork point is
    # correctly invisible to the child (see test_isolation.py's
    # test_a_branch_is_immune_to_what_the_parent_concludes_later); getting the
    # order backwards here would test that immunity by accident instead of
    # inheritance.
    write(root, "auth-fact", "Auth is a before_request hook")
    with transaction() as cur:
        branch_id = fork(cur, root, "codemod")
    ancestry_cache.clear()
    seen_context: list[list[str]] = []

    def _recording_explorer(hypothesis: str, context: list[str]):
        seen_context.append(context)
        return FakeExplorer()(hypothesis, context)

    explore_branch(
        branch_id,
        Strategy(name="codemod", hypothesis="Auth is a before_request hook"),
        explore=_recording_explorer,
        embed=embedder,
    )

    assert any("Auth is a before_request hook" in c for c in seen_context[0])


def test_a_sibling_branchs_exploration_is_never_seen(
    root: str, four_way: dict[str, str], embedder: FakeEmbedder
) -> None:
    shim, codemod = four_way["shim"], four_way["codemod"]

    explore_branch(
        shim,
        Strategy(name="shim", hypothesis="wrap Flask hooks as dependencies"),
        explore=FakeExplorer(),
        embed=embedder,
    )

    seen_context: list[list[str]] = []

    def _recording_explorer(hypothesis: str, context: list[str]):
        seen_context.append(context)
        return FakeExplorer()(hypothesis, context)

    explore_branch(
        codemod,
        Strategy(name="codemod", hypothesis="AST transform with generated deps"),
        explore=_recording_explorer,
        embed=embedder,
    )

    # shim's belief content (FakeExplorer's default template embeds the
    # hypothesis verbatim: "Implemented via: wrap Flask hooks..."), not just
    # its key -- codemod's search must not surface it under any name.
    joined = " ".join(seen_context[0])
    assert "wrap Flask hooks" not in joined


def test_on_event_brackets_the_call_with_exploring_then_explored(
    root: str, four_way: dict[str, str], embedder: FakeEmbedder
) -> None:
    seen: list[tuple[str, str]] = []

    explore_branch(
        four_way["codemod"],
        Strategy(name="codemod", hypothesis="AST transform with generated deps"),
        explore=FakeExplorer(),
        embed=embedder,
        on_event=lambda stage, message: seen.append((stage, message)),
    )

    assert [stage for stage, _ in seen] == ["exploring", "explored"]
    # The second event carries the actual belief, not a generic "done" --
    # otherwise the activity feed says something happened without saying what.
    assert "AST transform with generated deps" in seen[1][1]
