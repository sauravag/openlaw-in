"""Tests for the citizen evaluation set loader.

See `openlaw.evaluation.dataset`'s module docstring for why this set matters:
it is the only way to know whether retrieval works, rather than resting
decisions on a handful of anecdotal queries.
"""

import json

import pytest

from openlaw.evaluation.dataset import MINIMUM_QUESTIONS, EvalQuestion, load_eval_set


def test_minimum_eval_set_size_is_two_hundred() -> None:
    assert MINIMUM_QUESTIONS == 200


def test_question_requires_at_least_one_gold_chunk() -> None:
    """A question with no known-correct source cannot measure recall."""
    with pytest.raises(ValueError):
        EvalQuestion(question="q", gold_chunk_ids=(), language="en", category="tenancy")


def test_question_requires_non_empty_text() -> None:
    with pytest.raises(ValueError):
        EvalQuestion(
            question="", gold_chunk_ids=("a",), language="en", category="tenancy"
        )


def test_loads_questions_from_jsonl(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps(
            {
                "question": "my landlord kept my deposit",
                "gold_chunk_ids": ["IND_central_1_12"],
                "language": "en",
                "category": "tenancy",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    questions = load_eval_set(path)

    assert len(questions) == 1
    assert questions[0].category == "tenancy"


def test_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps(
            {
                "question": "q", "gold_chunk_ids": ["a"],
                "language": "en", "category": "tenancy",
            }
        )
        + "\n\n\n",
        encoding="utf-8",
    )
    assert len(load_eval_set(path)) == 1


def test_raises_on_a_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_eval_set(tmp_path / "absent.jsonl")
