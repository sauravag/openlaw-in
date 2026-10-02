"""Seed the citizen evaluation set.

Writes TWO files, deliberately kept separate:

- `data/eval/citizen_questions.jsonl` -- the research backlog. Real citizen
  legal problems with `gold_chunk_ids` left EMPTY, because the correct source
  for each one is genuine human research (ideally with legal-aid clinics),
  not something a script can fabricate. `openlaw.evaluation.dataset.
  EvalQuestion` refuses an empty `gold_chunk_ids`, so this file cannot be
  loaded by `load_eval_set` until someone fills each row in -- an unfinished
  row fails loudly instead of silently inflating a release-gate run.
- `data/eval/citizen_questions_verified.jsonl` -- the small subset that
  currently has a hand-verified `gold_chunk_ids`, confirmed against the live
  `acts_india_v1` collection during Task 9 (see task-9-report.md for exactly
  how each one was checked). This is the only file `scripts/run_eval.py`
  can actually run recall/precision numbers against today.

As of Task 9: 18 backlog questions, 1 verified. That is nowhere near the
200-question minimum (`MINIMUM_QUESTIONS`) the release gate needs -- see the
report for why fabricating the other 199 would be worse than admitting they
do not exist yet.

Run: python scripts/build_eval_set.py
"""

import json
from pathlib import Path

OUTPUT = Path("data/eval/citizen_questions.jsonl")
VERIFIED_OUTPUT = Path("data/eval/citizen_questions_verified.jsonl")

# Real citizen legal problems, `gold_chunk_ids` intentionally empty -- see the
# module docstring. Extend this list (and, once researched, promote entries
# into VERIFIED_QUESTIONS below) toward the 200-question minimum.
SEED_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("My landlord will not return my security deposit. What can I do?", "en", "tenancy"),
    ("I was fired without notice after four years. What am I owed?", "en", "labour"),
    ("My employer has not paid my salary for three months.", "en", "labour"),
    ("My husband's family is demanding dowry. What protection do I have?", "en", "family"),
    ("How do I apply for maintenance after separation?", "en", "family"),
    ("The builder has delayed possession of my flat by two years.", "en", "property"),
    ("A shop refused to refund a defective phone.", "en", "consumer"),
    ("I was arrested without being told why.", "en", "criminal"),
    ("How do I get anticipatory bail?", "en", "criminal"),
    ("My father died without a will. How is the property divided?", "en", "succession"),
    ("My neighbour has encroached on my land.", "en", "property"),
    ("The bank is threatening to seize my house over a loan default.", "en", "banking"),
    ("I want a divorce by mutual consent. What is the process?", "en", "family"),
    ("My child's school is refusing admission under the RTE quota.", "en", "education"),
    ("My employer is not depositing my provident fund.", "en", "labour"),
    ("मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।", "hi", "tenancy"),
    ("मुझे बिना नोटिस के नौकरी से निकाल दिया गया।", "hi", "labour"),
    ("दहेज के लिए मुझे परेशान किया जा रहा है।", "hi", "family"),
)

# Human-verified gold chunks, checked against the live `acts_india_v1`
# collection during Task 9 (both dense-only and hybrid search independently
# returned this exact chunk as the top hit, at the same score task-1-report.md
# measured: 0.689). This is the ONLY question in the whole set with a
# confirmed-correct source as of Task 9 -- see task-9-report.md.
VerifiedQuestion = tuple[str, str, str, tuple[str, ...]]
VERIFIED_QUESTIONS: tuple[VerifiedQuestion, ...] = (
    (
        "what is the punishment for theft",
        "en",
        "criminal",
        ("IND_state_4219_s379",),  # IPC 1860 s.379 "Punishment for theft"
    ),
)


def _write_jsonl(path: Path, rows: list[dict[str, object]], label: str) -> None:
    """Write `rows` as JSONL to `path`, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[Eval] wrote {len(rows)} {label} to {path}")


def _backlog_row(question: str, language: str, category: str) -> dict[str, object]:
    return {
        "question": question,
        "gold_chunk_ids": [],
        "language": language,
        "category": category,
    }


def _verified_row(entry: VerifiedQuestion) -> dict[str, object]:
    question, language, category, gold_chunk_ids = entry
    return {
        "question": question,
        "gold_chunk_ids": list(gold_chunk_ids),
        "language": language,
        "category": category,
    }


def main() -> None:
    backlog = [
        _backlog_row(question, language, category)
        for question, language, category in SEED_QUESTIONS
    ]
    verified = [_verified_row(entry) for entry in VERIFIED_QUESTIONS]

    _write_jsonl(OUTPUT, backlog, "backlog questions (gold_chunk_ids EMPTY)")
    _write_jsonl(VERIFIED_OUTPUT, verified, "verified questions (gold_chunk_ids filled in)")

    print(
        f"[Eval] {len(verified)} verified vs 200 needed for the release gate -- "
        "see task-9-report.md for what verification means and how to add more"
    )


if __name__ == "__main__":
    main()
