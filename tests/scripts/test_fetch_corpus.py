"""Unit tests for `scripts/fetch_corpus.py`.

Hermetic: no network, no Hugging Face, no Qdrant. Qdrant is stood up with
`httpx.MockTransport`, which also lets the clobber-refusal and batching
assertions inspect exactly what WOULD have been written -- the real
collections are never touched.

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
import pyarrow.parquet as pq
import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.index.publish import (
    ExportTarget,
    point_to_row,
    rows_to_table,
)
from openlaw.index.sparse import SPARSE_VECTOR_NAME
from openlaw.index.upsert import point_id

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fetch_corpus.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_corpus = _load()

QDRANT = "http://qdrant.test:6333"
SC = ExportTarget(key="sc_judgments_v1", collection="sc_judgments_v1", court_code=None)
DELHI = ExportTarget(
    key="hc_judgments_v1/delhi", collection="hc_judgments_v1", court_code="7_26"
)


# --- Deliverable 3: the warning must be in --help and the docstring ----------


def test_the_docstring_carries_the_embedder_warning() -> None:
    assert "voyage-4-nano" in fetch_corpus.__doc__
    assert "15.0%" in fetch_corpus.__doc__


def test_help_output_carries_the_embedder_warning() -> None:
    help_text = fetch_corpus.build_parser().format_help()
    assert "voyage-4-nano" in help_text
    assert "15.0%" in help_text
    assert "DOES NOT RAISE AN ERROR" in help_text


def test_the_repo_id_is_configurable_and_has_no_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENLAW_CORPUS_REPO_ID", raising=False)
    assert fetch_corpus.build_parser().parse_args([]).repo_id is None
    override = fetch_corpus.build_parser().parse_args(["--repo-id", "me/mine"])
    assert override.repo_id == "me/mine"


def test_courts_are_accepted_as_a_comma_separated_list() -> None:
    args = fetch_corpus.build_parser().parse_args(["--courts", "karnataka,delhi"])
    assert fetch_corpus.split_list(args.courts) == ["karnataka", "delhi"]


# --- refuses to clobber -------------------------------------------------------


def test_an_absent_collection_is_safe_to_write() -> None:
    assert fetch_corpus.clobber_refusal(SC, existing=None, resumable=False) is None


def test_an_existing_but_empty_collection_is_safe_to_write() -> None:
    existing = fetch_corpus.Existing(points=0, matching=False)
    assert fetch_corpus.clobber_refusal(SC, existing=existing, resumable=False) is None


def test_an_existing_populated_collection_is_refused() -> None:
    existing = fetch_corpus.Existing(points=371_247, matching=True)
    refusal = fetch_corpus.clobber_refusal(SC, existing=existing, resumable=False)
    assert refusal is not None
    assert "sc_judgments_v1" in refusal
    assert "371,247" in refusal


def test_the_refusal_says_what_to_do_about_it() -> None:
    existing = fetch_corpus.Existing(points=10, matching=True)
    refusal = fetch_corpus.clobber_refusal(SC, existing=existing, resumable=False)
    assert "DELETE" in refusal


def test_a_resumed_run_of_our_own_is_not_treated_as_a_clobber() -> None:
    existing = fetch_corpus.Existing(points=50_000, matching=True)
    assert fetch_corpus.clobber_refusal(SC, existing=existing, resumable=True) is None


def test_another_courts_points_do_not_block_fetching_a_different_court() -> None:
    # `hc_judgments_v1` is shared across courts (see openlaw.corpus.high_courts),
    # so Karnataka's 1.4M points must not block a later Delhi fetch.
    existing = fetch_corpus.Existing(points=1_467_133, matching=False)
    assert fetch_corpus.clobber_refusal(DELHI, existing=existing, resumable=False) is None


def test_the_same_courts_points_do_block_refetching_it() -> None:
    existing = fetch_corpus.Existing(points=1_053_138, matching=True)
    refusal = fetch_corpus.clobber_refusal(DELHI, existing=existing, resumable=False)
    assert refusal is not None
    assert "7_26" in refusal or "delhi" in refusal


def test_nothing_is_written_when_any_target_would_be_clobbered() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/collections/sc_judgments_v1":
            return httpx.Response(
                200, json={"result": {"points_count": 5, "config": {}}}
            )
        return httpx.Response(200, json={"result": {"points": [{"id": "x"}]}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="")
    with pytest.raises(SystemExit) as raised:
        fetch_corpus.refuse_to_clobber(client, QDRANT, (SC,), resumable=frozenset())

    assert "sc_judgments_v1" in str(raised.value)
    assert all(request.method in {"GET", "POST"} for request in requests)
    assert not any(request.method in {"PUT", "DELETE", "PATCH"} for request in requests)


def test_a_missing_collection_reads_as_absent_not_as_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": {"error": "Not found"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_corpus.inspect_collection(client, QDRANT, SC) is None


# --- creating the collection --------------------------------------------------


def test_the_created_collection_declares_the_idf_modifier() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"result": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_corpus.create_collection(client, QDRANT, "sc_judgments_v1")

    assert bodies[0]["sparse_vectors"][SPARSE_VECTOR_NAME] == {"modifier": "idf"}
    assert bodies[0]["vectors"]["dense"]["size"] == EMBEDDING_DIMENSIONS


def test_an_already_existing_collection_is_tolerated_on_create() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"status": {"error": "already exists"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_corpus.create_collection(client, QDRANT, "hc_judgments_v1")


# --- upserting shards ---------------------------------------------------------


def _synthetic_point(index: int) -> dict[str, Any]:
    chunk_id = f"chunk-{index}"
    return {
        "id": point_id(chunk_id),
        "vector": {
            "dense": [(index % 7) / 100.0] * EMBEDDING_DIMENSIONS,
            SPARSE_VECTOR_NAME: {"indices": [index, index + 1], "values": [1.0, 2.0]},
        },
        "payload": {
            "chunk_id": chunk_id,
            "text": f"provision {index}",
            "court_code": "7_26",
            "decision_date": "2019-04-11",
        },
    }


def _write_shard(path: Path, count: int) -> None:
    rows = [point_to_row(_synthetic_point(index)) for index in range(count)]
    pq.write_table(rows_to_table(rows), path, compression="zstd")


def test_a_shard_is_upserted_in_batches(tmp_path: Path) -> None:
    shard = tmp_path / "shard-00000.parquet"
    _write_shard(shard, 300)
    batches: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batches.append(len(json.loads(request.content)["points"]))
        return httpx.Response(200, json={"result": {"status": "completed"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    written = fetch_corpus.upsert_shard(
        client, QDRANT, "hc_judgments_v1", shard, batch_size=128, start_row=0
    )

    assert written == 300
    assert batches == [128, 128, 44]


def test_a_resumed_shard_skips_the_rows_already_upserted(tmp_path: Path) -> None:
    shard = tmp_path / "shard-00000.parquet"
    _write_shard(shard, 300)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        for point in json.loads(request.content)["points"]:
            seen.append(point["payload"]["chunk_id"])
        return httpx.Response(200, json={"result": {"status": "completed"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    written = fetch_corpus.upsert_shard(
        client, QDRANT, "hc_judgments_v1", shard, batch_size=128, start_row=250
    )

    assert written == 50
    assert seen[0] == "chunk-250"


def test_the_round_trip_restores_byte_identical_points(tmp_path: Path) -> None:
    """Export -> parquet -> fetch must hand Qdrant what Qdrant handed out.

    Dense vectors are float16 on the way through, so they are compared to a
    tolerance; the id, the sparse vector and the whole payload must match
    exactly, because those are what a citation resolves through.
    """
    originals = [_synthetic_point(index) for index in range(300)]
    shard = tmp_path / "shard-00000.parquet"
    pq.write_table(
        rows_to_table([point_to_row(point) for point in originals]),
        shard,
        compression="zstd",
    )
    upserted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upserted.extend(json.loads(request.content)["points"])
        return httpx.Response(200, json={"result": {"status": "completed"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_corpus.upsert_shard(
        client, QDRANT, "hc_judgments_v1", shard, batch_size=64, start_row=0
    )

    assert len(upserted) == len(originals)
    for original, restored in zip(originals, upserted, strict=True):
        assert restored["id"] == original["id"]
        assert restored["payload"] == original["payload"]
        assert restored["vector"][SPARSE_VECTOR_NAME] == original["vector"][SPARSE_VECTOR_NAME]
        assert restored["vector"]["dense"] == pytest.approx(
            original["vector"]["dense"], abs=1e-3
        )


# --- manifests and downloads --------------------------------------------------


def test_the_manifest_path_is_namespaced_by_target() -> None:
    assert fetch_corpus.repo_path(DELHI, "manifest.json") == (
        "hc_judgments_v1/delhi/manifest.json"
    )


def test_a_manifest_naming_a_different_collection_is_refused(tmp_path: Path) -> None:
    manifest = {"collection": "acts_india_v1", "shards": [], "points": 0}
    with pytest.raises(ValueError, match="hc_judgments_v1"):
        fetch_corpus.validate_manifest(DELHI, manifest)


def test_a_manifest_naming_a_different_court_is_refused(tmp_path: Path) -> None:
    manifest = {
        "collection": "hc_judgments_v1",
        "court_code": "29_3",
        "shards": [],
        "points": 0,
    }
    with pytest.raises(ValueError, match="7_26"):
        fetch_corpus.validate_manifest(DELHI, manifest)


def test_a_manifest_from_a_float32_export_is_refused_not_misread() -> None:
    manifest = {
        "collection": "sc_judgments_v1",
        "court_code": None,
        "dense_dtype": "float64",
        "shards": [],
        "points": 0,
    }
    with pytest.raises(ValueError, match="float"):
        fetch_corpus.validate_manifest(SC, manifest)


def test_a_wellformed_manifest_passes() -> None:
    manifest = {
        "collection": "sc_judgments_v1",
        "court_code": None,
        "dense_dtype": "float16",
        "embedding_model": "voyage-4-nano",
        "points": 2,
        "shards": [{"name": "shard-00000.parquet", "rows": 2, "bytes": 10}],
    }
    assert fetch_corpus.validate_manifest(SC, manifest) == manifest


# --- checkpoints --------------------------------------------------------------


def test_restore_state_round_trips(tmp_path: Path) -> None:
    state = fetch_corpus.RestoreState(shard_index=2, row_offset=128, points=300)
    fetch_corpus.save_state(SC, tmp_path, state, "v2026.08")
    assert fetch_corpus.load_state(SC, tmp_path) == state


def test_a_checkpoint_from_another_corpus_version_is_refused(tmp_path: Path) -> None:
    fetch_corpus.save_state(
        SC, tmp_path, fetch_corpus.RestoreState(1, 0, 10), "v2025.01"
    )
    with pytest.raises(SystemExit, match="v2025.01"):
        fetch_corpus.load_state(SC, tmp_path, expected_version="v2026.08")


def test_no_checkpoint_starts_from_the_first_shard(tmp_path: Path) -> None:
    assert fetch_corpus.load_state(SC, tmp_path) == fetch_corpus.RestoreState(0, 0, 0)


# --- working on a bare clone --------------------------------------------------


def test_settings_resolve_without_the_raw_dataset_paths(monkeypatch) -> None:
    """A downloader has no vaquill/KanoonGPT snapshots -- that must still work.

    Requiring them would fail a fresh clone's first command with a pydantic
    stack trace naming two paths the user is right not to have.
    """
    monkeypatch.delenv("OPENLAW_VAQUILL_ROOT", raising=False)
    monkeypatch.delenv("OPENLAW_KANOONGPT_ROOT", raising=False)
    monkeypatch.delenv("OPENLAW_QDRANT_URL", raising=False)
    from openlaw.config import Settings, get_settings

    get_settings.cache_clear()
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    try:
        settings = fetch_corpus.resolve_settings()
    finally:
        Settings.model_config["env_file"] = original
        get_settings.cache_clear()

    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.corpus_version == "v2026.08"


def test_a_configured_env_still_wins(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_QDRANT_URL", "http://qdrant.internal:6333")
    from openlaw.config import get_settings

    get_settings.cache_clear()
    try:
        assert fetch_corpus.resolve_settings().qdrant_url == "http://qdrant.internal:6333"
    finally:
        get_settings.cache_clear()


def test_the_refusal_prints_a_copy_pasteable_delete_command() -> None:
    existing = fetch_corpus.Existing(points=10, matching=True)
    refusal = fetch_corpus.clobber_refusal(
        SC, existing, False, "http://qdrant.internal:6333"
    )
    assert (
        "curl -X DELETE 'http://qdrant.internal:6333/collections/sc_judgments_v1'"
        in refusal
    )
