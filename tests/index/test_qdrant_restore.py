import httpx
import pytest

from openlaw.index.qdrant_restore import (
    ACTS_INDIA,
    CollectionSpec,
    build_collection_payload,
    restore_collection,
    shard_snapshot_urls,
)


def test_acts_india_spec_matches_the_published_collection() -> None:
    assert ACTS_INDIA.name == "acts_india"
    assert ACTS_INDIA.shards == 2


def test_payload_declares_voyage_4_vector_geometry() -> None:
    """1024 dimensions and cosine distance, or vectors are not comparable."""
    payload = build_collection_payload(ACTS_INDIA)

    assert payload["shard_number"] == 2
    assert payload["vectors"]["dense"]["size"] == 1024
    assert payload["vectors"]["dense"]["distance"] == "Cosine"


def test_payload_enables_on_disk_storage_and_int8_quantization() -> None:
    dense = build_collection_payload(ACTS_INDIA)["vectors"]["dense"]

    assert dense["on_disk"] is True
    assert dense["quantization_config"]["scalar"]["type"] == "int8"


def test_payload_declares_the_sparse_vector_for_hybrid_search() -> None:
    """The published collections carry a named sparse vector for BM25."""
    assert "sparse" in build_collection_payload(ACTS_INDIA)["sparse_vectors"]


def test_shard_snapshot_urls_are_built_from_the_mirror() -> None:
    spec = CollectionSpec(
        name="acts_india", shards=2, mirror="https://mirror.test/qdrant/acts_india"
    )
    snapshot_url, checksum_url = shard_snapshot_urls(spec, shard=1, snapshot="snap.tar")

    assert snapshot_url == "https://mirror.test/qdrant/acts_india/shard-1/snap.tar"
    assert checksum_url == (
        "https://mirror.test/qdrant/acts_india/shard-1/snap.tar.checksum"
    )


class FakeResponse:
    """A minimal stand-in for `httpx.Response` — no network involved."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> dict:
        return self._json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("PUT", "https://qdrant.test/collections/acts_india")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=request, response=response
            )


class FakeClient:
    """A stub `httpx.Client` recording calls, returning canned responses."""

    def __init__(self, create_status: int = 200) -> None:
        self.create_status = create_status
        self.put_calls: list[dict] = []
        self.get_calls: list[str] = []

    def put(self, url: str, json: dict | None = None, timeout: float | None = None) -> FakeResponse:
        self.put_calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/collections/acts_india"):
            return FakeResponse(self.create_status)
        return FakeResponse(200)

    def get(self, url: str) -> FakeResponse:
        self.get_calls.append(url)
        if url.endswith("index.json"):
            return FakeResponse(200, json_data={"snapshot": "snap.tar"})
        if url.endswith(".checksum"):
            return FakeResponse(200, text="deadbeef")
        raise AssertionError(f"unexpected GET {url}")


def _recover_urls(qdrant_url: str) -> list[str]:
    return [
        f"{qdrant_url}/collections/{ACTS_INDIA.name}/shards/{shard}/snapshots/recover"
        for shard in range(ACTS_INDIA.shards)
    ]


def test_restore_collection_treats_409_as_success_and_recovers_shards() -> None:
    """A pre-existing collection (409) must not abort the restore.

    This is the exact failure mode a previously-interrupted restore leaves
    behind: the collection exists with 0 points, and re-running must still
    reach shard recovery instead of dying on the create-collection call.
    """
    client = FakeClient(create_status=409)

    restore_collection(ACTS_INDIA, "http://qdrant.test", client)

    called_urls = [call["url"] for call in client.put_calls]
    for recover_url in _recover_urls("http://qdrant.test"):
        assert recover_url in called_urls


def test_restore_collection_raises_on_other_create_errors() -> None:
    client = FakeClient(create_status=500)

    with pytest.raises(httpx.HTTPStatusError):
        restore_collection(ACTS_INDIA, "http://qdrant.test", client)

    # Shard recovery must never be attempted when creation fails for a real reason.
    called_urls = [call["url"] for call in client.put_calls]
    for recover_url in _recover_urls("http://qdrant.test"):
        assert recover_url not in called_urls


def test_restore_collection_succeeds_on_fresh_create() -> None:
    client = FakeClient(create_status=200)

    restore_collection(ACTS_INDIA, "http://qdrant.test", client)

    called_urls = [call["url"] for call in client.put_calls]
    assert f"http://qdrant.test/collections/{ACTS_INDIA.name}" in called_urls
    for recover_url in _recover_urls("http://qdrant.test"):
        assert recover_url in called_urls
