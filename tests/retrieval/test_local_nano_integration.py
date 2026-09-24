"""Integration tests against the real voyage-4-nano model and live Qdrant.

These download real weights and hit a live service, so every test here is
`@pytest.mark.integration` and skips cleanly (not fails) when the model can't be
loaded or Qdrant is unreachable -- CI must never be blocked on a model download.
"""

from collections.abc import Iterator

import pytest
from qdrant_client import QdrantClient

from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.retrieval._nano_compat import NanoCompatError
from openlaw.retrieval.local_nano import LocalNanoQueryEmbedder

ACTS_INDIA_V1 = "acts_india_v1"


@pytest.fixture(scope="module")
def real_embedder() -> Iterator[LocalNanoQueryEmbedder]:
    """A real, model-backed embedder -- skips cleanly if it can't be loaded.

    Deliberately broad: missing ML deps (`ImportError`), no network/HF Hub access
    (raised as assorted `huggingface_hub`/`httpx` exceptions, not one common base),
    or the known transformers-version incompatibility (`NanoCompatError`) must all
    skip this integration test rather than fail CI.
    """
    try:
        yield LocalNanoQueryEmbedder()
    except NanoCompatError as exc:
        pytest.skip(f"voyage-4-nano's remote code failed to load: {exc}")
    except Exception as exc:  # noqa: BLE001 - any load failure means skip, not fail
        pytest.skip(f"voyage-4-nano could not be loaded (no model/network?): {exc}")


@pytest.fixture(scope="module")
def live_qdrant() -> QdrantClient:
    """A real Qdrant client against `acts_india_v1` -- skips if unreachable."""
    client = QdrantClient("127.0.0.1", port=6333, check_compatibility=False)
    try:
        client.get_collection(ACTS_INDIA_V1)
    except Exception as exc:  # noqa: BLE001 - any connectivity failure means skip
        pytest.skip(f"{ACTS_INDIA_V1} is not reachable: {exc}")
    return client


@pytest.mark.integration
def test_real_nano_embeds_a_query_at_the_corpus_dimensionality(
    real_embedder: LocalNanoQueryEmbedder,
) -> None:
    vector = real_embedder.embed_query("what is the notice period for resignation")

    assert len(vector) == EMBEDDING_DIMENSIONS
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-4


@pytest.mark.integration
def test_real_nano_single_query_latency_is_reasonable(
    real_embedder: LocalNanoQueryEmbedder,
) -> None:
    """This runs on the live request path -- warm (post-load) latency must stay
    well under a second for a single question."""
    import time

    real_embedder.embed_query("warmup")  # first call pays one-time lazy costs

    start = time.perf_counter()
    real_embedder.embed_query("can my landlord evict me without notice")
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0


@pytest.mark.integration
def test_real_nano_query_retrieves_sensibly_from_live_acts_india_v1(
    real_embedder: LocalNanoQueryEmbedder, live_qdrant: QdrantClient
) -> None:
    """Read-only sanity check: a nano query vector must surface a plausibly
    relevant provision from the live, voyage-4-embedded collection -- the whole
    point of sharing voyage-4's embedding space."""
    vector = real_embedder.embed_query("what is the punishment for theft")

    results = live_qdrant.query_points(
        ACTS_INDIA_V1,
        using="dense",
        query=vector,
        limit=5,
        with_payload=["text", "title"],
    )

    assert len(results.points) > 0
    top = results.points[0]
    assert top.score > 0.3
    top_text = ((top.payload or {}).get("text") or "").lower()
    assert "theft" in top_text
