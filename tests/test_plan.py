"""The planning step: goal + context -> N distinct migration strategies.

Runs offline -- no cluster, no AWS. ``TestClaudePlannerWiring`` is the
exception in spirit, not in fact: it injects a fake client shaped like the
real Anthropic SDK response, so the request-construction and response-parsing
code that will talk to real Bedrock is exercised now, before that account
access exists, rather than left completely unverified until it does.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from bmem.plan import ClaudePlanner, FakePlanner, Strategy, get_planner


class TestFakePlanner:
    def test_default_output_has_n_distinct_strategies(self) -> None:
        strategies = FakePlanner()("migrate off Flask", context=[], n=4)
        assert len(strategies) == 4
        assert len({s.name for s in strategies}) == 4

    def test_respects_custom_n(self) -> None:
        assert len(FakePlanner()("goal", [], n=2)) == 2

    def test_fixed_strategies_are_returned_verbatim(self) -> None:
        fixed = [
            Strategy(name="shim", hypothesis="wrap it"),
            Strategy(name="codemod", hypothesis="rewrite it"),
        ]
        result = FakePlanner(strategies=fixed)("goal", [], n=2)
        assert result == fixed

    def test_fixed_strategies_are_sliced_to_n(self) -> None:
        fixed = [Strategy(name=f"s{i}", hypothesis="h") for i in range(5)]
        assert len(FakePlanner(strategies=fixed)("goal", [], n=3)) == 3

    def test_duplicate_fixed_names_are_rejected(self) -> None:
        dupes = [Strategy(name="same", hypothesis="a"), Strategy(name="same", hypothesis="b")]
        with pytest.raises(ValueError, match="duplicate"):
            FakePlanner(strategies=dupes)("goal", [], n=2)


class TestGetPlanner:
    def test_fake_by_default_in_tests(self) -> None:
        # conftest.py's pytest_configure forces BMEM_FAKE_PLANNER=1 for the
        # whole suite, precisely so no test can silently bill Bedrock.
        assert isinstance(get_planner(), FakePlanner)

    def test_real_planner_when_the_escape_hatch_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BMEM_FAKE_PLANNER", raising=False)
        assert isinstance(get_planner(), ClaudePlanner)


class _FakeMessages:
    def __init__(self, outer: _FakeAnthropicClient) -> None:
        self._outer = outer

    def create(self, **kwargs: Any) -> Any:
        self._outer.last_call = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._outer.response_text)]
        )


class _FakeAnthropicClient:
    """Shaped like the real SDK response just enough to exercise ClaudePlanner's
    request construction and response parsing without a network call."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_call: dict[str, Any] | None = None
        self.messages = _FakeMessages(self)


def _strategy_set_json(*pairs: tuple[str, str]) -> str:
    return json.dumps({"strategies": [{"name": n, "hypothesis": h} for n, h in pairs]})


class TestClaudePlannerWiring:
    def test_parses_a_well_formed_response(self) -> None:
        client = _FakeAnthropicClient(
            _strategy_set_json(("shim", "wrap it"), ("codemod", "rewrite it"))
        )
        planner = ClaudePlanner(client=client, model_id="us.anthropic.claude-sonnet-4-6")

        result = planner("migrate off Flask", ["some context"], n=2)

        assert [s.name for s in result] == ["shim", "codemod"]

    def test_sends_the_model_id_and_a_json_schema_output_config(self) -> None:
        client = _FakeAnthropicClient(_strategy_set_json(("a", "h1"), ("b", "h2")))
        planner = ClaudePlanner(client=client, model_id="us.anthropic.claude-sonnet-4-6")

        planner("goal", [], n=2)

        assert client.last_call is not None
        assert client.last_call["model"] == "us.anthropic.claude-sonnet-4-6"
        assert client.last_call["output_config"]["format"]["type"] == "json_schema"
        # The schema must actually describe our Strategy shape, not a stub --
        # this is what would catch a typo in the pydantic model wiring.
        schema = client.last_call["output_config"]["format"]["schema"]
        assert "strategies" in schema["properties"]

    def test_goal_and_context_reach_the_prompt(self) -> None:
        client = _FakeAnthropicClient(_strategy_set_json(("a", "h")))
        planner = ClaudePlanner(client=client, model_id="m")

        planner("a very specific goal", ["a very specific context line"], n=1)

        prompt = client.last_call["messages"][0]["content"]
        assert "a very specific goal" in prompt
        assert "a very specific context line" in prompt

    def test_malformed_json_raises_rather_than_crashing_on_a_missing_field(self) -> None:
        client = _FakeAnthropicClient("not json at all")
        planner = ClaudePlanner(client=client, model_id="m")

        with pytest.raises(ValueError, match="did not match the strategy schema"):
            planner("goal", [], n=1)

    def test_wrong_count_is_rejected(self) -> None:
        client = _FakeAnthropicClient(_strategy_set_json(("a", "h1"), ("b", "h2")))
        planner = ClaudePlanner(client=client, model_id="m")

        with pytest.raises(ValueError, match="asked for 4"):
            planner("goal", [], n=4)

    def test_duplicate_names_are_rejected(self) -> None:
        client = _FakeAnthropicClient(_strategy_set_json(("same", "h1"), ("same", "h2")))
        planner = ClaudePlanner(client=client, model_id="m")

        with pytest.raises(ValueError, match="duplicate"):
            planner("goal", [], n=2)


def test_strategy_requires_both_fields() -> None:
    with pytest.raises(ValidationError):
        Strategy(name="only-a-name")  # type: ignore[call-arg]


def test_claude_planner_against_real_bedrock(bedrock: None) -> None:
    """The one test in this file that is not actually offline.

    Everything else in ``TestClaudePlannerWiring`` injects a fake client to
    prove the request/response wiring without a network call. This is the
    complement: no injected client, so it exercises that exact wiring for
    real. Skips cleanly via the ``bedrock`` fixture until Bedrock access is
    connected -- see conftest.py.
    """
    strategies = ClaudePlanner()(
        goal="Migrate a small Flask API blueprint to FastAPI.",
        context=["The blueprint defines three routes: GET /health, GET /items, POST /items."],
        n=2,
    )
    assert len(strategies) == 2
    assert all(s.name and s.hypothesis for s in strategies)


class _FakeOpenAICompletions:
    def __init__(self, outer: _FakeOpenAIClient) -> None:
        self._outer = outer

    def create(self, **kwargs: Any) -> Any:
        self._outer.last_call = kwargs
        message = SimpleNamespace(content=self._outer.response_text, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeOpenAIClient:
    """Shaped like the OpenAI SDK response, which differs from Anthropic's in
    both directions: the schema goes in response_format.json_schema, and the
    text comes back on choices[].message.content."""

    def __init__(self, response_text: str | None) -> None:
        self.response_text = response_text
        self.last_call: dict[str, Any] | None = None
        self.chat = SimpleNamespace(completions=_FakeOpenAICompletions(self))


class TestOpenAIPlannerWiring:
    def test_parses_a_well_formed_response(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient(_strategy_set_json(("shim", "wrap it"), ("codemod", "rewrite")))
        result = OpenAIPlanner(client=client, model_id="gpt-4o")("migrate", ["ctx"], n=2)
        assert [s.name for s in result] == ["shim", "codemod"]

    def test_sends_a_strict_schema(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient(_strategy_set_json(("a", "x")))
        OpenAIPlanner(client=client, model_id="gpt-4o")("goal", [], n=1)
        fmt = client.last_call["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        schema = fmt["json_schema"]["schema"]
        # Strict mode rejects a schema that omits either of these.
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["strategies"]

    def test_nested_objects_are_also_strictified(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient(_strategy_set_json(("a", "x")))
        OpenAIPlanner(client=client, model_id="gpt-4o")("goal", [], n=1)
        strategy = client.last_call["response_format"]["json_schema"]["schema"]["$defs"]["Strategy"]
        assert strategy["additionalProperties"] is False
        assert sorted(strategy["required"]) == ["hypothesis", "name"]

    def test_a_refusal_raises_something_readable(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient(None)
        with pytest.raises(ValueError, match="no strategy content"):
            OpenAIPlanner(client=client, model_id="gpt-4o")("goal", [], n=1)

    def test_malformed_json_is_reported_against_the_schema(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient('{"strategies": [{"name": "only-a-name"}]}')
        with pytest.raises(ValueError, match="did not match the strategy schema"):
            OpenAIPlanner(client=client, model_id="gpt-4o")("goal", [], n=1)

    def test_the_count_guard_still_applies(self) -> None:
        from bmem.plan import OpenAIPlanner

        client = _FakeOpenAIClient(_strategy_set_json(("a", "x")))
        with pytest.raises(ValueError, match="asked for 3"):
            OpenAIPlanner(client=client, model_id="gpt-4o")("goal", [], n=3)


class TestProviderRouting:
    def test_openai_provider_selects_the_openai_planner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bmem.plan import OpenAIPlanner, get_planner

        monkeypatch.delenv("BMEM_FAKE_PLANNER", raising=False)
        monkeypatch.setenv("BMEM_REASONING_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
        assert isinstance(get_planner(), OpenAIPlanner)

    def test_default_provider_is_still_bedrock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bedrock stays the configured default: OpenAI is the escape hatch,
        # not a migration away from AWS.
        from bmem.plan import ClaudePlanner, get_planner

        monkeypatch.delenv("BMEM_FAKE_PLANNER", raising=False)
        monkeypatch.delenv("BMEM_REASONING_PROVIDER", raising=False)
        assert isinstance(get_planner(), ClaudePlanner)

    def test_the_fake_still_wins_over_any_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bmem.plan import FakePlanner, get_planner

        monkeypatch.setenv("BMEM_FAKE_PLANNER", "1")
        monkeypatch.setenv("BMEM_REASONING_PROVIDER", "openai")
        assert isinstance(get_planner(), FakePlanner)
