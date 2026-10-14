"""One branch failing must not fail the whole migration run. No cluster
needed: the DB write and the exploration step are replaced with stand-ins."""

from __future__ import annotations

import bmem.orchestrate as orch
from bmem.plan import Strategy


def test_a_crashing_branch_becomes_a_scored_loser(monkeypatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(orch, "run_transaction", lambda fn, **_: writes.append("score=0"))

    def boom(*_a, **_k):
        raise ValueError("Claude's patch exceeded the 16,000-token output limit and was cut off")

    monkeypatch.setattr(orch, "explore_branch", boom)
    events: list[tuple[str, str]] = []

    outcome = orch._explore_and_evaluate(
        "b1",
        Strategy(name="rewrite", hypothesis="rewrite it"),
        "run1",
        explore=None,
        evaluate=None,
        embed=None,
        on_event=lambda stage, msg: events.append((stage, msg)),
    )

    assert outcome.evaluation.score == 0.0
    assert outcome.evaluation.reason.startswith(
        "branch_failed: ValueError: Claude's patch exceeded"
    )
    assert outcome.exploration.patch == ""
    assert writes == ["score=0"]
    assert events and events[-1][0] == "scored"


def test_explorer_reports_truncation_clearly() -> None:
    from types import SimpleNamespace

    from bmem.explore import EXPLORE_MAX_TOKENS, ClaudeExplorer

    class _Client:
        class messages:  # noqa: N801 -- mirrors the SDK attribute
            @staticmethod
            def create(**kwargs):
                assert kwargs["max_tokens"] == EXPLORE_MAX_TOKENS
                return SimpleNamespace(
                    stop_reason="max_tokens",
                    content=[
                        SimpleNamespace(type="text", text='{"belief": "x", "patch": "diff --git')
                    ],
                )

    import pytest

    with pytest.raises(ValueError, match="output limit"):
        ClaudeExplorer(client=_Client())("hyp", [])
