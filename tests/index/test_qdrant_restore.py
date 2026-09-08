from openlaw.index.qdrant_restore import (
    ACTS_INDIA,
    CollectionSpec,
    build_collection_payload,
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
