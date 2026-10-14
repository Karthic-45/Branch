"""Embeddings via Amazon Titan Text Embeddings v2 on Bedrock.

Titan v2 embeds one text per ``InvokeModel`` call, so throughput comes from
issuing calls concurrently rather than from a batch API.

The dimension must stay at 1024 to match ``VECTOR(1024)`` in the schema. Titan v2
also offers 256 and 512; changing it here means changing the column type and
rebuilding the index, so it is asserted rather than assumed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

__all__ = [
    "DIMENSIONS",
    "Embedder",
    "FakeEmbedder",
    "OpenAIEmbedder",
    "TitanEmbedder",
    "get_embedder",
]

DIMENSIONS = 1024

_MAX_ATTEMPTS = 5
_BASE_DELAY = 0.5

# Throttling is the one failure that is guaranteed to clear if you wait long
# enough, so it gets a longer budget than other transient errors. Found on
# the first real ingest of a full repository: thousands of Titan calls from 8
# threads on a fresh account's default quota exhausted the 5 x ~4s budget
# above five minutes in, and the whole ingest -- every vector already paid
# for -- was thrown away.
_THROTTLE_MAX_ATTEMPTS = 10
_THROTTLE_MAX_DELAY = 20.0
_THROTTLE_NAMES = (
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceQuotaExceededException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
)


def _is_throttle(exc: Exception) -> bool:
    if type(exc).__name__ in _THROTTLE_NAMES:
        return True
    # botocore surfaces these as ClientError with the code in the response.
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in _THROTTLE_NAMES


def _default_workers() -> int:
    try:
        return max(1, int(os.environ.get("BMEM_EMBED_WORKERS", "4")))
    except ValueError:
        return 4


class Embedder(Protocol):
    """Anything that turns texts into vectors of length :data:`DIMENSIONS`."""

    def __call__(self, texts: Sequence[str]) -> list[list[float]]: ...


class TitanEmbedder:
    """Amazon Titan Text Embeddings v2, via ``bedrock-runtime``."""

    def __init__(
        self,
        *,
        region: str | None = None,
        model_id: str | None = None,
        max_workers: int | None = None,
        client: Any = None,
    ) -> None:
        self.model_id = model_id or os.environ.get(
            "BMEM_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0"
        )
        self.max_workers = max_workers if max_workers is not None else _default_workers()
        if client is not None:
            self._client = client
        else:
            import boto3  # imported lazily so tests need no AWS SDK config
            from botocore.config import Config

            # "adaptive" retry mode adds client-side rate limiting that backs
            # the whole client off when Bedrock starts throttling, instead of
            # every thread hammering the quota independently.
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
                config=Config(retries={"max_attempts": 8, "mode": "adaptive"}),
            )

    def _embed_one(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": DIMENSIONS,
                # Normalized vectors make L2 distance monotonic with cosine
                # similarity, so the index's <-> operator ranks the way the
                # retrieval layer assumes it does.
                "normalize": True,
            }
        )

        last_error: Exception | None = None
        attempt = 0
        while True:
            try:
                response = self._client.invoke_model(modelId=self.model_id, body=body)
                payload = json.loads(response["body"].read())
                vector = payload["embedding"]
                if len(vector) != DIMENSIONS:
                    raise ValueError(
                        f"Titan returned {len(vector)} dimensions, schema expects "
                        f"{DIMENSIONS}. The VECTOR column and index would have to "
                        f"change to match."
                    )
                return vector
            except Exception as exc:  # noqa: BLE001 -- retried below, re-raised at end
                # Bedrock throttles aggressively when four branch workers embed
                # concurrently. Exponential backoff with jitter, then give up
                # loudly rather than returning a zero vector, which would
                # silently poison the index.
                if _is_fatal(exc):
                    raise
                last_error = exc
                throttled = _is_throttle(exc)
                limit = _THROTTLE_MAX_ATTEMPTS if throttled else _MAX_ATTEMPTS
                attempt += 1
                if attempt >= limit:
                    break
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                if throttled:
                    delay = min(_THROTTLE_MAX_DELAY, delay * 2)
                time.sleep(delay + random.uniform(0, 1.0 if throttled else 0.25))

        raise RuntimeError(
            f"Titan embedding failed after {attempt} attempts "
            f"({type(last_error).__name__}: {last_error})"
        ) from last_error

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) == 1:
            return [self._embed_one(texts[0])]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(self._embed_one, texts))


class OpenAIEmbedder:
    """OpenAI text embeddings, pinned to the schema's 1024 dimensions.

    The v3 embedding models support native dimension reduction, so asking for
    1024 produces a genuine 1024-dimension vector rather than a truncated
    1536 one -- which matters because the schema declares ``VECTOR(1024)`` and
    the index is built for it. That is the whole reason this provider is a
    drop-in: no migration, no reindex, no change to retrieval.

    Vectors are returned already normalised by the API, which is the property
    :mod:`bmem.retrieval` relies on for its L2 distance to rank the same way
    cosine similarity would.

    One caveat worth stating plainly: embeddings from two different models are
    not comparable. Memory embedded with Titan and memory embedded with OpenAI
    must not share a branch lineage, or retrieval distances across the two
    become meaningless. :mod:`bmem.manifest` records the embedding model per
    branch so a mismatch is at least visible after the fact.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        max_workers: int = 8,
        client: Any = None,
    ) -> None:
        self.provider = "openai"
        self.model_id = model_id or os.environ.get(
            "BMEM_EMBEDDING_MODEL_OPENAI", "text-embedding-3-small"
        )
        self.max_workers = max_workers
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI  # lazy: offline tests need no API key

            self._client = OpenAI()

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # One request for the whole batch: unlike Titan, this API embeds many
        # inputs per call, so the ThreadPoolExecutor TitanEmbedder needs is
        # unnecessary here.
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._client.embeddings.create(
                    model=self.model_id,
                    input=list(texts),
                    dimensions=DIMENSIONS,
                )
                vectors = [item.embedding for item in response.data]
                for vector in vectors:
                    if len(vector) != DIMENSIONS:
                        raise ValueError(
                            f"{self.model_id} returned {len(vector)} dimensions, schema "
                            f"expects {DIMENSIONS}."
                        )
                return vectors
            except Exception as exc:  # noqa: BLE001 -- retried below, re-raised at end
                if _is_fatal(exc):
                    raise
                last_error = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BASE_DELAY * (2**attempt) + random.uniform(0, 0.25))

        raise RuntimeError(
            f"OpenAI embedding failed after {_MAX_ATTEMPTS} attempts"
        ) from last_error


def _is_fatal(exc: Exception) -> bool:
    """Distinguish "retrying will not help" from transient failures."""
    name = type(exc).__name__
    if name in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException"):
        return True
    return isinstance(exc, ValueError)


class FakeEmbedder:
    """Deterministic stand-in for Titan, for tests and offline development.

    Not semantic -- it cannot be. It guarantees only the two properties the
    tests actually depend on: identical text yields an identical vector, and
    distinct text yields a well-separated one.

    That is sufficient to test isolation, because isolation is enforced by branch
    lineage rather than by distance. A test embeds a query with the same string
    as the belief it is hunting for, which pins that row at distance zero -- so if
    the row is reachable at all, it ranks first. A test asserting "not found"
    therefore cannot pass merely because the similarity was poor.
    """

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(seed[:16], "big"))
        vector = [rng.gauss(0.0, 1.0) for _ in range(DIMENSIONS)]
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


def get_embedder() -> Embedder:
    """Return the real embedder, or the fake one when ``BMEM_FAKE_EMBEDDINGS`` is set.

    The escape hatch exists so the isolation and merge tests can run against a
    live cluster without also requiring Bedrock model access -- the two
    dependencies are approved on different timelines.
    """
    if os.environ.get("BMEM_FAKE_EMBEDDINGS"):
        return FakeEmbedder()
    if os.environ.get("BMEM_EMBEDDING_PROVIDER", "titan") == "openai":
        return OpenAIEmbedder()
    return TitanEmbedder()
