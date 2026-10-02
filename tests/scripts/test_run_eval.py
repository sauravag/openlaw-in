"""Unit tests for `scripts/run_eval.py`'s pure helpers.

`run_eval.py` talks to Qdrant, Neo4j, and the Anthropic API, none of which a
unit test may depend on -- so only its pure, DI-free logic is covered here:
`gate_verdict` (the honesty policy deciding PASS/FAIL/CANNOT BE CERTIFIED) and
the small file-reading helpers. The script lives outside the `openlaw`
package, so it is loaded directly from its file path, following the same
pattern as `tests/scripts/test_build_sc_index.py`.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from openlaw.evaluation.metrics import QuestionResult

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"


def _load_run_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_eval", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_eval = _load_run_eval()


# --- gate_verdict ---------------------------------------------------------


def test_gate_cannot_be_certified_below_the_minimum_question_count() -> None:
    verdict = run_eval.gate_verdict(1, 200, (QuestionResult(recall=1.0, unverifiable_citations=0),))
    assert verdict.startswith("CANNOT BE CERTIFIED")
    assert "1 of 200" in verdict


def test_gate_cannot_be_certified_when_generation_did_not_run() -> None:
    verdict = run_eval.gate_verdict(200, 200, None)
    assert verdict.startswith("CANNOT BE CERTIFIED")
    assert "did not run" in verdict


def test_gate_cannot_be_certified_reports_both_reasons_together() -> None:
    verdict = run_eval.gate_verdict(1, 200, None)
    assert "1 of 200" in verdict
    assert "did not run" in verdict


def test_gate_passes_when_the_minimum_is_met_and_generation_is_clean() -> None:
    results = tuple(QuestionResult(recall=0.9, unverifiable_citations=0) for _ in range(200))
    assert run_eval.gate_verdict(200, 200, results).startswith("PASS")


def test_gate_fails_when_recall_is_below_threshold_even_with_enough_questions() -> None:
    results = tuple(QuestionResult(recall=0.5, unverifiable_citations=0) for _ in range(200))
    verdict = run_eval.gate_verdict(200, 200, results)
    assert verdict.startswith("FAIL")


def test_gate_fails_on_a_single_unverifiable_citation_even_with_perfect_recall() -> None:
    results = (QuestionResult(recall=1.0, unverifiable_citations=1),) * 200
    verdict = run_eval.gate_verdict(200, 200, results)
    assert verdict.startswith("FAIL")


# --- file-reading helpers --------------------------------------------------


def test_load_verified_questions_returns_empty_tuple_for_a_missing_file(tmp_path: Path) -> None:
    assert run_eval._load_verified_questions(tmp_path / "absent.jsonl") == ()


def test_load_verified_questions_loads_a_real_file(tmp_path: Path) -> None:
    path = tmp_path / "verified.jsonl"
    path.write_text(
        '{"question": "q", "gold_chunk_ids": ["a"], "language": "en", "category": "tenancy"}\n',
        encoding="utf-8",
    )
    questions = run_eval._load_verified_questions(path)
    assert len(questions) == 1


def test_backlog_count_is_zero_for_a_missing_file(tmp_path: Path) -> None:
    assert run_eval._backlog_count(tmp_path / "absent.jsonl") == 0


def test_backlog_count_ignores_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "backlog.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n\n\n', encoding="utf-8")
    assert run_eval._backlog_count(path) == 2
