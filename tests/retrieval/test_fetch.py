"""Tests for re-fetching carried chunks by id (Task 9).

Mocks the HTTP layer throughout -- no live Qdrant. The fake client's `.json()`
shape mirrors Qdrant's real points-RETRIEVE response (`{"result": [...]}`, a
flat list), which is distinct from the points-QUERY response
`tests/retrieval/test_search.py` fakes (`{"result": {"points": [...]}}`) --
`fetch_chunks_by_id` calls the retrieve endpoint, not query.
"""

import logging
from typing import Any

import pytest

from openlaw.index.upsert import point_id
from openlaw.retrieval.context import ChunkKind, RetrievedChunk
from openlaw.retrieval.fetch import fetch_chunks_by_id
from openlaw.retrieval.rerank import merge_chunks
from openlaw.retrieval.search import ACTS_INDIA_V1, HC_COLLECTION, SC_COLLECTION

_QDRANT_URL = "http://127.0.0.1:6333"


def _point(chunk_id: str, text: str = "provision text") -> dict[str, Any]:
    return {
        "id": point_id(chunk_id),
        "payload": {
            "chunk_id": chunk_id,
            "text": text,
            "source_url": f"https://www.indiacode.nic.in/{chunk_id}",
        },
    }


class _FakeResponse:
    def __init__(self, points: list[dict[str, Any]]) -> None:
        self._points = points

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"result": self._points}


class _FakeClient:
    """Records every request; answers from canned per-collection points.

    `unreachable_collections` names collections whose `.post` raises
    (simulating Qdrant being unreachable), independent of whether any point
    is configured for that collection.
    """

    def __init__(
        self,
        points_by_collection: dict[str, list[dict[str, Any]]],
        unreachable_collections: frozenset[str] = frozenset(),
    ) -> None:
        self._points_by_collection = points_by_collection
        self._unreachable_collections = unreachable_collections
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.methods_called: set[str] = set()

    def post(self, url: str, json: dict[str, Any], timeout: float) -> Any:
        self.methods_called.add("post")
        self.requests.append((url, json))
        for collection in self._unreachable_collections:
            if collection in url:
                raise ConnectionError(f"connection refused: {collection}")
        for collection, points in self._points_by_collection.items():
            if collection in url:
                return _FakeResponse(points)
        return _FakeResponse([])

    def put(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        self.methods_called.add("put")
        raise AssertionError("fetch_chunks_by_id must never write")

    def delete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        self.methods_called.add("delete")
        raise AssertionError("fetch_chunks_by_id must never write")


def test_resolves_ids_via_the_deterministic_point_id() -> None:
    """The request body must carry `point_id(chunk_id)`, not the raw chunk id --
    that is the whole re-fetch mechanism: same chunk_id -> same point id."""
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1")]})

    result = fetch_chunks_by_id(client, _QDRANT_URL, ("act:1",))

    assert [chunk.chunk_id for chunk in result] == ["act:1"]
    sent_ids = next(body["ids"] for _, body in client.requests if body["ids"])
    assert sent_ids == [point_id("act:1")]


def test_a_carried_id_that_no_longer_exists_drops_silently() -> None:
    """A chunk re-indexed away must not raise and must not appear -- exactly
    like an id that was never retrieved at all."""
    client = _FakeClient({ACTS_INDIA_V1: []})

    result = fetch_chunks_by_id(client, _QDRANT_URL, ("act:gone",))

    assert result == ()


def test_results_are_built_by_the_shared_chunk_from_point() -> None:
    """A payload `chunk_from_point` would reject (no source_url) must be
    rejected here too -- there is no second, divergent construction path."""
    client = _FakeClient(
        {
            ACTS_INDIA_V1: [
                {
                    "id": point_id("bad"),
                    "payload": {"chunk_id": "bad", "text": "t", "source_url": ""},
                }
            ]
        }
    )

    assert fetch_chunks_by_id(client, _QDRANT_URL, ("bad",)) == ()


def test_a_carried_chunk_already_in_primary_hits_appears_once() -> None:
    """Merging a re-fetched carried chunk with this turn's own primary hits,
    via the same `merge_chunks` the turn assembly uses, must not duplicate an
    id both sides agree on -- and the primary (fresh) copy wins."""
    primary = (
        RetrievedChunk(
            chunk_id="act:1",
            text="fresh text from this turn's own search",
            source_url="https://www.indiacode.nic.in/act:1",
            score=0.9,
            kind=ChunkKind.PROVISION,
        ),
    )
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1", text="stale carried text")]})

    carried = fetch_chunks_by_id(client, _QDRANT_URL, ("act:1",))
    merged = merge_chunks(primary, carried)

    assert [chunk.chunk_id for chunk in merged] == ["act:1"]
    assert merged[0].text == "fresh text from this turn's own search"


def test_a_carried_id_that_resolves_with_a_changed_payload_returns_the_current_form() -> None:
    """No cached copy is ever kept -- a re-indexed chunk's CURRENT text and
    source_url come back, not whatever an earlier turn originally saw."""
    client = _FakeClient(
        {SC_COLLECTION: [_point("case:1", text="the re-indexed, corrected headnote")]}
    )

    result = fetch_chunks_by_id(client, _QDRANT_URL, ("case:1",))

    assert result[0].text == "the re-indexed, corrected headnote"


def test_issues_no_write_of_any_kind() -> None:
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1")]})

    fetch_chunks_by_id(client, _QDRANT_URL, ("act:1",))

    assert client.methods_called == {"post"}


def test_never_touches_the_retired_acts_india_collection() -> None:
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1")]})

    fetch_chunks_by_id(client, _QDRANT_URL, ("act:1",))

    urls = {url for url, _ in client.requests}
    assert not any(
        url.rstrip("/").endswith("/collections/acts_india/points") for url in urls
    )
    assert any(ACTS_INDIA_V1 in url for url in urls)


def test_qdrant_unreachable_during_refetch_returns_empty_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transport/HTTP failure must not propagate into the caller -- the new
    question can still be answered, only the carried history goes empty."""
    client = _FakeClient(
        {}, unreachable_collections=frozenset({ACTS_INDIA_V1, SC_COLLECTION, HC_COLLECTION})
    )

    with caplog.at_level(logging.ERROR):
        result = fetch_chunks_by_id(client, _QDRANT_URL, ("act:1",))

    assert result == ()
    assert any("re-fetch failed" in record.message for record in caplog.records)


def test_a_failed_collection_is_logged_differently_from_a_plain_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The silent-empty failure mode (Task 8's flagged risk) must be made
    observable: a genuine ordinary miss (id not found) logs nothing, while a
    transport failure logs an ERROR naming the collection."""
    client = _FakeClient(
        {ACTS_INDIA_V1: []},  # ordinary miss: no error expected
        unreachable_collections=frozenset({SC_COLLECTION}),
    )

    with caplog.at_level(logging.ERROR):
        result = fetch_chunks_by_id(client, _QDRANT_URL, ("missing-id",))

    assert result == ()
    error_messages = [record.message for record in caplog.records]
    assert any(SC_COLLECTION in message for message in error_messages)
    assert not any(ACTS_INDIA_V1 in message for message in error_messages)


def test_empty_chunk_ids_makes_no_request_at_all() -> None:
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1")]})

    result = fetch_chunks_by_id(client, _QDRANT_URL, ())

    assert result == ()
    assert client.requests == []


def test_result_order_matches_the_requested_chunk_ids_order() -> None:
    client = _FakeClient({ACTS_INDIA_V1: [_point("act:1"), _point("act:2")]})

    result = fetch_chunks_by_id(client, _QDRANT_URL, ("act:2", "act:1"))

    assert [chunk.chunk_id for chunk in result] == ["act:2", "act:1"]
