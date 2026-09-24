import httpx
import pytest

from openlaw.corpus.schema import JudgmentChunk
from openlaw.index.hc_upsert import (
    HC_COLLECTION,
    build_hc_collection_payload,
    build_hc_point,
    delete_hc_court_points,
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
        self.post_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        self.put_calls.append({"url": url, **kwargs})
        return FakeResponse(self.status_code)

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return FakeResponse(self.status_code)

    def delete(self, url: str, **kwargs: object) -> FakeResponse:
        self.delete_calls.append({"url": url, **kwargs})
        return FakeResponse(self.status_code)


class FakeQdrantStore:
    """An in-memory double for the tiny slice of the Qdrant HTTP surface this
    module calls -- faithful enough (it actually filters by `court_code` on
    delete) to prove `delete_hc_court_points` only removes the targeted
    court's points, not merely that it sends the expected request shape."""

    def __init__(self) -> None:
        self._points: dict[str, dict] = {}

    def put(self, url: str, *, json: dict, **kwargs: object) -> FakeResponse:
        for point in json["points"]:
            self._points[point["id"]] = point
        return FakeResponse(200)

    def post(self, url: str, *, json: dict, **kwargs: object) -> FakeResponse:
        target_court = json["filter"]["must"][0]["match"]["value"]
        surviving = {
            pid: point
            for pid, point in self._points.items()
            if point["payload"]["court_code"] != target_court
        }
        self._points = surviving
        return FakeResponse(200)

    def has_point(self, point: dict) -> bool:
        return point["id"] in self._points


def _chunk(court_code: str = "3_22", case_id: str = "PHHC010641322019") -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": f"{case_id}_001",
            "case_id": case_id,
            "chunk_index": 1,
            "text": "the writ petition is allowed",
            "text_original": "the writ petition is allowed",
            "court_code": court_code,
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


def test_delete_hc_court_points_sends_a_filtered_delete_scoped_to_one_court() -> None:
    """A --restart must clear only the rebuilt court's points, filtered on
    the `court_code` payload -- never the whole shared collection."""
    client = FakeClient(status_code=200)
    delete_hc_court_points("3_22", "http://qdrant.test", client)
    call = client.post_calls[0]
    assert call["url"] == "http://qdrant.test/collections/hc_judgments_v1/points/delete"
    assert call["params"] == {"wait": "true"}
    assert call["json"] == {"filter": {"must": [{"key": "court_code", "match": {"value": "3_22"}}]}}


def test_delete_hc_court_points_tolerates_not_found() -> None:
    """A --restart before anything has ever been indexed must not fail just
    because the collection does not exist yet."""
    client = FakeClient(status_code=404)
    delete_hc_court_points("3_22", "http://qdrant.test", client)  # must not raise


def test_delete_hc_court_points_raises_on_a_real_server_error() -> None:
    client = FakeClient(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        delete_hc_court_points("3_22", "http://qdrant.test", client)


def test_delete_hc_court_points_leaves_other_courts_points_intact() -> None:
    """The core per-court-restart guarantee: restarting one court's build must
    never destroy another already-indexed court's points. Proven against a
    store that actually applies the filter, not just against the request
    shape sent to it."""
    store = FakeQdrantStore()
    punjab_point = build_hc_point(
        _chunk(court_code="3_22", case_id="PHHC1"), [0.1] * 1024, _sparse(), PDF_URL,
        headnote=None,
    )
    delhi_point = build_hc_point(
        _chunk(court_code="7_26", case_id="DLHC1"), [0.2] * 1024, _sparse(), PDF_URL,
        headnote=None,
    )
    upsert_hc_points([punjab_point, delhi_point], "http://qdrant.test", store)

    delete_hc_court_points("7_26", "http://qdrant.test", store)

    assert store.has_point(punjab_point)
    assert not store.has_point(delhi_point)


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


# --- Robustness against transient connection drops (idle-keepalive fix) -----
#
# A multi-hour build spends minutes per batch embedding between Qdrant calls,
# so a pooled connection going stale and dying on the next write is expected,
# not exceptional. `upsert_hc_points` must recover from it via
# `openlaw.index.qdrant_http.call_with_retry`, and a genuine 4xx must still
# fail on the first attempt -- never silently retried.


class FlakyThenOkClient(FakeClient):
    """A stub that drops the connection on the first PUT, then succeeds --
    the exact shape of the incident this fix targets."""

    def __init__(self) -> None:
        super().__init__(status_code=200)
        self.attempts = 0

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        self.attempts += 1
        if self.attempts == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response"
            )
        return super().put(url, **kwargs)


def test_upsert_hc_points_recovers_from_one_dropped_connection(monkeypatch) -> None:
    """A transport failure followed by success must complete the batch, not
    crash the build."""
    monkeypatch.setattr("openlaw.index.qdrant_http.time.sleep", lambda seconds: None)
    client = FlakyThenOkClient()
    points = [build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)]

    upsert_hc_points(points, "http://qdrant.test", client)  # must not raise

    assert client.attempts == 2
    assert client.put_calls[0]["json"] == {"points": points}


def test_upsert_hc_points_does_not_retry_a_bad_request() -> None:
    """A malformed request (4xx) must fail loudly and immediately, never
    disappear into a retry loop."""
    client = FakeClient(status_code=400)
    points = [build_hc_point(_chunk(), [0.1] * 1024, _sparse(), PDF_URL, headnote=None)]

    with pytest.raises(httpx.HTTPStatusError):
        upsert_hc_points(points, "http://qdrant.test", client)

    assert len(client.put_calls) == 1
