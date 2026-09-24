import httpx
import pytest

from openlaw.corpus.schema import JudgmentChunk
from openlaw.index.hc_upsert import (
    HC_COLLECTION,
    build_hc_collection_payload,
    build_hc_point,
    ensure_hc_collection,
    upsert_hc_points,
)
from openlaw.index.sparse import bm25_vector
from openlaw.index.upsert import point_id

PDF_URL = "https://phhc-example.gov.in/x.pdf"


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("PUT", "https://qdrant.test/collections/x")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=request, response=response
            )


class FakeClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.put_calls: list[dict] = []

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        self.put_calls.append({"url": url, **kwargs})
        return FakeResponse(self.status_code)


def _chunk() -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "PHHC010641322019_001",
            "case_id": "PHHC010641322019",
            "chunk_index": 1,
            "text": "the writ petition is allowed",
            "text_original": "the writ petition is allowed",
            "court_code": "3_22",
            "judges": ["A.B. SHARMA"],
            "decision_date": "2019-05-10",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def _sparse() -> dict[str, list]:
    return bm25_vector("the writ petition is allowed", avg_len=6.0)


def test_collection_name_can_hold_further_high_courts() -> None:
    assert HC_COLLECTION == "hc_judgments_v1"


def test_collection_payload_declares_1024_dim_cosine_dense_vector() -> None:
    payload = build_hc_collection_payload()
    assert payload["vectors"]["dense"]["size"] == 1024
    assert payload["vectors"]["dense"]["distance"] == "Cosine"


def test_collection_payload_declares_sparse_vector_with_idf_modifier() -> None:
    payload = build_hc_collection_payload()
    assert payload["sparse_vectors"]["sparse"]["modifier"] == "idf"


def test_ensure_hc_collection_tolerates_already_exists() -> None:
    client = FakeClient(status_code=409)
    ensure_hc_collection("http://qdrant.test", client)  # must not raise
    assert client.put_calls[0]["url"] == "http://qdrant.test/collections/hc_judgments_v1"


def test_ensure_hc_collection_raises_on_a_real_server_error() -> None:
    client = FakeClient(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        ensure_hc_collection("http://qdrant.test", client)


def test_point_carries_dense_and_sparse_vectors() -> None:
    point = build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)
    assert len(point["vector"]["dense"]) == 1024
    assert point["vector"]["sparse"] == _sparse()


def test_point_payload_carries_court_code_for_jurisdiction_filtering() -> None:
    """High Court judgments bind only within their own jurisdiction -- this is
    the field retrieval filters on, not decoration."""
    payload = build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)[
        "payload"
    ]
    assert payload["court_code"] == "3_22"


def test_point_payload_carries_the_source_url() -> None:
    payload = build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)[
        "payload"
    ]
    assert payload["source_url"] == PDF_URL


def test_point_payload_carries_corpus_version_and_case_id() -> None:
    payload = build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)[
        "payload"
    ]
    assert payload["corpus_version"] == "v2026.08"
    assert payload["case_id"] == "PHHC010641322019"


def test_point_id_matches_the_shared_deterministic_scheme() -> None:
    """Reuses `openlaw.index.upsert.point_id` -- not a second ID scheme."""
    point = build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)
    assert point["id"] == point_id(_chunk().chunk_id)


def test_point_includes_the_headnote_when_available() -> None:
    payload = build_hc_point(
        _chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote="Writ: maintainability"
    )["payload"]
    assert payload["headnote"] == "Writ: maintainability"


def test_upsert_hc_points_waits_for_the_write_to_be_applied() -> None:
    client = FakeClient()
    points = [build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)]

    upsert_hc_points(points, "http://qdrant.test", client)

    assert client.put_calls[0]["params"] == {"wait": "true"}
    assert client.put_calls[0]["json"] == {"points": points}
    assert client.put_calls[0]["url"] == "http://qdrant.test/collections/hc_judgments_v1/points"


def test_upsert_hc_points_raises_on_a_server_error() -> None:
    client = FakeClient(status_code=500)
    points = [build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)]

    with pytest.raises(httpx.HTTPStatusError):
        upsert_hc_points(points, "http://qdrant.test", client)
