"""Tests for hybrid dense+sparse Qdrant search.

Binding corrections over the original task brief (see task-2-report.md):
  * Searches `acts_india_v1` (the rebuilt, citable collection), not the broken
    `acts_india`.
  * The query-side sparse vector is built from `tokenise`/`token_index` with a
    weight of 1.0 per distinct query term -- NOT `bm25_vector`, which is the
    document-side function and would double-apply term-frequency weighting on
    top of Qdrant's `modifier: "idf"` (query-time IDF from live collection
    stats). Getting this wrong silently degrades keyword matching.
  * The verified-against-a-live-server Qdrant wire shape: within each prefetch
    entry, and in a standalone query, `using` is a SIBLING of `query`, never
    nested inside it. For a sparse vector, `query` must be wrapped in
    `{"nearest": {...}}` -- a bare `{"indices": ..., "values": ...}` at the
    query key returns HTTP 400 "Not existing vector name error".
"""

from typing import Any

import httpx
import pytest

from openlaw.index.sparse import token_index, tokenise
from openlaw.retrieval.context import ChunkKind
from openlaw.retrieval.search import (
    ACTS_INDIA_V1,
    SC_COLLECTION,
    SEARCH_COLLECTIONS,
    build_hybrid_request,
    build_sparse_query_vector,
    chunk_from_point,
    search,
)


def test_searches_the_rebuilt_citable_acts_collection() -> None:
    """Not `acts_india` -- that collection has no corpus_version and mixes two
    incompatible sparse-vector weighting schemes (see openlaw.index.sparse)."""
    assert ACTS_INDIA_V1 == "acts_india_v1"
    assert ACTS_INDIA_V1 in SEARCH_COLLECTIONS
    assert "acts_india" not in SEARCH_COLLECTIONS


def test_searches_the_supreme_court_collection() -> None:
    assert SC_COLLECTION == "sc_judgments_v1"
    assert SC_COLLECTION in SEARCH_COLLECTIONS


def test_sparse_query_vector_weights_every_distinct_term_equally() -> None:
    """Query-side weight is 1.0 per distinct term; Qdrant's `modifier: "idf"`
    supplies the inverse-document-frequency component at query time from real
    collection statistics."""
    sparse = build_sparse_query_vector("theft theft punishment")

    assert len(sparse["indices"]) == 2  # two distinct terms
    assert set(sparse["values"]) == {1.0}


def test_sparse_query_vector_matches_the_shared_tokeniser() -> None:
    sparse = build_sparse_query_vector("Section 138")
    expected_indices = sorted(
        token_index(token) for token in set(tokenise("Section 138"))
    )
    assert sparse["indices"] == expected_indices


def test_sparse_query_vector_is_empty_for_text_with_no_tokens() -> None:
    assert build_sparse_query_vector("...") == {"indices": [], "values": []}


def test_hybrid_request_prefetches_dense_and_sparse() -> None:
    """The published collections carry a sparse BM25 vector alongside dense."""
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, limit=10, court_codes=None)
    assert {entry["using"] for entry in request["prefetch"]} == {"dense", "sparse"}


def test_hybrid_request_wraps_the_sparse_prefetch_query_in_nearest() -> None:
    """Verified against the live server: a bare sparse vector at the `query` key
    returns HTTP 400 -- it must be wrapped in `{"nearest": {...}}`."""
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, limit=10, court_codes=None)
    sparse_entry = next(e for e in request["prefetch"] if e["using"] == "sparse")
    assert sparse_entry["query"] == {"nearest": sparse}


def test_hybrid_request_dense_prefetch_uses_the_bare_vector() -> None:
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, limit=10, court_codes=None)
    dense_entry = next(e for e in request["prefetch"] if e["using"] == "dense")
    assert dense_entry["query"] == [0.1] * 1024


def test_hybrid_request_fuses_with_reciprocal_rank_fusion() -> None:
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, limit=10, court_codes=None)
    assert request["query"]["fusion"] == "rrf"
    assert "using" not in request  # top-level `using` is only valid for a single vector


def test_hybrid_request_asks_for_payloads_but_not_vectors() -> None:
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, limit=10, court_codes=None)
    assert request["with_payload"] is True
    assert request["with_vector"] is False


def test_hybrid_request_applies_a_court_filter_when_given() -> None:
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request(
        [0.1] * 1024, sparse, limit=10, court_codes=("SCI",)
    )
    condition = request["filter"]["must"][0]
    assert condition["key"] == "court_code"
    assert condition["match"]["any"] == ["SCI"]


def test_hybrid_request_omits_the_filter_when_no_courts_given() -> None:
    sparse = build_sparse_query_vector("theft")
    request = build_hybrid_request([0.1] * 1024, sparse, 10, None)
    assert "filter" not in request


def test_chunk_from_point_maps_a_provision_payload() -> None:
    point = {
        "score": 0.87,
        "payload": {
            "chunk_id": "IND_central_2385_388",
            "text": "An order of a District Judge ...",
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
            "title": "The Indian Succession Act, 1925",
        },
    }
    chunk = chunk_from_point(point)
    assert chunk is not None
    assert chunk.kind is ChunkKind.PROVISION
    assert chunk.score == 0.87


def test_chunk_from_point_maps_a_judgment_payload() -> None:
    point = {
        "score": 0.75,
        "payload": {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "text": "the appeal is dismissed",
            "source_url": "https://indian-supreme-court-judgments.s3.amazonaws.com/x.pdf",
            "court_code": "SCI",
            "decision_date": "2016-12-15",
            "headnote": "Contempt: scope of powers",
        },
    }
    chunk = chunk_from_point(point)
    assert chunk is not None
    assert chunk.kind is ChunkKind.JUDGMENT
    assert chunk.headnote == "Contempt: scope of powers"


def test_chunk_from_point_rejects_a_payload_without_a_source_url() -> None:
    """Defence in depth -- such a point should never have been indexed."""
    point = {"score": 0.9, "payload": {"chunk_id": "x", "text": "t", "source_url": ""}}
    assert chunk_from_point(point) is None


def test_chunk_from_point_rejects_a_payload_without_text() -> None:
    point = {
        "score": 0.9,
        "payload": {"chunk_id": "x", "text": "", "source_url": "https://x.gov.in"},
    }
    assert chunk_from_point(point) is None


def test_chunk_from_point_rejects_a_missing_payload() -> None:
    assert chunk_from_point({"score": 0.9, "payload": None}) is None


class _FakeResponse:
    def __init__(self, points: list[dict[str, Any]]) -> None:
        self._points = points

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"result": {"points": self._points}}


class _FakeClient:
    """Records every POST it receives and answers with canned per-collection hits."""

    def __init__(self, points_by_collection: dict[str, list[dict[str, Any]]]) -> None:
        self._points_by_collection = points_by_collection
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.requests.append((url, json))
        for collection, points in self._points_by_collection.items():
            if collection in url:
                return _FakeResponse(points)
        return _FakeResponse([])


def test_search_queries_every_collection_and_merges_by_score() -> None:
    client = _FakeClient(
        {
            ACTS_INDIA_V1: [
                {
                    "score": 0.5,
                    "payload": {
                        "chunk_id": "a1",
                        "text": "provision text",
                        "source_url": "https://indiacode.nic.in/a1",
                    },
                }
            ],
            SC_COLLECTION: [
                {
                    "score": 0.9,
                    "payload": {
                        "chunk_id": "j1",
                        "case_id": "case1",
                        "text": "judgment text",
                        "source_url": "https://s3.amazonaws.com/j1",
                    },
                }
            ],
        }
    )

    result = search(client, "http://127.0.0.1:6333", "theft", [0.1] * 1024, limit=10)

    assert [chunk.chunk_id for chunk in result] == ["j1", "a1"]
    assert len(client.requests) == 2
    urls = {url for url, _ in client.requests}
    assert any(ACTS_INDIA_V1 in url for url in urls)
    assert any(SC_COLLECTION in url for url in urls)


def test_search_respects_the_limit_across_merged_collections() -> None:
    many_points = [
        {
            "score": float(i),
            "payload": {
                "chunk_id": f"c{i}",
                "text": "t",
                "source_url": "https://indiacode.nic.in/x",
            },
        }
        for i in range(20)
    ]
    client = _FakeClient({ACTS_INDIA_V1: many_points, SC_COLLECTION: []})

    result = search(client, "http://127.0.0.1:6333", "theft", [0.1] * 1024, limit=3)

    assert len(result) == 3
    assert [chunk.score for chunk in result] == [19.0, 18.0, 17.0]


def test_search_builds_a_sparse_vector_from_the_query_text() -> None:
    """The sparse prefetch must reflect the raw query text, not the dense
    embedding -- the two are built from independent inputs."""
    client = _FakeClient({ACTS_INDIA_V1: [], SC_COLLECTION: []})

    search(client, "http://127.0.0.1:6333", "theft punishment", [0.1] * 1024, limit=5)

    _, body = client.requests[0]
    sparse_entry = next(e for e in body["prefetch"] if e["using"] == "sparse")
    expected = build_sparse_query_vector("theft punishment")
    assert sparse_entry["query"] == {"nearest": expected}


def test_search_drops_unusable_points_silently() -> None:
    client = _FakeClient(
        {
            ACTS_INDIA_V1: [{"score": 0.9, "payload": {"chunk_id": "x", "text": ""}}],
            SC_COLLECTION: [],
        }
    )
    result = search(client, "http://127.0.0.1:6333", "theft", [0.1] * 1024, limit=5)
    assert result == ()


@pytest.mark.integration
def test_search_raises_on_a_transport_error() -> None:
    """A real `httpx.Client` against an unreachable URL raises, rather than the
    search silently returning nothing."""
    with httpx.Client(timeout=1) as client:
        with pytest.raises(httpx.HTTPError):
            search(client, "http://127.0.0.1:1", "theft", [0.1] * 1024, limit=1)
