"""Integration tests for hybrid search against the live Qdrant collections.

Read-only: never writes to, deletes, or reconfigures a collection. Every test
here is `@pytest.mark.integration` and skips cleanly (not fails) when Qdrant is
unreachable -- CI must never be blocked on a live service.
"""

from collections.abc import Iterator

import httpx
import pytest

from openlaw.retrieval.query import StubQueryEmbedder
from openlaw.retrieval.search import ACTS_INDIA_V1, HC_COLLECTION, SC_COLLECTION, search

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


@pytest.fixture(scope="module")
def hc_live_client() -> Iterator[httpx.Client]:
    """A real HTTP client against Qdrant, skipping if `hc_judgments_v1` does not
    exist yet. Unlike `acts_india_v1`/`sc_judgments_v1`, this collection is
    populated incrementally by `scripts/build_hc_index.py` (see its checkpoint
    scheme) and so may hold anywhere from a bounded proof slice to the full
    corpus depending on the environment -- these tests only need it to exist
    and to return citable, correctly-scoped hits, not to be complete."""
    with httpx.Client(timeout=10) as client:
        try:
            client.get(f"{QDRANT_URL}/collections/{HC_COLLECTION}").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.skip(f"{HC_COLLECTION} is not built yet: {exc}")
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


@pytest.mark.integration
def test_hybrid_search_can_scope_to_a_single_high_court(
    hc_live_client: httpx.Client,
) -> None:
    """court_codes scopes hits to one jurisdiction -- a Punjab & Haryana query
    must never surface another High Court's judgments."""
    result = search(
        hc_live_client,
        QDRANT_URL,
        "writ petition maintainability",
        [0.05] * 1024,
        limit=5,
        court_codes=("3_22",),
    )
    for chunk in result:
        assert chunk.court_code == "3_22"
        assert chunk.source_url
