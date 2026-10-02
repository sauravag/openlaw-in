"""The citizen evaluation set.

**What exists today:** one question with a hand-verified `gold_chunk_ids`
(`data/eval/citizen_questions_verified.jsonl`), plus 18 real citizen
questions with no gold answer researched yet, kept as a backlog in a
separate file (`data/eval/citizen_questions.jsonl`). That is 1 of the 200
the release gate requires, and the gate says so: `scripts/run_eval.py`
reports CANNOT BE CERTIFIED, never PASS, until `MINIMUM_QUESTIONS` verified
questions exist.

**The target:** ~200 real citizen legal problems with known-correct sources,
sourced ideally with legal-aid clinics. This is the only way to know whether
retrieval works, it is on the critical path, and nothing else in the project
substitutes for it.

The backlog lives in its own file precisely so it cannot silently load here
and inflate an eval run with a fabricated-looking pass, and `load_eval_set`
raises on any row missing a gold chunk (via `EvalQuestion`'s own validation)
rather than skipping it, so a still-empty row fails loudly instead of
quietly vanishing.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

MINIMUM_QUESTIONS = 200


class EvalQuestion(BaseModel):
    """One evaluation question with its known-correct sources."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    gold_chunk_ids: tuple[str, ...] = Field(min_length=1)
    language: str = "en"
    category: str = "general"


def load_eval_set(path: Path) -> tuple[EvalQuestion, ...]:
    """Load evaluation questions from the JSONL file at `path`."""
    if not path.exists():
        raise FileNotFoundError(f"evaluation set not found at {path}")

    questions: list[EvalQuestion] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            questions.append(EvalQuestion.model_validate(json.loads(stripped)))
    return tuple(questions)
