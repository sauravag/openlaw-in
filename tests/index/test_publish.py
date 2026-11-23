"""Unit tests for `openlaw.index.publish` -- the corpus publishing contract.

Hermetic: no network, no Qdrant, no Hugging Face. Everything here is pure
data shaping plus `tmp_path`.
"""

import json
import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from openlaw.corpus.high_courts import HIGH_COURTS
from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.index.publish import (
    EMBEDDING_COMPATIBILITY_WARNING,
    PUBLISHED_COLLECTIONS,
    PUBLISHED_COURT_SLUGS,
    PUBLISHED_POINT_COUNTS,
    ExportTarget,
    build_published_collection_payload,
    court_code_filter,
    export_schema,
    float16_worst_cosine,
    point_to_row,
    read_checkpoint,
    resolve_targets,
    row_to_point,
    rows_to_table,
    shard_name,
    write_checkpoint,
)
from openlaw.index.sparse import SPARSE_VECTOR_NAME
from openlaw.index.upsert import point_id

# --- the warning that must travel with the data ------------------------------


def test_compatibility_warning_names_the_exact_embedder() -> None:
    assert "voyage-4-nano" in EMBEDDING_COMPATIBILITY_WARNING
    assert "1024" in EMBEDDING_COMPATIBILITY_WARNING


def test_compatibility_warning_names_the_measured_failure_mode() -> None:
    # The whole point: it does not error, it silently returns near-garbage.
    assert "15" in EMBEDDING_COMPATIBILITY_WARNING
    assert "voyage-4-lite" in EMBEDDING_COMPATIBILITY_WARNING
    assert "silent" in EMBEDDING_COMPATIBILITY_WARNING.lower()


def test_compatibility_warning_mentions_matryoshka_truncation() -> None:
    lowered = EMBEDDING_COMPATIBILITY_WARNING.lower()
    assert "matryoshka" in lowered
    assert "renormalis" in lowered or "renormaliz" in lowered


# --- published inventory ------------------------------------------------------


def test_published_collections_cover_the_three_a_user_needs() -> None:
    assert set(PUBLISHED_COLLECTIONS) == {
        "acts_india_v1",
        "sc_judgments_v1",
        "hc_judgments_v1",
    }


def test_the_publishers_own_snapshot_is_never_published() -> None:
    assert "acts_india" not in PUBLISHED_COLLECTIONS


def test_published_shard_counts_match_the_live_collections() -> None:
    assert PUBLISHED_COLLECTIONS["acts_india_v1"].shards == 2
    assert PUBLISHED_COLLECTIONS["sc_judgments_v1"].shards == 1
    assert PUBLISHED_COLLECTIONS["hc_judgments_v1"].shards == 1


def test_every_published_court_is_a_registered_high_court() -> None:
    for slug in PUBLISHED_COURT_SLUGS:
        assert slug in HIGH_COURTS


def test_measured_per_court_counts_sum_to_the_measured_collection_total() -> None:
    per_court = sum(
        PUBLISHED_POINT_COUNTS[f"hc_judgments_v1/{slug}"] for slug in PUBLISHED_COURT_SLUGS
    )
    assert per_court == 15_097_324


def test_every_target_key_has_a_measured_point_count() -> None:
    for target in resolve_targets(None, None):
        assert PUBLISHED_POINT_COUNTS[target.key] > 0


# --- target resolution --------------------------------------------------------


def test_default_targets_are_every_collection_and_every_published_court() -> None:
    keys = [target.key for target in resolve_targets(None, None)]
    assert keys == [
        "acts_india_v1",
        "sc_judgments_v1",
        "hc_judgments_v1/punjab-and-haryana",
        "hc_judgments_v1/delhi",
        "hc_judgments_v1/bombay",
        "hc_judgments_v1/karnataka",
        "hc_judgments_v1/madras",
        "hc_judgments_v1/patna",
        "hc_judgments_v1/kerala",
    ]


def test_selecting_courts_implies_the_high_court_collection_only() -> None:
    targets = resolve_targets(None, ["karnataka", "delhi"])
    assert [target.key for target in targets] == [
        "hc_judgments_v1/delhi",
        "hc_judgments_v1/karnataka",
    ]


def test_selecting_a_court_carries_the_vaquill_court_code() -> None:
    (target,) = resolve_targets(None, ["karnataka"])
    assert target.collection == "hc_judgments_v1"
    assert target.court_code == HIGH_COURTS["karnataka"].vaquill_court_code


def test_selecting_statutes_and_supreme_court_skips_the_high_courts() -> None:
    targets = resolve_targets(["acts_india_v1", "sc_judgments_v1"], None)
    assert [target.key for target in targets] == ["acts_india_v1", "sc_judgments_v1"]


def test_naming_the_high_court_collection_without_courts_takes_every_court() -> None:
    targets = resolve_targets(["hc_judgments_v1"], None)
    assert len(targets) == len(PUBLISHED_COURT_SLUGS)


def test_unknown_collection_is_refused_naming_the_valid_ones() -> None:
    with pytest.raises(ValueError, match="sc_judgments_v1"):
        resolve_targets(["nope"], None)


def test_the_publishers_snapshot_cannot_be_named_as_a_collection() -> None:
    with pytest.raises(ValueError, match="acts_india"):
        resolve_targets(["acts_india"], None)


def test_unknown_court_is_refused_naming_the_published_ones() -> None:
    with pytest.raises(ValueError, match="karnataka"):
        resolve_targets(None, ["atlantis"])


def test_a_registered_but_unbuilt_court_is_refused_rather_than_exported_empty() -> None:
    assert "sikkim" in HIGH_COURTS
    with pytest.raises(ValueError, match="sikkim"):
        resolve_targets(None, ["sikkim"])


def test_court_code_filter_is_none_for_a_whole_collection() -> None:
    assert court_code_filter(None) is None


def test_court_code_filter_matches_the_payload_field_retrieval_filters_on() -> None:
    assert court_code_filter("29_3") == {
        "must": [{"key": "court_code", "match": {"value": "29_3"}}]
    }


# --- parquet shaping ----------------------------------------------------------


def _point(chunk_id: str = "c1") -> dict:
    return {
        "id": point_id(chunk_id),
        "vector": {
            "dense": [0.1] * EMBEDDING_DIMENSIONS,
            SPARSE_VECTOR_NAME: {"indices": [7, 9], "values": [1.5, 2.0]},
        },
        "payload": {"chunk_id": chunk_id, "text": "section 498A", "court_code": "29_3"},
    }


def test_schema_stores_dense_vectors_as_float16() -> None:
    field = export_schema().field("dense")
    assert field.type.list_size == EMBEDDING_DIMENSIONS
    assert field.type.value_type == np.dtype("float16").name


def test_a_row_carries_everything_a_restore_needs() -> None:
    row = point_to_row(_point())
    assert set(row) == {"chunk_id", "dense", "sparse_indices", "sparse_values", "payload"}
    assert row["chunk_id"] == "c1"
    assert json.loads(row["payload"])["text"] == "section 498A"


def test_a_point_without_a_chunk_id_payload_is_refused_not_silently_exported() -> None:
    broken = _point()
    broken["payload"] = {"text": "no chunk id"}
    with pytest.raises(ValueError, match="chunk_id"):
        point_to_row(broken)


def test_a_point_with_the_wrong_dense_width_is_refused() -> None:
    broken = _point()
    broken["vector"]["dense"] = [0.1] * 512
    with pytest.raises(ValueError, match="1024"):
        point_to_row(broken)


def test_a_dense_only_point_exports_an_empty_sparse_vector() -> None:
    dense_only = _point()
    dense_only["vector"] = {"dense": [0.1] * EMBEDDING_DIMENSIONS}
    row = point_to_row(dense_only)
    assert row["sparse_indices"] == []
    assert row["sparse_values"] == []


def test_table_round_trips_through_parquet_losslessly(tmp_path: Path) -> None:
    rows = [point_to_row(_point("c1")), point_to_row(_point("c2"))]
    path = tmp_path / "shard-00000.parquet"

    pq.write_table(rows_to_table(rows), path, compression="zstd")
    back = pq.read_table(path).to_pylist()

    assert [row["chunk_id"] for row in back] == ["c1", "c2"]
    assert len(back[0]["dense"]) == EMBEDDING_DIMENSIONS
    assert back[0]["sparse_indices"] == [7, 9]
    assert json.loads(back[0]["payload"])["court_code"] == "29_3"


def test_export_then_restore_reconstructs_the_same_point_id(tmp_path: Path) -> None:
    original = _point("c1")
    path = tmp_path / "shard.parquet"
    pq.write_table(rows_to_table([point_to_row(original)]), path, compression="zstd")

    restored = row_to_point(pq.read_table(path).to_pylist()[0])

    assert restored["id"] == original["id"]
    assert restored["payload"] == original["payload"]
    assert restored["vector"][SPARSE_VECTOR_NAME] == {"indices": [7, 9], "values": [1.5, 2.0]}
    assert len(restored["vector"]["dense"]) == EMBEDDING_DIMENSIONS
    assert restored["vector"]["dense"][0] == pytest.approx(0.1, abs=1e-3)


def test_restored_dense_vectors_are_plain_floats_not_numpy_scalars() -> None:
    restored = row_to_point(point_to_row(_point()))
    assert all(type(value) is float for value in restored["vector"]["dense"][:4])
    assert all(type(value) is int for value in restored["vector"][SPARSE_VECTOR_NAME]["indices"])


def test_a_restored_point_is_json_serialisable_for_the_qdrant_http_api() -> None:
    json.dumps(row_to_point(point_to_row(_point())))


def test_shard_names_sort_lexicographically_in_numeric_order() -> None:
    names = [shard_name(index) for index in (0, 2, 10, 11)]
    assert names == sorted(names)
    assert names[0] == "shard-00000.parquet"


# --- float16 fidelity ---------------------------------------------------------


def test_identical_vectors_round_trip_at_cosine_one() -> None:
    vectors = np.full((4, EMBEDDING_DIMENSIONS), 1.0 / math.sqrt(EMBEDDING_DIMENSIONS))
    assert float16_worst_cosine(vectors) == pytest.approx(1.0, abs=1e-6)


def test_unit_norm_vectors_survive_float16_within_a_tiny_margin() -> None:
    generator = np.random.default_rng(0)
    vectors = generator.standard_normal((64, EMBEDDING_DIMENSIONS)).astype(np.float32)
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    assert float16_worst_cosine(vectors) > 0.9999


def test_worst_cosine_of_no_vectors_is_reported_as_nan_not_a_false_pass() -> None:
    assert math.isnan(float16_worst_cosine(np.zeros((0, EMBEDDING_DIMENSIONS))))


# --- collection creation for a restore ---------------------------------------


@pytest.mark.parametrize("collection", sorted(PUBLISHED_COLLECTIONS))
def test_every_published_collection_declares_the_idf_modifier(collection: str) -> None:
    payload = build_published_collection_payload(collection)
    assert payload["sparse_vectors"][SPARSE_VECTOR_NAME] == {"modifier": "idf"}


@pytest.mark.parametrize("collection", sorted(PUBLISHED_COLLECTIONS))
def test_every_published_collection_matches_the_live_dense_geometry(collection: str) -> None:
    dense = build_published_collection_payload(collection)["vectors"]["dense"]
    assert dense["size"] == EMBEDDING_DIMENSIONS
    assert dense["distance"] == "Cosine"
    assert dense["on_disk"] is True
    assert dense["quantization_config"] == {"scalar": {"type": "int8", "quantile": 0.99}}


def test_acts_india_v1_is_created_with_its_measured_two_shards() -> None:
    assert build_published_collection_payload("acts_india_v1")["shard_number"] == 2


def test_an_unpublished_collection_cannot_be_created_by_the_fetcher() -> None:
    with pytest.raises(ValueError, match="acts_india"):
        build_published_collection_payload("acts_india")


# --- checkpoints --------------------------------------------------------------


def test_missing_checkpoint_reads_as_none(tmp_path: Path) -> None:
    assert read_checkpoint(tmp_path / "absent.json") is None


def test_checkpoint_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "checkpoint.json"
    write_checkpoint(path, {"offset": "abc", "rows": 5})
    assert read_checkpoint(path) == {"offset": "abc", "rows": 5}


def test_checkpoint_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    write_checkpoint(path, {"rows": 1})
    assert [entry.name for entry in tmp_path.iterdir()] == ["checkpoint.json"]


def test_an_unparseable_checkpoint_fails_loudly_rather_than_silently_restarting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        read_checkpoint(path)


def test_target_checkpoint_paths_are_distinct_per_court(tmp_path: Path) -> None:
    first = ExportTarget(
        key="hc_judgments_v1/delhi", collection="hc_judgments_v1", court_code="7_26"
    )
    second = ExportTarget(
        key="hc_judgments_v1/karnataka", collection="hc_judgments_v1", court_code="29_3"
    )
    assert first.checkpoint_path(tmp_path) != second.checkpoint_path(tmp_path)
    assert first.checkpoint_path(tmp_path).suffix == ".json"


def test_a_target_key_becomes_a_safe_single_path_segment(tmp_path: Path) -> None:
    target = ExportTarget(
        key="hc_judgments_v1/delhi", collection="hc_judgments_v1", court_code="7_26"
    )
    assert "/" not in target.checkpoint_path(tmp_path).name
    assert target.shard_dir(tmp_path) == tmp_path / "hc_judgments_v1" / "delhi"


def test_an_empty_shard_is_still_a_valid_table(tmp_path: Path) -> None:
    """A court with no points must write a readable, empty shard.

    `advance` in `scripts/export_corpus.py` skips flushing an empty buffer,
    but a caller that does build one must not get a shape error -- the empty
    case is where `np.stack` would raise.
    """
    path = tmp_path / "shard-00000.parquet"
    pq.write_table(rows_to_table([]), path, compression="zstd")

    back = pq.read_table(path)

    assert back.num_rows == 0
    assert back.schema == export_schema()


# --- integrity and pinning ----------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    from openlaw.index.publish import sha256_file

    path = tmp_path / "shard.bin"
    path.write_bytes(b"section 420" * 100_000)
    assert sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_verify_sha256_refuses_a_mismatch_and_names_both_hashes(tmp_path: Path) -> None:
    from openlaw.index.publish import IntegrityError, verify_sha256

    path = tmp_path / "shard.bin"
    path.write_bytes(b"forged law")
    with pytest.raises(IntegrityError, match="shard.bin") as excinfo:
        verify_sha256(path, "0" * 64, label="shard.bin")
    assert "0" * 64 in str(excinfo.value)


def test_verify_sha256_refuses_a_malformed_expected_hash(tmp_path: Path) -> None:
    from openlaw.index.publish import IntegrityError, verify_sha256

    path = tmp_path / "x"
    path.write_bytes(b"x")
    with pytest.raises(IntegrityError, match="64"):
        verify_sha256(path, "abc", label="x")


@pytest.mark.parametrize("revision", [None, "", "main", "master", "HEAD", "Main"])
def test_an_unpinned_revision_is_refused(revision: str | None) -> None:
    from openlaw.index.publish import IntegrityError, require_pinned_revision

    with pytest.raises(IntegrityError, match="revision"):
        require_pinned_revision(revision)


def test_a_commit_or_tag_revision_is_accepted() -> None:
    from openlaw.index.publish import require_pinned_revision

    assert require_pinned_revision("a" * 40) == "a" * 40
    assert require_pinned_revision("v2026.08") == "v2026.08"


def test_load_corpus_lock_reads_revision_and_manifest_pins(tmp_path: Path) -> None:
    import json

    from openlaw.index.publish import load_corpus_lock

    path = tmp_path / "corpus.lock.json"
    path.write_text(json.dumps({"revision": "v1", "manifests": {"sc": "a" * 64}}))
    lock = load_corpus_lock(path)
    assert lock.revision == "v1"
    assert lock.manifests == {"sc": "a" * 64}


def test_load_corpus_lock_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    from openlaw.index.publish import load_corpus_lock

    lock = load_corpus_lock(tmp_path / "absent.json")
    assert lock.revision is None and lock.manifests == {}


def test_load_corpus_lock_refuses_a_bad_pin(tmp_path: Path) -> None:
    import json

    from openlaw.index.publish import IntegrityError, load_corpus_lock

    path = tmp_path / "corpus.lock.json"
    path.write_text(json.dumps({"revision": None, "manifests": {"sc": "nothex"}}))
    with pytest.raises(IntegrityError, match="sc"):
        load_corpus_lock(path)
