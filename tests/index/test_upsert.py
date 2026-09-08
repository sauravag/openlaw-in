import httpx
import pytest

from openlaw.corpus.schema import JudgmentChunk
from openlaw.index.upsert import SC_COLLECTION, build_point, upsert_points

PDF_URL = "https://indian-supreme-court-judgments.s3.amazonaws.com/x.pdf"


class FakeResponse:
    """A minimal stand-in for `httpx.Response` — no network involved."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("PUT", "https://qdrant.test/collections/x/points")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=request, response=response
            )


class FakeClient:
    """A stub `httpx.Client` recording PUT calls, returning canned responses."""

    def __init__(self) -> None:
        self.put_calls: list[dict] = []

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        self.put_calls.append({"url": url, **kwargs})
        return FakeResponse(200)


def _chunk() -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is G dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def test_collection_name_is_versioned() -> None:
    assert SC_COLLECTION == "sc_judgments_v1"


def test_point_carries_the_vector_under_the_dense_name() -> None:
    point = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    assert len(point["vector"]["dense"]) == 1024


def test_point_payload_carries_the_source_url() -> None:
    """Without this the citation cannot be rendered or verified."""
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]
    assert payload["source_url"] == PDF_URL


def test_point_payload_carries_filterable_metadata() -> None:
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]

    assert payload["court_code"] == "SCI"
    assert payload["case_id"] == "2016_INSC_1151"
    assert payload["decision_date"] == "2016-12-15"
    assert payload["corpus_version"] == "v2026.08"
    assert payload["language_code"] == "en"


def test_point_payload_carries_text_and_retains_the_original() -> None:
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]

    assert payload["text"] == "the appeal is dismissed"
    assert payload["text_original"] == "the appeal is G dismissed"


def test_point_includes_the_headnote_when_available() -> None:
    payload = build_point(
        _chunk(), [0.1] * 1024, PDF_URL, headnote="Contempt: scope of powers"
    )["payload"]
    assert payload["headnote"] == "Contempt: scope of powers"


def test_point_id_is_stable_for_the_same_chunk() -> None:
    first = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    second = build_point(_chunk(), [0.2] * 1024, PDF_URL, headnote=None)
    assert first["id"] == second["id"]


def test_point_ids_differ_across_chunks() -> None:
    other = JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_002", "case_id": "2016_INSC_1151",
            "chunk_index": 2, "text": "t", "text_original": "t",
            "court_code": "SCI", "judges": [], "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    first = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    second = build_point(other, [0.1] * 1024, PDF_URL, headnote=None)
    assert first["id"] != second["id"]


def test_upsert_points_waits_for_the_write_to_be_applied() -> None:
    """Without `wait=true`, Qdrant defaults to `wait=false`, so a driver's own
    "done" line — or a validation run started right after — could observe the
    collection before this write actually lands."""
    client = FakeClient()
    points = [build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)]

    upsert_points(points, "http://qdrant.test", client)

    assert len(client.put_calls) == 1
    assert client.put_calls[0]["params"] == {"wait": "true"}
    assert client.put_calls[0]["json"] == {"points": points}


def test_upsert_points_raises_on_a_server_error() -> None:
    class FailingClient(FakeClient):
        def put(self, url: str, **kwargs: object) -> FakeResponse:
            super().put(url, **kwargs)
            return FakeResponse(500)

    with pytest.raises(httpx.HTTPStatusError):
        upsert_points(
            [build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)],
            "http://qdrant.test",
            FailingClient(),
        )
