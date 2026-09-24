"""Unit tests for the checkpoint/resume logic in scripts/build_graph.py.

Resumability is the property Task 5 is judged on, and most of it is pure file
and dict logic — no Neo4j needed. Every `load_nodes`/`load_edges` call here is
faked, so these tests run against the real fixture corpus (small, fast, no
network) and assert only on checkpoint state, not on graph content — the
loader itself is already covered end to end against live Neo4j in
tests/graph/test_loader.py.

The script lives outside the `openlaw` package (it is a driver, not a
library), so it is loaded directly from its file path, matching the existing
pattern in tests/scripts/test_build_sc_index.py.
"""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from openlaw.config import Settings

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_graph.py"
_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "vaquill"
CORPUS_VERSION = "v2026.08"


def _load_build_graph() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_graph", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_graph = _load_build_graph()


def _settings(vaquill_root: Path) -> Settings:
    return Settings(
        vaquill_root=vaquill_root, kanoongpt_root=vaquill_root, corpus_version=CORPUS_VERSION
    )


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Replace the Neo4j-touching functions with fakes that record calls."""
    calls: list[tuple] = []

    def fake_load_nodes(driver, label, rows, batch_size=10_000):  # noqa: ANN001
        calls.append(("node", label, len(rows)))
        return len(rows)

    def fake_load_edges(driver, kind, edges, batch_size=10_000):  # noqa: ANN001
        calls.append(("edge", kind, len(edges)))
        return len(edges)

    monkeypatch.setattr(build_graph, "load_nodes", fake_load_nodes)
    monkeypatch.setattr(build_graph, "load_edges", fake_load_edges)
    return calls


# --- checkpoint round-trip ----------------------------------------------------


def test_read_checkpoint_returns_none_when_missing(tmp_path: Path) -> None:
    assert build_graph.read_checkpoint(tmp_path / "checkpoint.json") is None


def test_write_then_read_checkpoint_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    state = build_graph.default_state(CORPUS_VERSION)
    state["provision_files_done"].append("in_central_legislation.parquet")

    build_graph.write_checkpoint(path, state)

    assert build_graph.read_checkpoint(path) == state


def test_write_checkpoint_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "checkpoint.json"

    build_graph.write_checkpoint(path, build_graph.default_state(CORPUS_VERSION))

    assert path.exists()


def test_write_checkpoint_overwrites_a_previous_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_graph.write_checkpoint(path, build_graph.default_state(CORPUS_VERSION))

    second = build_graph.default_state(CORPUS_VERSION)
    second["judgments_loaded"] = True
    build_graph.write_checkpoint(path, second)

    assert build_graph.read_checkpoint(path)["judgments_loaded"] is True


def test_write_checkpoint_leaves_no_stray_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    build_graph.write_checkpoint(path, build_graph.default_state(CORPUS_VERSION))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkpoint.json"]


def test_clear_checkpoint_removes_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    build_graph.write_checkpoint(path, build_graph.default_state(CORPUS_VERSION))

    build_graph.clear_checkpoint(path)

    assert not path.exists()


def test_clear_checkpoint_is_a_no_op_when_nothing_exists(tmp_path: Path) -> None:
    build_graph.clear_checkpoint(tmp_path / "checkpoint.json")  # must not raise


def test_checkpoint_default_path_is_under_the_gitignored_data_directory() -> None:
    assert build_graph.DEFAULT_CHECKPOINT_PATH == Path("data/graph_checkpoint.json")


# --- default_state and load_state ---------------------------------------------


def test_default_state_starts_with_nothing_loaded() -> None:
    state = build_graph.default_state(CORPUS_VERSION)
    assert state == {
        "corpus_version": CORPUS_VERSION,
        "judgments_loaded": False,
        "provision_files_done": [],
    }


def test_load_state_returns_a_fresh_default_when_no_checkpoint_exists(tmp_path: Path) -> None:
    state = build_graph.load_state(tmp_path / "checkpoint.json", CORPUS_VERSION)
    assert state == build_graph.default_state(CORPUS_VERSION)


def test_load_state_returns_the_checkpoint_when_versions_match(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    saved = build_graph.default_state(CORPUS_VERSION)
    saved["judgments_loaded"] = True
    build_graph.write_checkpoint(path, saved)

    assert build_graph.load_state(path, CORPUS_VERSION) == saved


def test_load_state_refuses_to_resume_across_a_different_corpus_version(tmp_path: Path) -> None:
    """A different snapshot means different rows entirely — resuming from a
    stale checkpoint would silently skip or misalign work."""
    path = tmp_path / "checkpoint.json"
    build_graph.write_checkpoint(path, build_graph.default_state("v2025.01"))

    with pytest.raises(ValueError, match="corpus_version"):
        build_graph.load_state(path, CORPUS_VERSION)


# --- --restart / --limit flags -------------------------------------------------


def test_restart_flag_defaults_to_false() -> None:
    assert build_graph.parse_args([]).restart is False


def test_restart_flag_can_be_set() -> None:
    assert build_graph.parse_args(["--restart"]).restart is True


def test_limit_flag_defaults_to_none() -> None:
    assert build_graph.parse_args([]).limit is None


def test_limit_flag_parses_to_an_int() -> None:
    assert build_graph.parse_args(["--limit", "3000"]).limit == 3000


def test_restart_clears_an_existing_checkpoint_end_to_end(tmp_path: Path) -> None:
    """Exercises the same clear-then-load sequence `main()` performs on
    --restart, without needing a live Neo4j."""
    path = tmp_path / "checkpoint.json"
    saved = build_graph.default_state(CORPUS_VERSION)
    saved["judgments_loaded"] = True
    build_graph.write_checkpoint(path, saved)

    build_graph.clear_checkpoint(path)

    assert build_graph.load_state(path, CORPUS_VERSION) == build_graph.default_state(
        CORPUS_VERSION
    )


# --- judgments phase state transitions -----------------------------------------


def test_judgments_phase_skips_entirely_when_already_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checkpoint short-circuit must never touch Neo4j once done."""
    calls = _record_calls(monkeypatch)
    state = build_graph.default_state(CORPUS_VERSION)
    state["judgments_loaded"] = True
    checkpoint_path = tmp_path / "checkpoint.json"

    build_graph.run_judgments_phase(
        driver=None,
        state=state,
        checkpoint_path=checkpoint_path,
        settings=_settings(_FIXTURE_DIR),
        limit=None,
    )

    assert calls == []
    assert not checkpoint_path.exists()  # nothing changed, nothing to persist


def test_judgments_phase_loads_nodes_and_edges_then_marks_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    state = build_graph.default_state(CORPUS_VERSION)
    checkpoint_path = tmp_path / "checkpoint.json"

    build_graph.run_judgments_phase(
        driver=None,
        state=state,
        checkpoint_path=checkpoint_path,
        settings=_settings(_FIXTURE_DIR),
        limit=None,
    )

    assert state["judgments_loaded"] is True
    assert build_graph.read_checkpoint(checkpoint_path) == state
    node_labels = {label for kind, label, _ in calls if kind == "node"}
    assert node_labels == {"Court", "Judge", "Judgment"}
    assert any(kind == "edge" for kind, _, _ in calls)


def test_judgments_phase_respects_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    state = build_graph.default_state(CORPUS_VERSION)

    build_graph.run_judgments_phase(
        driver=None,
        state=state,
        checkpoint_path=tmp_path / "checkpoint.json",
        settings=_settings(_FIXTURE_DIR),
        limit=1,
    )

    judgment_rows = next(n for kind, label, n in calls if label == "Judgment")
    assert judgment_rows <= 1


# --- provisions phase state transitions ----------------------------------------


def test_provisions_phase_skips_files_already_recorded_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    state = build_graph.default_state(CORPUS_VERSION)
    state["provision_files_done"] = ["in_central_legislation.parquet"]
    checkpoint_path = tmp_path / "checkpoint.json"

    build_graph.run_provisions_phase(
        driver=None,
        state=state,
        checkpoint_path=checkpoint_path,
        settings=_settings(_FIXTURE_DIR),
        limit=None,
    )

    assert calls == []
    assert state["provision_files_done"] == ["in_central_legislation.parquet"]


def test_provisions_phase_loads_an_unfinished_file_then_records_it_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    state = build_graph.default_state(CORPUS_VERSION)
    checkpoint_path = tmp_path / "checkpoint.json"

    build_graph.run_provisions_phase(
        driver=None,
        state=state,
        checkpoint_path=checkpoint_path,
        settings=_settings(_FIXTURE_DIR),
        limit=None,
    )

    assert state["provision_files_done"] == ["in_central_legislation.parquet"]
    assert build_graph.read_checkpoint(checkpoint_path) == state
    node_labels = {label for kind, label, _ in calls if kind == "node"}
    assert node_labels == {"Act", "Section", "DefinedTerm", "LegalSubject"}


def test_provisions_phase_stops_once_the_limit_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With two files and a limit smaller than the first file's row count, the
    second file must never be touched — the loop must stop, not just cap rows."""
    calls = _record_calls(monkeypatch)
    fixtures = tmp_path / "vaquill"
    fixtures.mkdir()
    shutil.copy(
        _FIXTURE_DIR / "in_central_legislation.parquet",
        fixtures / "in_aaa_legislation.parquet",
    )
    shutil.copy(
        _FIXTURE_DIR / "in_central_legislation.parquet",
        fixtures / "in_bbb_legislation.parquet",
    )
    state = build_graph.default_state(CORPUS_VERSION)

    build_graph.run_provisions_phase(
        driver=None,
        state=state,
        checkpoint_path=tmp_path / "checkpoint.json",
        settings=_settings(fixtures),
        limit=100,
    )

    assert state["provision_files_done"] == ["in_aaa_legislation.parquet"]
    assert all(name != "in_bbb_legislation.parquet" for name in state["provision_files_done"])


def test_provisions_phase_resumes_at_the_next_unfinished_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    fixtures = tmp_path / "vaquill"
    fixtures.mkdir()
    shutil.copy(
        _FIXTURE_DIR / "in_central_legislation.parquet",
        fixtures / "in_aaa_legislation.parquet",
    )
    shutil.copy(
        _FIXTURE_DIR / "in_central_legislation.parquet",
        fixtures / "in_bbb_legislation.parquet",
    )
    state = build_graph.default_state(CORPUS_VERSION)
    state["provision_files_done"] = ["in_aaa_legislation.parquet"]

    build_graph.run_provisions_phase(
        driver=None,
        state=state,
        checkpoint_path=tmp_path / "checkpoint.json",
        settings=_settings(fixtures),
        limit=None,
    )

    assert state["provision_files_done"] == [
        "in_aaa_legislation.parquet",
        "in_bbb_legislation.parquet",
    ]
    assert len(calls) > 0
