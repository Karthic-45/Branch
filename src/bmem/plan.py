"""Claude proposes migration strategies; the caller turns each into a branch.

Mirrors :mod:`bmem.embed`'s split: a :class:`Planner` protocol, a real
Bedrock-backed implementation, and a deterministic fake for tests -- so the
planning step is fully buildable and testable before Bedrock access exists,
the same way ingest was built and proven against :class:`bmem.embed.FakeEmbedder`
before Titan access existed.

Deliberately decoupled from :mod:`bmem.branch` and :mod:`bmem.retrieval`: this
module only turns ``(goal, context) -> strategies``. It does not call
``retrieval.search`` for context and does not call ``branch.fork`` for the
result -- both are the orchestrator's job, once one exists. That keeps this
module testable with plain strings, no database required.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

__all__ = [
    "ClaudePlanner",
    "FakePlanner",
    "OpenAIPlanner",
    "Planner",
    "Strategy",
    "get_planner",
    "strict_json_schema",
]


class Strategy(BaseModel):
    name: str = Field(description="Short slug identifying this strategy, e.g. 'codemod'")
    hypothesis: str = Field(description="One or two sentences describing the concrete approach")


class _StrategySet(BaseModel):
    strategies: list[Strategy]


class Planner(Protocol):
    def __call__(self, goal: str, context: Sequence[str], *, n: int = 4) -> list[Strategy]: ...


_PROMPT_TEMPLATE = """\
You are planning a codebase migration.

Goal: {goal}

Relevant repository context:
{context}

Propose exactly {n} migration strategies. Each needs a short slug name and a \
one-to-two sentence hypothesis describing the concrete approach.

The strategies must be substantively different approaches, not variations on \
the same idea. They will be implemented and evaluated independently against \
the real test suite, and the value of exploring several in parallel depends \
entirely on them actually diverging -- two strategies that converge on the \
same plan waste a branch."""


def _build_prompt(goal: str, context: Sequence[str], n: int) -> str:
    joined = "\n---\n".join(context) if context else "(no repository context provided)"
    return _PROMPT_TEMPLATE.format(goal=goal, context=joined, n=n)


def _validate_strategies(strategies: list[Strategy], n: int) -> None:
    if len(strategies) != n:
        raise ValueError(f"asked for {n} strategies, got {len(strategies)}")
    names = [s.name for s in strategies]
    if len(set(names)) != len(names):
        # Not a semantic-distinctness check -- that would need embedding
        # comparison, and a demo-scale N makes it unnecessary. This only
        # catches the cheap failure mode: two strategies sharing a slug,
        # which downstream would collide as branch hypotheses.
        raise ValueError(f"duplicate strategy names: {names}")


def _reasoning_provider() -> str:
    return os.environ.get("BMEM_REASONING_PROVIDER", "bedrock")


def _default_model_id(provider: str) -> str:
    # Bedrock needs the "us." Geo-inference prefix (see .env.example); the
    # direct Anthropic API takes the bare model name; OpenAI is its own family.
    if provider == "bedrock":
        return "us.anthropic.claude-sonnet-4-6"
    if provider == "openai":
        return "gpt-4o"
    return "claude-sonnet-4-6"


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a Pydantic JSON schema into the shape OpenAI strict mode demands.

    Strict structured output rejects a schema unless every object sets
    ``additionalProperties: false`` and lists *all* of its properties in
    ``required`` -- Pydantic omits both for fields that have defaults. Rewriting
    here rather than hand-maintaining a second copy of the schema keeps the
    model contract derived from the same Pydantic classes the response is
    validated against, so the two cannot drift apart.
    """
    if not isinstance(schema, dict):
        return schema

    out = {k: strict_json_schema(v) if isinstance(v, dict) else v for k, v in schema.items()}
    for key in ("$defs", "properties"):
        if isinstance(out.get(key), dict):
            out[key] = {k: strict_json_schema(v) for k, v in out[key].items()}
    if isinstance(out.get("items"), dict):
        out["items"] = strict_json_schema(out["items"])

    if out.get("type") == "object":
        out["additionalProperties"] = False
        if isinstance(out.get("properties"), dict):
            out["required"] = list(out["properties"])
    return out


def _build_reasoning_client(provider: str, region: str | None) -> Any:
    # BMEM_REASONING_PROVIDER=anthropic swaps this for the direct API while
    # Bedrock access is still pending approval -- switching back later is
    # flipping that one env var, nothing else changes, since both clients
    # expose the identical messages.create surface used below.
    if provider == "openai":
        from openai import OpenAI  # lazy: offline tests need no API key

        return OpenAI()
    if provider == "anthropic":
        from anthropic import Anthropic  # lazy: offline tests need no API key

        return Anthropic()
    aws_region = region or os.environ.get("AWS_REGION", "us-east-1")
    # bedrock-runtime by default -- the same endpoint readiness.py probes, and
    # the one that accepts cross-region inference profile IDs like
    # "us.anthropic.claude-sonnet-4-6". The Mantle (Anthropic-compatible)
    # endpoint only takes bare "anthropic.claude-..." IDs and serves a narrower
    # set of models; with the default model ID it 404s ("model does not
    # exist") even though readiness reports Bedrock as ready. Opt back in with
    # BMEM_BEDROCK_CLIENT=mantle and a Mantle-style BMEM_REASONING_MODEL.
    if os.environ.get("BMEM_BEDROCK_CLIENT", "runtime") == "mantle":
        from anthropic import AnthropicBedrockMantle  # lazy: offline tests need no AWS SDK config

        return AnthropicBedrockMantle(aws_region=aws_region)
    from anthropic import AnthropicBedrock  # lazy: offline tests need no AWS SDK config

    return AnthropicBedrock(aws_region=aws_region)


class ClaudePlanner:
    """Claude, via Bedrock (bedrock-runtime) by default or the direct Anthropic
    API when ``BMEM_REASONING_PROVIDER=anthropic`` -- see ``_build_reasoning_client``.

    Uses ``output_config.format`` with a JSON schema rather than
    ``messages.parse()``: the latter is documented for the first-party client,
    and this project cannot yet confirm live whether it is mirrored identically
    on ``AnthropicBedrockMantle``. Raw ``output_config.format`` + manual
    ``model_validate_json`` is the pattern documented to work the same way
    across every provider client, so it is the safer choice to write without
    being able to test it against a real account yet.
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        model_id: str | None = None,
        client: Any = None,
    ) -> None:
        self.provider = _reasoning_provider()
        self.model_id = model_id or os.environ.get(
            "BMEM_REASONING_MODEL", _default_model_id(self.provider)
        )
        self._client = (
            client if client is not None else _build_reasoning_client(self.provider, region)
        )

    def __call__(self, goal: str, context: Sequence[str], *, n: int = 4) -> list[Strategy]:
        prompt = _build_prompt(goal, context, n)
        response = self._client.messages.create(
            model=self.model_id,
            max_tokens=4096,
            output_config={
                "format": {
                    "type": "json_schema",
                    # additionalProperties: false on every object -- Bedrock
                    # structured outputs reject any other value.
                    "schema": strict_json_schema(_StrategySet.model_json_schema()),
                }
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = next(block.text for block in response.content if block.type == "text")
        try:
            parsed = _StrategySet.model_validate_json(text)
        except ValidationError as exc:
            raise ValueError(f"Claude's response did not match the strategy schema: {exc}") from exc

        _validate_strategies(parsed.strategies, n)
        return parsed.strategies


class OpenAIPlanner:
    """The same planning step against OpenAI's chat completions API.

    A sibling of :class:`ClaudePlanner` rather than a branch inside it: the two
    APIs disagree about where the schema goes (``output_config.format`` versus
    ``response_format.json_schema``) and where the text comes back
    (``content[].text`` versus ``choices[].message.content``). Threading both
    through one class would mean two code paths behind one name, and the
    injected-client tests could then only ever cover one of them.

    Exists because AWS Bedrock model quotas are account-level and can be zero
    on a new account even after model access is granted -- see
    :mod:`bmem.readiness`. Bedrock remains the default provider; this is the
    escape hatch, and :mod:`bmem.manifest` records which one actually ran so a
    decision's provenance never claims Bedrock for a call OpenAI served.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        client: Any = None,
    ) -> None:
        self.provider = "openai"
        self.model_id = model_id or os.environ.get("BMEM_REASONING_MODEL", "gpt-4o")
        self._client = client if client is not None else _build_reasoning_client("openai", None)

    def __call__(self, goal: str, context: Sequence[str], *, n: int = 4) -> list[Strategy]:
        prompt = _build_prompt(goal, context, n)
        response = self._client.chat.completions.create(
            model=self.model_id,
            max_tokens=4096,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "strategy_set",
                    "strict": True,
                    "schema": strict_json_schema(_StrategySet.model_json_schema()),
                },
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not text:
            # A strict-mode refusal comes back with content=None and the reason
            # on .refusal; surfacing that beats a confusing JSON parse error.
            refusal = getattr(response.choices[0].message, "refusal", None)
            raise ValueError(f"OpenAI returned no strategy content (refusal: {refusal})")
        try:
            parsed = _StrategySet.model_validate_json(text)
        except ValidationError as exc:
            raise ValueError(f"OpenAI's response did not match the strategy schema: {exc}") from exc

        _validate_strategies(parsed.strategies, n)
        return parsed.strategies


# The four strategies a real planner actually proposes for a framework
# migration, and the ones the demo's narrative is built around. Used by
# FakePlanner so that running without Bedrock still produces branches a viewer
# can tell apart -- "strategy-0/1/2/3" makes the tree look like a placeholder
# even though the branching underneath it is entirely real.
_STOCK_STRATEGIES = (
    (
        "shim",
        "Wrap the existing Flask app in an ASGI compatibility shim and migrate routes behind it",
    ),
    (
        "codemod",
        "Mechanically rewrite every route decorator and request accessor with an AST codemod",
    ),
    (
        "strangler",
        "Stand up FastAPI alongside Flask and move endpoints across one blueprint at a time",
    ),
    (
        "rewrite",
        "Hand-rewrite the API layer against FastAPI idioms, porting tests as the specification",
    ),
)


class FakePlanner:
    """Deterministic stand-in for Claude, for tests and offline development.

    With no fixed strategies given, returns plausible named migration
    strategies rather than numbered placeholders -- distinct enough that the
    live tree reads correctly with no Bedrock access. Past the stock four it
    falls back to generated names, so an arbitrary ``n`` still satisfies
    :func:`_validate_strategies`'s duplicate-name check.

    Pass ``strategies=`` to pin exact output for a specific test.
    """

    def __init__(self, strategies: Sequence[Strategy] | None = None) -> None:
        self._fixed = list(strategies) if strategies is not None else None

    def __call__(self, goal: str, context: Sequence[str], *, n: int = 4) -> list[Strategy]:
        if self._fixed is not None:
            chosen = list(self._fixed[:n])
        else:
            chosen = [
                Strategy(name=name, hypothesis=hypothesis)
                for name, hypothesis in _STOCK_STRATEGIES[:n]
            ]
            chosen += [
                Strategy(name=f"strategy-{i}", hypothesis=f"Approach {i} for: {goal}")
                for i in range(len(chosen), n)
            ]
        _validate_strategies(chosen, n)
        return chosen


def get_planner() -> Planner:
    """Return the real planner, or the fake one when ``BMEM_FAKE_PLANNER`` is set.

    Mirrors :func:`bmem.embed.get_embedder`'s escape hatch, for the same
    reason: this project's Bedrock access and CockroachDB access are approved
    on different timelines, and tests should not have to wait on either.
    """
    if os.environ.get("BMEM_FAKE_PLANNER"):
        return FakePlanner()
    if _reasoning_provider() == "openai":
        return OpenAIPlanner()
    return ClaudePlanner()
