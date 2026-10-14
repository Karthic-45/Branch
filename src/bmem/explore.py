"""Each branch explores its own hypothesis in isolation.

Reads only its own branch-scoped memory (via :mod:`bmem.retrieval`, already
sealed by lineage), proposes a patch implementing its assigned strategy, and
writes its reasoning back to its own ``branch_id`` -- never touching a
sibling's memory, because a sibling's memory is not in its lineage at all.

Mirrors :mod:`bmem.plan`'s shape exactly: an ``Explorer`` protocol, a
deterministic ``FakeExplorer``, a real ``ClaudeExplorer`` proven offline with
an injected fake client plus one ``bedrock``-marked real test. See plan.py's
module docstring for why ``output_config.format`` is used instead of
``messages.parse()``; the same reasoning applies here unchanged.

What this module does not do: call CodeBuild, touch S3, or decide a winner.
Evaluating the patch against the real test suite and promoting one via
``branch.merge`` are later, separate concerns -- this module's job ends the
moment a branch has written down what it believes and what it would change.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import psycopg
from pydantic import BaseModel, Field, ValidationError

from bmem.db import Vector, run_transaction
from bmem.embed import Embedder
from bmem.plan import Strategy, strict_json_schema
from bmem.retrieval import Hit, search
from bmem.source_context import SourceTree, build_context

__all__ = [
    "ClaudeExplorer",
    "OpenAIExplorer",
    "EXPLORE_PROMPT_VERSION",
    "Exploration",
    "Explorer",
    "FakeExplorer",
    "belief_key",
    "explore_branch",
    "get_explorer",
]

# Bumped by hand whenever _PROMPT_TEMPLATE changes meaningfully -- the
# reproducibility manifest (bmem.manifest) records this per branch, so a
# later "did strategy X ever get tried against the current prompt" question
# has an actual answer instead of an assumption.
EXPLORE_PROMPT_VERSION = "v2-full-files"


def belief_key(strategy_name: str) -> str:
    """The memory key a branch's belief is written under -- shared with
    callers that need to address that row later (orchestrate.py writes
    ``s3_uri`` onto it once evaluation finishes), so the slug format lives in
    exactly one place.
    """
    return f"{strategy_name}-belief"


EXPLORE_MAX_TOKENS = 16_000


class Exploration(BaseModel):
    belief: str = Field(description="What this branch concluded, in prose -- becomes a memory row")
    patch: str = Field(
        description="A unified diff implementing the hypothesis against the target repo"
    )


class Explorer(Protocol):
    def __call__(self, hypothesis: str, context: Sequence[str]) -> Exploration: ...


_PROMPT_TEMPLATE = """\
You are implementing one specific migration strategy against a real codebase.

Strategy to implement: {hypothesis}

Relevant repository context:
{context}

Produce a unified diff (git apply format) that implements this strategy \
against the files shown above. Also state, in one or two sentences, what you \
concluded while doing this -- a fact worth remembering on this branch, the \
kind of thing a future search for related work should be able to find.

Blocks marked FULL FILE are the exact current contents of those files: edit \
them freely, and quote their lines exactly in your diff's context so it \
applies with `git apply`. Blocks marked EXCERPT are fragments -- do not edit \
those files, because you cannot see the lines around the excerpt. You may \
create new files. Implement the strategy by changing the existing code it \
targets: a patch that only adds new files which nothing imports or calls \
leaves the application unchanged and does not count as implementing it.

If even the full files shown are not enough to implement the strategy safely, \
return an empty patch and explain in your belief exactly which file or \
information was missing."""


def _build_prompt(hypothesis: str, context: Sequence[str]) -> str:
    joined = "\n---\n".join(context) if context else "(no repository context provided)"
    return _PROMPT_TEMPLATE.format(hypothesis=hypothesis, context=joined)


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


class ClaudeExplorer:
    """Claude, via Bedrock (bedrock-runtime) by default or the direct Anthropic
    API when ``BMEM_REASONING_PROVIDER=anthropic`` -- see ``_build_reasoning_client``.
    See plan.ClaudePlanner for why this uses output_config.format rather than
    messages.parse()."""

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

    def __call__(self, hypothesis: str, context: Sequence[str]) -> Exploration:
        prompt = _build_prompt(hypothesis, context)
        response = self._client.messages.create(
            model=self.model_id,
            # A whole-file patch plus its belief can run past 8k tokens; the
            # first real CTFd run lost a branch to a JSON string cut off
            # mid-patch. Kept below the size at which the SDK insists on
            # streaming for a non-streaming call.
            max_tokens=EXPLORE_MAX_TOKENS,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": strict_json_schema(Exploration.model_json_schema()),
                }
            },
            messages=[{"role": "user", "content": prompt}],
        )
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise ValueError(
                f"Claude's patch exceeded the {EXPLORE_MAX_TOKENS:,}-token output limit "
                "and was cut off"
            )
        text = next(block.text for block in response.content if block.type == "text")
        try:
            return Exploration.model_validate_json(text)
        except ValidationError as exc:
            raise ValueError(
                f"Claude's response did not match the exploration schema: {exc}"
            ) from exc


class OpenAIExplorer:
    """The exploration step against OpenAI's chat completions API.

    Sibling of :class:`ClaudeExplorer` for the same reason OpenAIPlanner is a
    sibling of ClaudePlanner -- see that class. The belief and the diff still
    come back as one structured object validated against :class:`Exploration`,
    so everything downstream (the memory write, the patch upload, the manifest)
    is unchanged regardless of which provider produced it.
    """

    def __init__(self, *, model_id: str | None = None, client: Any = None) -> None:
        self.provider = "openai"
        self.model_id = model_id or os.environ.get("BMEM_REASONING_MODEL", "gpt-4o")
        self._client = client if client is not None else _build_reasoning_client("openai", None)

    def __call__(self, hypothesis: str, context: Sequence[str]) -> Exploration:
        prompt = _build_prompt(hypothesis, context)
        response = self._client.chat.completions.create(
            model=self.model_id,
            max_tokens=8192,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "exploration",
                    "strict": True,
                    "schema": strict_json_schema(Exploration.model_json_schema()),
                },
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not text:
            refusal = getattr(response.choices[0].message, "refusal", None)
            raise ValueError(f"OpenAI returned no exploration content (refusal: {refusal})")
        try:
            return Exploration.model_validate_json(text)
        except ValidationError as exc:
            raise ValueError(
                f"OpenAI's response did not match the exploration schema: {exc}"
            ) from exc


class FakeExplorer:
    """Deterministic stand-in for Claude, for tests and offline development."""

    def __init__(self, exploration: Exploration | None = None) -> None:
        self._fixed = exploration

    def __call__(self, hypothesis: str, context: Sequence[str]) -> Exploration:
        if self._fixed is not None:
            return self._fixed
        return Exploration(
            belief=f"Implemented via: {hypothesis}",
            patch=f"--- fake patch for: {hypothesis} ---",
        )


def get_explorer() -> Explorer:
    """Return the real explorer, or the fake one when ``BMEM_FAKE_PLANNER`` is set.

    Reuses plan.py's escape hatch rather than a separate one: both are "the
    reasoning step that talks to Claude", and a caller building the whole
    plan -> explore pipeline offline wants one switch, not two.
    """
    if os.environ.get("BMEM_FAKE_PLANNER"):
        return FakeExplorer()
    if _reasoning_provider() == "openai":
        return OpenAIExplorer()
    return ClaudeExplorer()


def explore_branch(
    branch_id: str,
    strategy: Strategy,
    *,
    explore: Explorer,
    embed: Embedder,
    k: int = 8,
    run_id: str | None = None,
    on_event: Callable[[str, str], None] | None = None,
    source: SourceTree | None = None,
) -> Exploration:
    """Search this branch's own memory, explore the strategy, write the belief back.

    ``source``, when given, is a checkout of the ref being evaluated: search
    still decides which files are relevant, and those files are then shown to
    the model whole (see :mod:`bmem.source_context`) instead of as the
    function-sized excerpts memory holds.

    A complete, top-level operation -- like :func:`bmem.branch.merge` and
    :func:`bmem.branch.revert`, not like :func:`bmem.retrieval.search` or
    :func:`bmem.branch.fork` -- because the caller's natural use of this is a
    loop over several sibling branches, and each needs its own retry-safe unit
    via :func:`bmem.db.run_transaction` rather than one transaction spanning
    every branch in the run.

    The slow external calls -- Titan embeddings and, above all, the Claude
    call that writes a whole patch (often minutes) -- run *between* two short
    transactions, never inside one. Holding a serializable CockroachDB
    transaction open across a multi-minute model call is the same failure
    mode db.run_transaction's docstring documents for long writes: the
    commit hits SQLSTATE 40001, the retry wrapper re-runs the closure, and
    with the model call inside it every retry pays for (and waits on) Claude
    again -- up to _RETRY_MAX_ATTEMPTS times. The instant FakeExplorer never
    exposed this; the first real-Bedrock run sat in "exploring" for minutes.

    Retrieval is scoped to ``branch_id``'s lineage by construction -- see
    :func:`bmem.retrieval.search` -- so this cannot see a sibling branch's
    reasoning even by accident. The belief is written under
    ``strategy.name``, not the full hypothesis prose, so the memory key stays
    a short, stable slug (``"codemod-belief"``, not a sentence) -- visible to
    this branch and anything forked from it later, never to the siblings it
    started alongside.

    ``on_event(stage, message)`` fires twice: once before anything happens,
    once after the belief is durably written. Nothing in between, deliberately
    -- an event fired mid-way would add noise, not information, to a step
    whose only observable outcome is the belief being written.

    ``run_id``, when given, also writes a reproducibility manifest row (see
    :mod:`bmem.manifest`) recording exactly which memories were retrieved and
    which model answered -- introspected via ``getattr(explore, "model_id"/
    "provider", None)`` rather than a required protocol method, so
    :class:`FakeExplorer` (which has neither) honestly produces a manifest
    with those fields null instead of a fabricated model name. ``None``
    (the default) skips this write entirely -- existing callers and tests
    that call this outside any tracked run are unaffected.
    """
    if on_event:
        on_event("exploring", "Searching this branch's inherited memory")

    # 1. Retrieve: embed outside any transaction, then one short read.
    query_vector = embed([strategy.hypothesis])[0]
    hits: list[Hit] = run_transaction(lambda cur: search(cur, branch_id, query_vector, k=k))

    # 2. Reason: the slow model call, with no transaction open.
    context = build_context(strategy.hypothesis, [(h.key, h.content) for h in hits], source)
    if on_event and source is not None:
        shown = ", ".join(context.full_files[:6]) + (
            f" +{len(context.full_files) - 6} more" if len(context.full_files) > 6 else ""
        )
        on_event(
            "exploring",
            f"Context: {len(context.full_files)} whole files ({shown or 'none'})"
            + (
                f"; too large to include: {', '.join(context.skipped_large)}"
                if context.skipped_large
                else ""
            ),
        )
    exploration = explore(strategy.hypothesis, context.blocks)
    belief_vector = Vector(embed([exploration.belief])[0])

    # 3. Write the belief back: one short, cheaply retryable write.
    def _write(cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            """
            UPSERT INTO memory (branch_id, key, content, embedding,
                                created_at_hlc, tombstone)
            VALUES (%(b)s, %(key)s, %(content)s, %(embedding)s,
                    cluster_logical_timestamp(), false)
            """,
            {
                "b": branch_id,
                "key": belief_key(strategy.name),
                "content": exploration.belief,
                "embedding": belief_vector,
            },
        )

    run_transaction(_write)
    if on_event:
        on_event("explored", exploration.belief)

    if run_id is not None:
        _write_manifest(branch_id, run_id, strategy, hits, explore, embed)

    return exploration


def _write_manifest(
    branch_id: str,
    run_id: str,
    strategy: Strategy,
    hits: list[Hit],
    explore: Explorer,
    embed: Embedder,
) -> None:
    def _write(cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            """
            UPSERT INTO execution_manifests
                (branch_id, run_id, strategy_name, hypothesis, reasoning_provider,
                 reasoning_model_id, explore_prompt_version, embedding_provider,
                 embedding_model_id, retrieved_memories, created_at)
            VALUES
                (%(branch_id)s, %(run_id)s, %(strategy_name)s, %(hypothesis)s,
                 %(reasoning_provider)s, %(reasoning_model_id)s, %(explore_prompt_version)s,
                 %(embedding_provider)s, %(embedding_model_id)s, %(retrieved_memories)s, now())
            """,
            {
                "branch_id": branch_id,
                "run_id": run_id,
                "strategy_name": strategy.name,
                "hypothesis": strategy.hypothesis,
                "reasoning_provider": getattr(explore, "provider", None),
                "reasoning_model_id": getattr(explore, "model_id", None),
                "explore_prompt_version": EXPLORE_PROMPT_VERSION,
                "embedding_provider": "titan" if getattr(embed, "model_id", None) else None,
                "embedding_model_id": getattr(embed, "model_id", None),
                "retrieved_memories": json.dumps(
                    [
                        {
                            "key": h.key,
                            "source_branch_id": h.source_branch_id,
                            "inherited": h.inherited,
                            "distance": h.distance,
                        }
                        for h in hits
                    ]
                ),
            },
        )

    run_transaction(_write)
