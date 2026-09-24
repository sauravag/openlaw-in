"""The citizen evaluation set.

~200 real citizen legal problems with known-correct sources. This is the only way
to know whether retrieval works, and sourcing it — ideally with legal-aid clinics —
is on the critical path. Nothing else in the project substitutes for it.

As of Task 9 (see task-9-report.md), only a handful of questions carry a
hand-verified `gold_chunk_ids` -- everything else is an unresearched backlog
kept in a separate file precisely so it cannot silently load here and inflate
an eval run with a fabricated-looking pass. `load_eval_set` raises on any row
missing a gold chunk (via `EvalQuestion`'s own validation) rather than
skipping it, so a still-empty row fails loudly instead of quietly vanishing.
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
