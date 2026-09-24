"""Integration tests for hybrid search against the live Qdrant collections.

Read-only: never writes to, deletes, or reconfigures a collection. Every test
here is `@pytest.mark.integration` and skips cleanly (not fails) when Qdrant is
unreachable -- CI must never be blocked on a live service.
"""

from collections.abc import Iterator

import httpx
import pytest

from openlaw.retrieval.query import StubQueryEmbedder
from openlaw.retrieval.search import ACTS_INDIA_V1, SC_COLLECTION, search

QDRANT_URL = "http://127.0.0.1:6333"


@pytest.fixture(scope="module")
def live_client() -> Iterator[httpx.Client]:
    """A real HTTP client against Qdrant -- skips if unreachable."""
    with httpx.Client(timeout=10) as client:
        try:
            for collection in (ACTS_INDIA_V1, SC_COLLECTION):
                client.get(f"{QDRANT_URL}/collections/{collection}").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.skip(f"Qdrant is not reachable: {exc}")
        yield client


@pytest.mark.integration
def test_hybrid_search_returns_chunks_with_citable_payloads(
    live_client: httpx.Client,
) -> None:
    """Every hit must carry a non-empty `chunk_id`, `text`, and `source_url` --
    the citation contract `RetrievedChunk` enforces at construction time."""
    embedder = StubQueryEmbedder()
    vector = embedder.embed_query("punishment for theft")

    result = search(live_client, QDRANT_URL, "punishment for theft", vector, limit=5)

    assert len(result) > 0
    for chunk in result:
        assert chunk.chunk_id
        assert chunk.text
        assert chunk.source_url


@pytest.mark.integration
def test_hybrid_search_respects_the_requested_limit(live_client: httpx.Client) -> None:
    result = search(
        live_client, QDRANT_URL, "how much notice before termination", [0.05] * 1024, limit=3
    )
    assert len(result) <= 3


@pytest.mark.integration
def test_hybrid_search_never_writes_to_the_collection(live_client: httpx.Client) -> None:
    """Sanity check on the read-only constraint: the collections' point counts
    are unchanged by running a search."""
    before = {
        name: live_client.get(f"{QDRANT_URL}/collections/{name}").json()["result"][
            "points_count"
        ]
        for name in (ACTS_INDIA_V1, SC_COLLECTION)
    }

    search(live_client, QDRANT_URL, "security deposit refund", [0.02] * 1024, limit=5)

    after = {
        name: live_client.get(f"{QDRANT_URL}/collections/{name}").json()["result"][
            "points_count"
        ]
        for name in (ACTS_INDIA_V1, SC_COLLECTION)
    }
    assert before == after
