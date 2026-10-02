"""Unit tests for `scripts/build_eval_set.py`'s row-building and file-writing
helpers.

The script lives outside the `openlaw` package (a driver, not a library), so
it is loaded directly from its file path, following the same pattern as
`tests/scripts/test_build_sc_index.py`.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from openlaw.evaluation.dataset import EvalQuestion

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_eval_set.py"


def _load_build_eval_set() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_eval_set", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_eval_set = _load_build_eval_set()


def test_backlog_row_has_empty_gold_chunk_ids() -> None:
    row = build_eval_set._backlog_row("q", "en", "tenancy")
    assert row == {"question": "q", "gold_chunk_ids": [], "language": "en", "category": "tenancy"}


def test_backlog_row_cannot_become_an_eval_question() -> None:
    """The whole point: an unresearched row must fail `EvalQuestion`'s own
    validation, not merely look empty."""
    row = build_eval_set._backlog_row("q", "en", "tenancy")
    try:
        EvalQuestion.model_validate(row)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_verified_row_carries_its_gold_chunk_ids() -> None:
    row = build_eval_set._verified_row(("q", "en", "criminal", ("IND_state_4219_s379",)))
    assert row["gold_chunk_ids"] == ["IND_state_4219_s379"]
    EvalQuestion.model_validate(row)  # must not raise


def test_every_seed_question_is_non_empty_and_categorised() -> None:
    for question, language, category in build_eval_set.SEED_QUESTIONS:
        assert question.strip()
        assert language in {"en", "hi"}
        assert category


def test_every_verified_question_is_a_real_eval_question() -> None:
    for entry in build_eval_set.VERIFIED_QUESTIONS:
        row = build_eval_set._verified_row(entry)
        EvalQuestion.model_validate(row)  # must not raise


def test_write_jsonl_round_trips_through_load_eval_set(tmp_path: Path) -> None:
    from openlaw.evaluation.dataset import load_eval_set

    path = tmp_path / "verified.jsonl"
    rows = [build_eval_set._verified_row(entry) for entry in build_eval_set.VERIFIED_QUESTIONS]

    build_eval_set._write_jsonl(path, rows, "verified questions")

    loaded = load_eval_set(path)
    assert len(loaded) == len(rows)
    assert loaded[0].gold_chunk_ids == build_eval_set.VERIFIED_QUESTIONS[0][3]


def test_write_jsonl_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "eval.jsonl"

    build_eval_set._write_jsonl(path, [{"a": 1}], "rows")

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8").strip()) == {"a": 1}


def test_write_jsonl_preserves_non_ascii_text(tmp_path: Path) -> None:
    """Hindi seed questions must not be escaped into `\\uXXXX` sequences."""
    path = tmp_path / "eval.jsonl"

    build_eval_set._write_jsonl(path, [{"question": "मेरा"}], "rows")

    assert "मेरा" in path.read_text(encoding="utf-8")


def test_main_writes_both_output_files(tmp_path: Path, monkeypatch) -> None:
    backlog_path = tmp_path / "citizen_questions.jsonl"
    verified_path = tmp_path / "citizen_questions_verified.jsonl"
    monkeypatch.setattr(build_eval_set, "OUTPUT", backlog_path)
    monkeypatch.setattr(build_eval_set, "VERIFIED_OUTPUT", verified_path)

    build_eval_set.main()

    assert backlog_path.exists()
    assert verified_path.exists()
    backlog_lines = backlog_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(backlog_lines) == len(build_eval_set.SEED_QUESTIONS)
