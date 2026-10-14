"""Fixtures for tests that need a live CockroachDB cluster.

Everything here skips cleanly when ``BMEM_DATABASE_URL`` is unset, so the
offline suite stays runnable on a laptop with no cloud account:

    pytest -m "not cluster and not bedrock"     # no accounts needed
    pytest                                       # everything
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from bmem.branch import create_root, fork
from bmem.db import Vector, get_pool, run_transaction, transaction
from bmem.embed import FakeEmbedder
from bmem.retrieval import ancestry_cache


@pytest.fixture(scope="session")
def cluster() -> None:
    """Verify a reachable cluster with the schema applied, or skip the test."""
    if not os.environ.get("BMEM_DATABASE_URL"):
        pytest.skip("BMEM_DATABASE_URL is not set; skipping cluster tests")

    try:
        with transaction() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
    except Exception as exc:  # noqa: BLE001 -- surfaced as a skip, not a failure
        pytest.skip(f"cannot reach cluster: {exc}")

    with transaction() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('branches', 'memory', 'merge_log', 'branch_scratch')
            """
        )
        found = {row["table_name"] for row in cur.fetchall()}

    missing = {"branches", "memory", "merge_log", "branch_scratch"} - found
    if missing:
        pytest.skip(
            f"schema not applied (missing: {', '.join(sorted(missing))}). "
            f"Run infra/apply_schema.sh first."
        )


@pytest.fixture
def embedder() -> FakeEmbedder:
    """Deterministic embeddings, so these tests need no Bedrock access."""
    return FakeEmbedder()


@pytest.fixture
def root(cluster: None) -> Iterator[str]:
    """A fresh trunk branch per test, with everything below it torn down after.

    Tests share one cluster, so each gets its own lineage rather than a truncated
    database -- that keeps them safe to run concurrently and safe to run against
    a cluster that has real data in it.
    """
    with transaction() as cur:
        root_id = create_root(cur, hypothesis=f"test-root-{os.getpid()}")

    yield root_id

    ancestry_cache.clear()

    def _teardown(cur: Any) -> None:
        # Collect the whole subtree, then delete children before parents so the
        # self-referencing foreign key stays satisfied.
        cur.execute(
            """
            WITH RECURSIVE subtree(id, depth) AS (
                    SELECT id, 0 FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id, s.depth + 1
                    FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT id FROM subtree ORDER BY depth DESC
            """,
            {"root": root_id},
        )
        ids = [row["id"] for row in cur.fetchall()]
        if ids:
            cur.execute("DELETE FROM memory WHERE branch_id = ANY(%(ids)s)", {"ids": ids})
            cur.execute("DELETE FROM branch_scratch WHERE branch_id = ANY(%(ids)s)", {"ids": ids})
            cur.execute(
                "DELETE FROM execution_manifests WHERE branch_id = ANY(%(ids)s)", {"ids": ids}
            )
            cur.execute("DELETE FROM branch_outcomes WHERE branch_id = ANY(%(ids)s)", {"ids": ids})
            # decision_rationales and decision_applications both reference
            # merge_log, so they have to go before it does -- same
            # children-before-parents rule as the branch subtree above.
            cur.execute(
                """
                DELETE FROM decision_rationales WHERE merge_id IN (
                    SELECT id FROM merge_log
                    WHERE parent_id = ANY(%(ids)s) OR winner_id = ANY(%(ids)s)
                )
                """,
                {"ids": ids},
            )
            cur.execute(
                """
                DELETE FROM decision_applications WHERE merge_id IN (
                    SELECT id FROM merge_log
                    WHERE parent_id = ANY(%(ids)s) OR winner_id = ANY(%(ids)s)
                )
                """,
                {"ids": ids},
            )
            cur.execute(
                "DELETE FROM merge_log WHERE parent_id = ANY(%(ids)s) OR winner_id = ANY(%(ids)s)",
                {"ids": ids},
            )
            for branch_id in ids:
                cur.execute("DELETE FROM branches WHERE id = %(id)s", {"id": branch_id})

    # Many statements against a shared, concurrently-written cluster -- exactly
    # the shape that can hit CockroachDB's SQLSTATE 40001 transaction-retry
    # error under real parallel test load. run_transaction retries the whole
    # teardown from BEGIN rather than leaving orphaned branches behind for the
    # next run to trip over.
    run_transaction(_teardown)


@pytest.fixture
def write(embedder: FakeEmbedder) -> Any:
    """Return a helper that writes one memory row to a branch."""

    def _write(
        branch_id: str,
        key: str,
        content: str,
        *,
        tombstone: bool = False,
    ) -> None:
        with transaction() as cur:
            cur.execute(
                """
                UPSERT INTO memory (branch_id, key, content, embedding,
                                    created_at_hlc, tombstone)
                VALUES (%(b)s, %(k)s, %(c)s, %(e)s, cluster_logical_timestamp(), %(t)s)
                """,
                {
                    "b": branch_id,
                    "k": key,
                    "c": content,
                    "e": Vector(embedder([content])[0]),
                    "t": tombstone,
                },
            )

    return _write


@pytest.fixture
def four_way(root: str) -> dict[str, str]:
    """The demo's shape: four sibling hypotheses forked from one trunk."""

    def _fork_four(cur: Any) -> dict[str, str]:
        return {name: fork(cur, root, name) for name in ("shim", "rewrite", "codemod", "strangler")}

    return run_transaction(_fork_four)


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Mark cluster/bedrock tests, and skip them outright when unconfigured.

    Two things happen here, and the second is what keeps ``pytest`` green on a
    machine with no cloud account:

    1. Anything requesting a cluster or bedrock fixture is auto-marked
       accordingly, so ``-m "not cluster and not bedrock"`` selects the offline
       suite without every test having to remember its own marker.
    2. If the corresponding credential is unset, every matching test is
       skipped outright. The fixtures already skip on use, but a test that
       reaches the cluster or Bedrock *without* going through the fixture would
       otherwise error out with a connection failure instead -- which reads as
       a broken suite rather than an unconfigured one.
    """
    del config
    cluster_fixtures = {"cluster", "root", "four_way", "write"}
    bedrock_fixtures = {"bedrock"}
    cluster_configured = bool(os.environ.get("BMEM_DATABASE_URL"))
    bedrock_configured = bool(os.environ.get("AWS_REGION"))
    no_cluster = pytest.mark.skip(reason="BMEM_DATABASE_URL is not set")
    no_bedrock = pytest.mark.skip(reason="AWS_REGION is not set")

    for item in items:
        fixtures = set(getattr(item, "fixturenames", ()))
        if cluster_fixtures & fixtures:
            item.add_marker(pytest.mark.cluster)
        if bedrock_fixtures & fixtures:
            item.add_marker(pytest.mark.bedrock)
        if not cluster_configured and item.get_closest_marker("cluster"):
            item.add_marker(no_cluster)
        if not bedrock_configured and item.get_closest_marker("bedrock"):
            item.add_marker(no_bedrock)


@pytest.fixture(scope="session")
def bedrock() -> None:
    """Verify Bedrock is reachable and the reasoning model is invokable, or skip.

    A real, tiny (max_tokens=1) call -- there is no way to prove the real
    integration works without spending at least one real token, the same
    tradeoff the ``cluster`` fixture makes with a real ``SELECT 1``.
    """
    if not os.environ.get("AWS_REGION"):
        pytest.skip("AWS_REGION is not set; skipping Bedrock tests")

    try:
        from anthropic import AnthropicBedrockMantle

        client = AnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"])
        model_id = os.environ.get("BMEM_REASONING_MODEL", "us.anthropic.claude-sonnet-4-6")
        client.messages.create(
            model=model_id,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced as a skip, not a failure
        pytest.skip(f"cannot reach Bedrock model {model_id}: {exc}")


def pytest_configure() -> None:
    """Force every fake backend, so no test can silently bill Bedrock or trigger a CodeBuild run."""
    os.environ.setdefault("BMEM_FAKE_EMBEDDINGS", "1")
    os.environ.setdefault("BMEM_FAKE_PLANNER", "1")
    os.environ.setdefault("BMEM_FAKE_CODEBUILD", "1")


@pytest.fixture(scope="session", autouse=True)
def _close_pool() -> Iterator[None]:
    yield
    if os.environ.get("BMEM_DATABASE_URL"):
        try:
            get_pool().close()
        except Exception:  # noqa: BLE001 -- teardown must not mask a real failure
            pass
