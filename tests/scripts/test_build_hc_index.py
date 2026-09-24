"""Unit tests for the pure checkpoint/argument helpers in scripts/build_hc_index.py.

Mirrors tests/scripts/test_build_sc_index.py -- this script has never been run
against the real Voyage API within the test suite (that needs a live key and
Qdrant), so its checkpoint/resume logic can only be covered here, with stubs
and `tmp_path`, never end to end.

The script lives outside the `openlaw` package (it is a driver, not a
library), so it is loaded directly from its file path rather than imported by
name -- same approach as test_build_sc_index.py.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_hc_index.py"


def _load_build_hc_index() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_hc_index", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_hc_index = _load_build_hc_index()

CORPUS_VERSION = "v2026.08"
COURT_SLUG = "punjab-and-haryana"


# --- Model choice is a named constant, not a silent substitution ------------


def test_embedding_model_is_the_local_nano_model_id() -> None:
    """Not voyage-4-lite -- the default query embedder is the LOCAL
    voyage-4-nano, and nano-query-against-lite-document retrieval measured
    only 15.0% top-1 / 31.7% top-10 (median rank 61 of 300). Documents must
    be embedded with the SAME model that embeds queries."""
    assert build_hc_index.EMBEDDING_MODEL == "voyageai/voyage-4-nano"


def test_default_court_is_punjab_and_haryana() -> None:
    assert build_hc_index.DEFAULT_COURT_SLUG == "punjab-and-haryana"


# --- Per-court checkpoint path ------------------------------------------------


def test_checkpoint_path_is_scoped_per_court() -> None:
    assert build_hc_index.checkpoint_path_for("punjab-and-haryana") == Path(
        "data/hc_index_checkpoint_punjab-and-haryana.json"
    )
    assert build_hc_index.checkpoint_path_for("bombay") != build_hc_index.checkpoint_path_for(
        "punjab-and-haryana"
    )


# --- Checkpoint read/write ----------------------------------------------------


def test_read_checkpoint_returns_none_when_missing(tmp_path: Path) -> None:
    assert build_hc_index.read_checkpoint(tmp_path / "checkpoint.json") is None


def test_write_then_read_checkpoint_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    build_hc_index.write_checkpoint(
        path, processed=1_234, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )
    checkpoint = build_hc_index.read_checkpoint(path)

    assert checkpoint == {
        "processed": 1_234,
        "corpus_version": CORPUS_VERSION,
        "court_slug": COURT_SLUG,
    }


def test_write_checkpoint_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "checkpoint.json"

    build_hc_index.write_checkpoint(
        path, processed=1, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    assert path.exists()


def test_write_checkpoint_overwrites_a_previous_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_hc_index.write_checkpoint(
        path, processed=100, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    build_hc_index.write_checkpoint(
        path, processed=200, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    assert build_hc_index.read_checkpoint(path)["processed"] == 200


def test_write_checkpoint_leaves_no_stray_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    build_hc_index.write_checkpoint(
        path, processed=1, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkpoint.json"]


def test_clear_checkpoint_removes_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_hc_index.write_checkpoint(
        path, processed=1, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    build_hc_index.clear_checkpoint(path)

    assert not path.exists()


def test_clear_checkpoint_is_a_no_op_when_nothing_exists(tmp_path: Path) -> None:
    build_hc_index.clear_checkpoint(tmp_path / "checkpoint.json")  # must not raise


# --- Resume offset: corpus_version AND court_slug must both match -----------


def test_resume_offset_is_zero_with_no_checkpoint() -> None:
    assert build_hc_index.resume_offset(None, CORPUS_VERSION, COURT_SLUG) == 0


def test_resume_offset_returns_the_processed_count_when_everything_matches() -> None:
    checkpoint = {
        "processed": 42_000,
        "corpus_version": CORPUS_VERSION,
        "court_slug": COURT_SLUG,
    }
    assert build_hc_index.resume_offset(checkpoint, CORPUS_VERSION, COURT_SLUG) == 42_000


def test_resume_offset_defaults_to_zero_when_processed_is_absent() -> None:
    checkpoint = {"corpus_version": CORPUS_VERSION, "court_slug": COURT_SLUG}
    assert build_hc_index.resume_offset(checkpoint, CORPUS_VERSION, COURT_SLUG) == 0


def test_resume_offset_refuses_to_resume_across_a_different_corpus_version() -> None:
    checkpoint = {"processed": 42_000, "corpus_version": "v2025.01", "court_slug": COURT_SLUG}

    with pytest.raises(ValueError, match="corpus_version"):
        build_hc_index.resume_offset(checkpoint, CORPUS_VERSION, COURT_SLUG)


def test_resume_offset_refuses_to_resume_across_a_different_court() -> None:
    """A checkpoint from one High Court must never be applied to another --
    their input streams are entirely different files."""
    checkpoint = {"processed": 5_000, "corpus_version": CORPUS_VERSION, "court_slug": "bombay"}

    with pytest.raises(ValueError, match="court_slug"):
        build_hc_index.resume_offset(checkpoint, CORPUS_VERSION, COURT_SLUG)


# --- CLI arguments -------------------------------------------------------------


def test_court_flag_defaults_to_punjab_and_haryana() -> None:
    assert build_hc_index.parse_args([]).court == "punjab-and-haryana"


def test_court_flag_can_be_overridden() -> None:
    assert build_hc_index.parse_args(["--court", "bombay"]).court == "bombay"


def test_restart_flag_defaults_to_false() -> None:
    assert build_hc_index.parse_args([]).restart is False


def test_restart_flag_can_be_set() -> None:
    assert build_hc_index.parse_args(["--restart"]).restart is True


def test_limit_flag_defaults_to_none() -> None:
    assert build_hc_index.parse_args([]).limit is None


def test_limit_flag_parses_as_an_integer() -> None:
    assert build_hc_index.parse_args(["--limit", "3000"]).limit == 3000


def test_restart_clears_an_existing_checkpoint_end_to_end(tmp_path: Path) -> None:
    """Exercises the same clear-then-read sequence `main()` performs on
    --restart, without needing a Voyage key or a running Qdrant."""
    path = tmp_path / "checkpoint.json"
    build_hc_index.write_checkpoint(
        path, processed=999, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )

    build_hc_index.clear_checkpoint(path)

    assert build_hc_index.read_checkpoint(path) is None
    assert (
        build_hc_index.resume_offset(
            build_hc_index.read_checkpoint(path), CORPUS_VERSION, COURT_SLUG
        )
        == 0
    )


# --- _index_batch: pure resolve/embed/upsert orchestration -------------------


class _StubEmbedder:
    """A stub `VoyageEmbedder` -- returns one fixed-size vector per text."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1] * 1024 for _ in texts]


class _RecordingClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.put_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def put(self, url: str, **kwargs: object) -> "_RecordingClient":
        self.put_calls.append({"url": url, **kwargs})
        return self

    def delete(self, url: str, **kwargs: object) -> "_RecordingClient":
        self.delete_calls.append({"url": url, **kwargs})
        return self

    def raise_for_status(self) -> None:
        return None


# --- _restart: --restart must clear the checkpoint AND the HC collection ----


def test_restart_clears_the_checkpoint_and_deletes_the_hc_collection(tmp_path: Path) -> None:
    """A --restart that only cleared the checkpoint would leave points
    embedded by a stale model (voyage-4-lite, on the first run of this
    script) mixed in with points from the new model -- silently unfindable
    at query time. Both must be discarded together."""
    path = tmp_path / "checkpoint.json"
    build_hc_index.write_checkpoint(
        path, processed=999, corpus_version=CORPUS_VERSION, court_slug=COURT_SLUG
    )
    client = _RecordingClient()

    build_hc_index._restart(path, "http://qdrant.test", client)

    assert build_hc_index.read_checkpoint(path) is None
    assert client.delete_calls[0]["url"] == "http://qdrant.test/collections/hc_judgments_v1"


def test_restart_tolerates_no_checkpoint_and_no_existing_collection(tmp_path: Path) -> None:
    """A --restart before anything has ever been indexed (no checkpoint, no
    collection) must not fail."""
    path = tmp_path / "checkpoint.json"
    client = _RecordingClient(status_code=404)

    build_hc_index._restart(path, "http://qdrant.test", client)  # must not raise

    assert build_hc_index.read_checkpoint(path) is None


def _make_chunk(chunk_id: str, join_key: str):
    from openlaw.corpus.schema import JudgmentChunk

    return JudgmentChunk.from_row(
        {
            "chunk_id": chunk_id, "case_id": join_key, "chunk_index": 0,
            "text": "the writ petition is allowed", "text_original": "text",
            "court_code": "3_22", "judges": [], "decision_date": "2019-05-10",
            "language_code": "en",
        },
        corpus_version=CORPUS_VERSION,
    )


def test_index_batch_skips_unresolvable_chunks_and_counts_them() -> None:
    resolvable = _make_chunk("c1", "PHHC1")
    unresolvable = _make_chunk("c2", "PHHC2")

    class _Index:
        def lookup(self, join_key: str):
            if join_key == "PHHC1":
                return type(
                    "Found", (), {"source_pdf_url": "https://x.gov.in/a.pdf", "headnote": None}
                )()
            return None

    embedder = _StubEmbedder()
    client = _RecordingClient()

    indexed, unresolved = build_hc_index._index_batch(
        [resolvable, unresolvable], _Index(), embedder, avg_len=6.0,
        qdrant_url="http://qdrant.test", client=client,
    )

    assert indexed == 1
    assert unresolved == 1
    assert len(embedder.calls) == 1
    assert embedder.calls[0] == [resolvable.text]


def test_index_batch_upserts_nothing_when_every_chunk_is_unresolvable() -> None:
    unresolvable = _make_chunk("c1", "PHHC1")

    class _EmptyIndex:
        def lookup(self, join_key: str):
            return None

    embedder = _StubEmbedder()
    client = _RecordingClient()

    indexed, unresolved = build_hc_index._index_batch(
        [unresolvable], _EmptyIndex(), embedder, avg_len=6.0,
        qdrant_url="http://qdrant.test", client=client,
    )

    assert indexed == 0
    assert unresolved == 1
    assert embedder.calls == []
    assert client.put_calls == []
