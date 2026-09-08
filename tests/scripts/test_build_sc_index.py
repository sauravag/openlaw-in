"""Unit tests for the pure checkpoint helpers in scripts/build_sc_index.py.

`build_sc_index.py` has never been run against the real API — it needs a Voyage
key we do not have — so its checkpoint/resume logic (Critical 1b) can only be
covered here, with stubs and `tmp_path`, never end to end.

The script lives outside the `openlaw` package (it is a driver, not a library),
so it is loaded directly from its file path rather than imported by name.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_sc_index.py"


def _load_build_sc_index() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_sc_index", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_sc_index = _load_build_sc_index()

CORPUS_VERSION = "v2026.08"


# --- Critical 3: Supreme Court filtering uses a named constant ---------------


def test_supreme_court_code_constant() -> None:
    assert build_sc_index.SUPREME_COURT_CODE == "SCI"


# --- Critical 1b: checkpoint read/write --------------------------------------


def test_read_checkpoint_returns_none_when_missing(tmp_path: Path) -> None:
    assert build_sc_index.read_checkpoint(tmp_path / "checkpoint.json") is None


def test_write_then_read_checkpoint_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    build_sc_index.write_checkpoint(path, processed=1_234, corpus_version=CORPUS_VERSION)
    checkpoint = build_sc_index.read_checkpoint(path)

    assert checkpoint == {"processed": 1_234, "corpus_version": CORPUS_VERSION}


def test_write_checkpoint_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "checkpoint.json"

    build_sc_index.write_checkpoint(path, processed=1, corpus_version=CORPUS_VERSION)

    assert path.exists()


def test_write_checkpoint_overwrites_a_previous_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_sc_index.write_checkpoint(path, processed=100, corpus_version=CORPUS_VERSION)

    build_sc_index.write_checkpoint(path, processed=200, corpus_version=CORPUS_VERSION)

    assert build_sc_index.read_checkpoint(path)["processed"] == 200


def test_write_checkpoint_leaves_no_stray_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    build_sc_index.write_checkpoint(path, processed=1, corpus_version=CORPUS_VERSION)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkpoint.json"]


def test_clear_checkpoint_removes_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_sc_index.write_checkpoint(path, processed=1, corpus_version=CORPUS_VERSION)

    build_sc_index.clear_checkpoint(path)

    assert not path.exists()


def test_clear_checkpoint_is_a_no_op_when_nothing_exists(tmp_path: Path) -> None:
    build_sc_index.clear_checkpoint(tmp_path / "checkpoint.json")  # must not raise


# --- Critical 1b: resume offset and the corpus-version guard ------------------


def test_resume_offset_is_zero_with_no_checkpoint() -> None:
    assert build_sc_index.resume_offset(None, CORPUS_VERSION) == 0


def test_resume_offset_returns_the_processed_count_when_versions_match() -> None:
    checkpoint = {"processed": 42_000, "corpus_version": CORPUS_VERSION}
    assert build_sc_index.resume_offset(checkpoint, CORPUS_VERSION) == 42_000


def test_resume_offset_defaults_to_zero_when_processed_is_absent() -> None:
    checkpoint = {"corpus_version": CORPUS_VERSION}
    assert build_sc_index.resume_offset(checkpoint, CORPUS_VERSION) == 0


def test_resume_offset_refuses_to_resume_across_a_different_corpus_version() -> None:
    """A different snapshot means a different stream order — resuming from a
    stale checkpoint's count would silently skip or re-embed the wrong chunks."""
    checkpoint = {"processed": 42_000, "corpus_version": "v2025.01"}

    with pytest.raises(ValueError, match="corpus_version"):
        build_sc_index.resume_offset(checkpoint, CORPUS_VERSION)


# --- --restart flag -----------------------------------------------------------


def test_restart_flag_defaults_to_false() -> None:
    assert build_sc_index.parse_args([]).restart is False


def test_restart_flag_can_be_set() -> None:
    assert build_sc_index.parse_args(["--restart"]).restart is True


def test_restart_clears_an_existing_checkpoint_end_to_end(tmp_path: Path) -> None:
    """Exercises the same clear-then-read sequence `main()` performs on --restart,
    without needing a Voyage key or a running Qdrant."""
    path = tmp_path / "checkpoint.json"
    build_sc_index.write_checkpoint(path, processed=999, corpus_version=CORPUS_VERSION)

    build_sc_index.clear_checkpoint(path)

    assert build_sc_index.read_checkpoint(path) is None
    assert build_sc_index.resume_offset(build_sc_index.read_checkpoint(path), CORPUS_VERSION) == 0


def test_checkpoint_default_path_is_under_the_gitignored_data_directory() -> None:
    assert build_sc_index.DEFAULT_CHECKPOINT_PATH == Path("data/sc_index_checkpoint.json")
