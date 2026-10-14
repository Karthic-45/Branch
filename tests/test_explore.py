"""The explore step: a hypothesis + context -> a belief and a candidate patch.

Runs offline -- no cluster, no AWS. Mirrors test_plan.py's structure exactly;
see that file's module docstring for why ``TestClaudeExplorerWiring`` counts
as offline despite exercising real request/response code.

``explore_branch`` itself -- the part that actually touches the database -- is
covered separately in tests/test_explore_branch.py, since that needs a live
cluster.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from bmem.explore import ClaudeExplorer, Exploration, FakeExplorer, get_explorer


class TestFakeExplorer:
    def test_default_output_is_deterministic_given_the_hypothesis(self) -> None:
        result = FakeExplorer()("wrap Flask hooks as dependencies", context=[])
        assert "wrap Flask hooks as dependencies" in result.belief
        assert result.patch

    def test_fixed_exploration_is_returned_verbatim(self) -> None:
        fixed = Exploration(belief="a specific belief", patch="a specific patch")
        assert FakeExplorer(exploration=fixed)("goal", []) == fixed


class TestGetExplorer:
    def test_fake_by_default_in_tests(self) -> None:
        # conftest.py forces BMEM_FAKE_PLANNER=1 for the whole suite -- the
        # same switch plan.py's get_planner() uses, reused deliberately.
        assert isinstance(get_explorer(), FakeExplorer)

    def test_real_explorer_when_the_escape_hatch_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BMEM_FAKE_PLANNER", raising=False)
        assert isinstance(get_explorer(), ClaudeExplorer)


class _FakeMessages:
    def __init__(self, outer: _FakeAnthropicClient) -> None:
        self._outer = outer

    def create(self, **kwargs: Any) -> Any:
        self._outer.last_call = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._outer.response_text)]
        )


class _FakeAnthropicClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_call: dict[str, Any] | None = None
        self.messages = _FakeMessages(self)


def _exploration_json(belief: str, patch: str) -> str:
    return json.dumps({"belief": belief, "patch": patch})


class TestClaudeExplorerWiring:
    def test_parses_a_well_formed_response(self) -> None:
        client = _FakeAnthropicClient(_exploration_json("it works because X", "--- a diff ---"))
        explorer = ClaudeExplorer(client=client, model_id="us.anthropic.claude-sonnet-4-6")

        result = explorer("some hypothesis", ["some context"])

        assert result.belief == "it works because X"
        assert result.patch == "--- a diff ---"

    def test_sends_the_model_id_and_a_json_schema_output_config(self) -> None:
        client = _FakeAnthropicClient(_exploration_json("b", "p"))
        explorer = ClaudeExplorer(client=client, model_id="us.anthropic.claude-sonnet-4-6")

        explorer("goal", [])

        assert client.last_call["model"] == "us.anthropic.claude-sonnet-4-6"
        assert client.last_call["output_config"]["format"]["type"] == "json_schema"
        schema = client.last_call["output_config"]["format"]["schema"]
        assert {"belief", "patch"} <= schema["properties"].keys()

    def test_hypothesis_and_context_reach_the_prompt(self) -> None:
        client = _FakeAnthropicClient(_exploration_json("b", "p"))
        explorer = ClaudeExplorer(client=client, model_id="m")

        explorer("a very specific hypothesis", ["a very specific context line"])

        prompt = client.last_call["messages"][0]["content"]
        assert "a very specific hypothesis" in prompt
        assert "a very specific context line" in prompt

    def test_malformed_json_raises_rather_than_crashing_on_a_missing_field(self) -> None:
        client = _FakeAnthropicClient("not json at all")
        explorer = ClaudeExplorer(client=client, model_id="m")

        with pytest.raises(ValueError, match="did not match the exploration schema"):
            explorer("goal", [])


def test_exploration_requires_both_fields() -> None:
    with pytest.raises(ValidationError):
        Exploration(belief="only a belief")  # type: ignore[call-arg]
