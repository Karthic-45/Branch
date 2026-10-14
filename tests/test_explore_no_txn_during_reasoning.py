"""explore_branch must never hold a database transaction open across the
model call. Runs without a cluster: run_transaction and search are replaced
with stand-ins that record whether a transaction is open at each moment.

Regression for the first real-Bedrock run, where the multi-minute Claude call
sat inside a serializable CockroachDB transaction, so every commit-time retry
re-ran (and re-paid for) the whole call.
"""

from __future__ import annotations

from typing import Any

import bmem.explore as explore_mod
from bmem.explore import Exploration, explore_branch
from bmem.plan import Strategy


class _Recorder:
    def __init__(self) -> None:
        self.open = False
        self.transactions = 0
        self.model_calls_inside_txn = 0
        self.embed_calls_inside_txn = 0

    def run_transaction(self, fn: Any, **_: Any) -> Any:
        self.transactions += 1
        self.open = True
        try:
            return fn(_FakeCursor())
        finally:
            self.open = False


class _FakeCursor:
    def execute(self, *_: Any, **__: Any) -> None:
        pass


def test_model_and_embedding_calls_happen_outside_any_transaction(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(explore_mod, "run_transaction", rec.run_transaction)
    monkeypatch.setattr(explore_mod, "search", lambda cur, branch_id, vec, k=8: [])

    def embed(texts: list[str]) -> list[list[float]]:
        if rec.open:
            rec.embed_calls_inside_txn += 1
        return [[0.0, 1.0] for _ in texts]

    def explore(hypothesis: str, context: list[str]) -> Exploration:
        if rec.open:
            rec.model_calls_inside_txn += 1
        return Exploration(belief=f"did {hypothesis}", patch="diff")

    events: list[str] = []
    result = explore_branch(
        "branch-1",
        Strategy(name="codemod", hypothesis="rewrite routes"),
        explore=explore,
        embed=embed,
        on_event=lambda stage, _msg: events.append(stage),
    )

    assert result.belief == "did rewrite routes"
    assert rec.model_calls_inside_txn == 0
    assert rec.embed_calls_inside_txn == 0
    assert rec.transactions == 2  # one short read, one short write
    assert events == ["exploring", "explored"]
