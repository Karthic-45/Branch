"""The reproducibility manifest's offline-testable surface.

get_manifest() itself needs a cluster (exercised live -- see demo/script.md),
so what's pinned here is the shape contract every consumer depends on: the
model fields are *optional*, because a fake-backed run legitimately has no
model to name, and the whole point of this table is that it never invents one.
"""

from __future__ import annotations

import pytest

from bmem.explore import EXPLORE_PROMPT_VERSION, FakeExplorer, belief_key
from bmem.manifest import ExecutionManifest, RetrievedMemoryRef


def _manifest(**overrides: object) -> ExecutionManifest:
    defaults: dict[str, object] = dict(
        branch_id="b-1",
        run_id="run-1",
        strategy_name="shim",
        hypothesis="Wrap it in a shim",
        reasoning_provider=None,
        reasoning_model_id=None,
        explore_prompt_version="v1",
        embedding_provider=None,
        embedding_model_id=None,
        retrieved_memories=[],
        target_repo=None,
        target_ref=None,
        base_sha=None,
        test_selector=None,
        codebuild_project=None,
        created_at=None,
        updated_at=None,
    )
    defaults.update(overrides)
    return ExecutionManifest.model_validate(defaults)


class TestManifestShape:
    def test_a_fake_backed_run_has_no_model_names_rather_than_invented_ones(self) -> None:
        m = _manifest()
        assert m.reasoning_model_id is None
        assert m.embedding_model_id is None
        assert m.reasoning_provider is None

    def test_records_the_prompt_version_that_produced_it(self) -> None:
        assert _manifest(explore_prompt_version="v1").explore_prompt_version == "v1"

    def test_retrieved_memories_round_trip(self) -> None:
        m = _manifest(
            retrieved_memories=[
                {
                    "key": "src/app.py::main",
                    "source_branch_id": "root-1",
                    "inherited": True,
                    "distance": 0.42,
                }
            ]
        )
        assert len(m.retrieved_memories) == 1
        assert m.retrieved_memories[0].inherited is True
        assert m.retrieved_memories[0].distance == 0.42

    def test_evaluation_fields_are_absent_until_evaluation_finishes(self) -> None:
        # explore.py writes the manifest row; orchestrate.py fills these in
        # afterwards. Between those two points they are legitimately null.
        m = _manifest()
        assert m.target_repo is None
        assert m.base_sha is None
        assert m.codebuild_project is None

    def test_a_fully_populated_manifest_validates(self) -> None:
        m = _manifest(
            reasoning_provider="bedrock",
            reasoning_model_id="us.anthropic.claude-sonnet-4-6",
            embedding_provider="titan",
            embedding_model_id="amazon.titan-embed-text-v2:0",
            target_repo="https://github.com/pypa/sampleproject.git",
            base_sha="621e4974ca25ce531773def586ba3ed8e736b3fc",
            codebuild_project="branchable-memory-eval",
        )
        assert m.reasoning_model_id == "us.anthropic.claude-sonnet-4-6"
        assert m.base_sha.startswith("621e4974")

    def test_retrieved_memory_ref_requires_every_field(self) -> None:
        with pytest.raises(ValueError):
            RetrievedMemoryRef.model_validate({"key": "k", "inherited": True})


class TestProvenanceIntrospection:
    """explore.py reads model identity off the explorer via getattr rather
    than a protocol method. These pin the two cases that behaviour rests on."""

    def test_fake_explorer_exposes_no_model_identity(self) -> None:
        fake = FakeExplorer()
        assert getattr(fake, "model_id", None) is None
        assert getattr(fake, "provider", None) is None

    def test_prompt_version_is_a_non_empty_string(self) -> None:
        # Recorded into every manifest, so an empty value would silently make
        # every row's provenance useless.
        assert isinstance(EXPLORE_PROMPT_VERSION, str)
        assert EXPLORE_PROMPT_VERSION


def test_belief_key_is_stable_across_modules() -> None:
    # manifest rows are joined back to memory rows by this key; if the slug
    # format drifts, provenance silently stops resolving.
    assert belief_key("shim") == "shim-belief"
