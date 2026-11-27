"""Unit tests for `scripts/export_corpus.py`.

Hermetic: no network, no Qdrant. The scroll loop is driven by
`httpx.MockTransport` serving synthetic pages, which is also how the
resume-after-interruption behaviour is asserted without a 7M-point corpus.

The script lives outside the `openlaw` package (it is a driver, not a
library), so it is loaded from its file path rather than imported by name --
the same pattern as `tests/scripts/test_build_sc_index.py`.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import numpy as np
import pyarrow.parquet as pq
import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.index.publish import ExportTarget, resolve_targets, target_lock
from openlaw.index.sparse import SPARSE_VECTOR_NAME

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "export_corpus.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_corpus = _load()

QDRANT = "http://qdrant.test:6333"
VERSION = "v2026.08"
SC = ExportTarget(key="sc_judgments_v1", collection="sc_judgments_v1", court_code=None)
KARNATAKA = ExportTarget(
    key="hc_judgments_v1/karnataka", collection="hc_judgments_v1", court_code="29_3"
)


def _options(tmp_path: Path, rows_per_shard: int = 100, limit: int | None = None):
    return export_corpus.ExportOptions(
        out_dir=tmp_path / "out",
        checkpoint_dir=tmp_path / "ckpt",
        rows_per_shard=rows_per_shard,
        limit=limit,
    )


def _point(index: int) -> dict[str, Any]:
    chunk_id = f"chunk-{index}"
    return {
        "id": chunk_id,
        "vector": {
            "dense": [index / 10_000.0] * EMBEDDING_DIMENSIONS,
            SPARSE_VECTOR_NAME: {"indices": [index], "values": [1.25]},
        },
        "payload": {"chunk_id": chunk_id, "text": f"para {index}", "court_code": "29_3"},
    }


def _qdrant_serving(total: int, page: int = 10) -> tuple[httpx.Client, list[dict]]:
    """Return a client serving `total` synthetic points, and the request log."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        start = int(body.get("offset") or 0)
        points = [_point(index) for index in range(start, min(start + page, total))]
        next_offset = start + page if start + page < total else None
        return httpx.Response(
            200, json={"result": {"points": points, "next_page_offset": next_offset}}
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


# --- the script only ever reads Qdrant ---------------------------------------


def test_the_export_never_issues_a_write_to_qdrant(tmp_path: Path) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200, json={"result": {"points": [_point(0)], "next_page_offset": None}}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    export_corpus.export_target(client, QDRANT, SC, _options(tmp_path), VERSION)

    assert set(methods) == {"POST"}


# --- per-court filtering ------------------------------------------------------


def test_a_court_target_filters_the_scroll_on_court_code(tmp_path: Path) -> None:
    client, seen = _qdrant_serving(total=5)
    export_corpus.export_target(client, QDRANT, KARNATAKA, _options(tmp_path), VERSION)
    assert seen[0]["filter"] == {
        "must": [{"key": "court_code", "match": {"value": "29_3"}}]
    }


def test_a_whole_collection_target_sends_no_filter(tmp_path: Path) -> None:
    client, seen = _qdrant_serving(total=5)
    export_corpus.export_target(client, QDRANT, SC, _options(tmp_path), VERSION)
    assert "filter" not in seen[0]


def test_the_scroll_asks_for_payloads_and_vectors(tmp_path: Path) -> None:
    client, seen = _qdrant_serving(total=5)
    export_corpus.export_target(client, QDRANT, SC, _options(tmp_path), VERSION)
    assert seen[0]["with_payload"] is True
    assert seen[0]["with_vector"] is True


# --- sharding and sizes -------------------------------------------------------


def test_points_are_written_into_shards_of_the_requested_size(tmp_path: Path) -> None:
    client, _ = _qdrant_serving(total=250)
    options = _options(tmp_path, rows_per_shard=100)

    state = export_corpus.export_target(client, QDRANT, SC, options, VERSION)

    assert state.rows_written == 250
    assert [shard.rows for shard in state.shards] == [100, 100, 50]
    written = sorted(path.name for path in SC.shard_dir(options.out_dir).glob("*.parquet"))
    assert written == ["shard-00000.parquet", "shard-00001.parquet", "shard-00002.parquet"]


def test_every_shard_reports_a_real_byte_size(tmp_path: Path) -> None:
    client, _ = _qdrant_serving(total=120)
    options = _options(tmp_path, rows_per_shard=100)

    state = export_corpus.export_target(client, QDRANT, SC, options, VERSION)

    for shard in state.shards:
        path = SC.shard_dir(options.out_dir) / shard.name
        assert shard.bytes == path.stat().st_size > 0


def test_the_exported_shard_round_trips_the_points(tmp_path: Path) -> None:
    client, _ = _qdrant_serving(total=3)
    options = _options(tmp_path)

    export_corpus.export_target(client, QDRANT, SC, options, VERSION)
    rows = pq.read_table(
        SC.shard_dir(options.out_dir) / "shard-00000.parquet"
    ).to_pylist()

    assert [row["chunk_id"] for row in rows] == ["chunk-0", "chunk-1", "chunk-2"]
    assert json.loads(rows[1]["payload"])["text"] == "para 1"
    assert rows[1]["sparse_values"] == pytest.approx([1.25])


# --- limits -------------------------------------------------------------------


def test_limit_stops_without_scrolling_the_whole_collection(tmp_path: Path) -> None:
    client, seen = _qdrant_serving(total=10_000, page=10)
    options = _options(tmp_path, rows_per_shard=5_000, limit=25)

    state = export_corpus.export_target(client, QDRANT, SC, options, VERSION)

    assert state.rows_written == 25
    assert len(seen) == 3  # 3 pages of 10, trimmed to 25 -- not 1,000 pages


def test_trim_to_limit_is_a_no_op_without_a_limit(tmp_path: Path) -> None:
    buffer = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    trimmed, met = export_corpus.trim_to_limit(
        buffer, export_corpus.EMPTY_STATE, _options(tmp_path)
    )
    assert trimmed == buffer
    assert met is False


# --- resume -------------------------------------------------------------------


def test_an_interrupted_export_resumes_from_the_last_flushed_shard(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, rows_per_shard=100)
    failing, _ = _qdrant_serving(total=250)
    original = export_corpus.flush_shard
    calls = {"n": 0}

    def flaky(rows: list, directory: Path, index: int):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("laptop lid closed")
        return original(rows, directory, index)

    export_corpus.flush_shard = flaky
    try:
        with pytest.raises(RuntimeError):
            export_corpus.export_target(failing, QDRANT, SC, options, VERSION)
    finally:
        export_corpus.flush_shard = original

    checkpoint = json.loads(SC.checkpoint_path(options.checkpoint_dir).read_text())
    assert checkpoint["rows_written"] == 200
    assert checkpoint["finished"] is False

    resumed, seen = _qdrant_serving(total=250)
    state = export_corpus.export_target(resumed, QDRANT, SC, options, VERSION)

    assert state.rows_written == 250
    assert seen[0]["offset"] == 200  # resumed where it stopped, not from zero


def test_a_finished_export_is_not_redone(tmp_path: Path) -> None:
    options = _options(tmp_path, rows_per_shard=100)
    client, _ = _qdrant_serving(total=50)
    export_corpus.export_target(client, QDRANT, SC, options, VERSION)

    again, seen = _qdrant_serving(total=50)
    state = export_corpus.export_target(again, QDRANT, SC, options, VERSION)

    assert state.rows_written == 50
    assert seen == []


def test_the_checkpoint_records_the_corpus_version_and_the_target(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)
    client, _ = _qdrant_serving(total=5)
    export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)

    checkpoint = json.loads(
        KARNATAKA.checkpoint_path(options.checkpoint_dir).read_text()
    )
    assert checkpoint["corpus_version"] == VERSION
    assert checkpoint["target"] == "hc_judgments_v1/karnataka"
    assert checkpoint["court_code"] == "29_3"


def test_clearing_checkpoints_leaves_other_targets_alone(tmp_path: Path) -> None:
    options = _options(tmp_path)
    client, _ = _qdrant_serving(total=5)
    export_corpus.export_target(client, QDRANT, SC, options, VERSION)
    export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)

    export_corpus.clear_checkpoints((SC,), options.checkpoint_dir)

    assert not SC.checkpoint_path(options.checkpoint_dir).exists()
    assert KARNATAKA.checkpoint_path(options.checkpoint_dir).exists()


# --- the manifest -------------------------------------------------------------


def test_the_manifest_describes_the_slice_and_its_shards(tmp_path: Path) -> None:
    options = _options(tmp_path, rows_per_shard=100)
    client, _ = _qdrant_serving(total=150)
    state = export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)

    path = export_corpus.write_manifest(KARNATAKA, options, state, VERSION)
    manifest = json.loads(path.read_text())

    assert manifest["collection"] == "hc_judgments_v1"
    assert manifest["court_code"] == "29_3"
    assert manifest["points"] == 150
    assert manifest["dense_dtype"] == "float16"
    assert manifest["embedding_model"] == "voyage-4-nano"
    assert manifest["embedding_dimensions"] == EMBEDDING_DIMENSIONS
    assert [shard["name"] for shard in manifest["shards"]] == [
        "shard-00000.parquet",
        "shard-00001.parquet",
    ]


def test_the_manifest_records_each_shards_sha256(tmp_path: Path) -> None:
    from openlaw.index.publish import sha256_file

    options = _options(tmp_path, rows_per_shard=100)
    client, _ = _qdrant_serving(total=150)
    state = export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)

    path = export_corpus.write_manifest(KARNATAKA, options, state, VERSION)
    manifest = json.loads(path.read_text())

    for shard in manifest["shards"]:
        assert shard["sha256"] == sha256_file(path.parent / shard["name"])


def test_the_manifest_hash_is_what_gets_pinned(tmp_path: Path) -> None:
    from openlaw.index.publish import sha256_file

    options = _options(tmp_path, rows_per_shard=100)
    client, _ = _qdrant_serving(total=150)
    state = export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)
    path = export_corpus.write_manifest(KARNATAKA, options, state, VERSION)

    line = export_corpus.pin_line(KARNATAKA, path)

    assert sha256_file(path) in line
    assert KARNATAKA.key in line


def test_a_manifest_is_refused_for_a_shard_with_no_recorded_hash(tmp_path: Path) -> None:
    """A resumed pre-hash checkpoint must not publish an unverifiable shard."""
    options = _options(tmp_path, rows_per_shard=100)
    client, _ = _qdrant_serving(total=150)
    state = export_corpus.export_target(client, QDRANT, KARNATAKA, options, VERSION)
    legacy = export_corpus.ShardRecord(name="shard-00000.parquet", rows=1, bytes=1)

    with pytest.raises(ValueError, match="sha256"):
        export_corpus.write_manifest(
            KARNATAKA, options, replace_shards(state, (legacy,)), VERSION
        )


# --- the float16 gate ---------------------------------------------------------


def _sampling_client(vectors: np.ndarray) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        points = [{"vector": {"dense": vector.tolist()}} for vector in vectors]
        return httpx.Response(200, json={"result": {"points": points}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_unit_norm_vectors_pass_the_float16_gate() -> None:
    generator = np.random.default_rng(1)
    vectors = generator.standard_normal((32, EMBEDDING_DIMENSIONS)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    export_corpus.verify_float16(_sampling_client(vectors), QDRANT, (SC,), 32, 0.9999)


def test_a_vector_space_float16_would_damage_refuses_to_export() -> None:
    # Huge dynamic range within one vector: float16 cannot hold both ends, so
    # the round-trip cosine collapses and the gate must stop the export
    # rather than publish silently-degraded vectors.
    vectors = np.zeros((2, EMBEDDING_DIMENSIONS), dtype=np.float32)
    vectors[:, 0] = 7.0e4
    vectors[:, 1] = 1.0e-6

    with pytest.raises(SystemExit, match="REFUSING"):
        export_corpus.verify_float16(
            _sampling_client(vectors), QDRANT, (SC,), 2, 0.999999
        )


def test_the_gate_reports_the_worst_case_not_the_average(capsys) -> None:
    generator = np.random.default_rng(2)
    vectors = generator.standard_normal((16, EMBEDDING_DIMENSIONS)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    export_corpus.verify_float16(_sampling_client(vectors), QDRANT, (SC,), 16, 0.9999)

    assert "worst cosine" in capsys.readouterr().out


# --- the command line ---------------------------------------------------------


def test_split_list_handles_spaces_and_emptiness() -> None:
    assert export_corpus.split_list("karnataka, delhi ") == ["karnataka", "delhi"]
    assert export_corpus.split_list("") is None
    assert export_corpus.split_list(None) is None


def test_an_unknown_court_exits_without_touching_qdrant() -> None:
    assert export_corpus.main(["--courts", "atlantis"]) == 2


def test_the_publishers_snapshot_cannot_be_exported() -> None:
    assert export_corpus.main(["--collections", "acts_india"]) == 2


def test_a_target_another_worker_holds_is_refused_before_qdrant_is_touched(
    tmp_path: Path, capsys
) -> None:
    """Two exports of one target would write the same shards; the second stops.

    Qdrant is never reached, so this also proves the refusal happens before any
    scrolling rather than part-way through a court.
    """
    target = resolve_targets(["sc_judgments_v1"], None)[0]
    with target_lock(target.checkpoint_path(tmp_path)):
        exit_code = export_corpus.main(
            [
                "--collections",
                "sc_judgments_v1",
                "--out",
                str(tmp_path / "out"),
                "--checkpoint-dir",
                str(tmp_path),
            ]
        )
    assert exit_code == 1
    assert "already being written by pid" in capsys.readouterr().out


def replace_shards(state: Any, shards: tuple[Any, ...]) -> Any:
    import dataclasses

    return dataclasses.replace(state, shards=shards)
