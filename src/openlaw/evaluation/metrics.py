"""Evaluation metrics and the release gate.

Two thresholds from the spec: retrieval recall@10 at or above 0.85, and zero
unverifiable citations. The second is not a percentage — one fabricated citation
that reached a user is a product failure.

`precision_at_1` is added beyond the original task brief: it is what makes "is
the sparse signal helping or hurting" (one of the three open questions this
harness must answer) measurable rather than anecdotal — see
`openlaw.evaluation.retrieval_eval` and task-9-report.md. A retriever that puts
noise at rank 1 can still have decent recall@10 while being actively harmful to
show a citizen first.
"""

from collections.abc import Sequence
from dataclasses import dataclass

RECALL_THRESHOLD = 0.85
RECALL_K = 10


@dataclass(frozen=True)
class QuestionResult:
    """The outcome of evaluating one question."""

    recall: float
    unverifiable_citations: int


@dataclass(frozen=True)
class EvalReport:
    """Aggregate evaluation outcome."""

    questions: int
    mean_recall: float
    unverifiable_citations: int

    @property
    def passes(self) -> bool:
        """True when both spec gates are met."""
        return (
            self.questions > 0
            and self.mean_recall >= RECALL_THRESHOLD
            and self.unverifiable_citations == 0
        )


def recall_at_k(
    retrieved: tuple[str, ...], gold: tuple[str, ...], k: int = RECALL_K
) -> float:
    """Return the fraction of `gold` chunks present in the first `k` `retrieved`."""
    if not gold:
        return 0.0
    top = set(retrieved[:k])
    return sum(1 for chunk_id in gold if chunk_id in top) / len(gold)


def precision_at_1(retrieved: tuple[str, ...], gold: tuple[str, ...]) -> float:
    """Return 1.0 when the top-ranked retrieved chunk is a gold chunk, else 0.0.

    Undefined ("nothing retrieved" or "no gold chunk to match") is scored 0.0
    rather than raising -- a retriever that returned nothing did not put a
    correct answer first, whatever the reason.
    """
    if not retrieved or not gold:
        return 0.0
    return 1.0 if retrieved[0] in gold else 0.0


def evaluate(results: Sequence[QuestionResult]) -> EvalReport:
    """Aggregate per-question results into a report."""
    if not results:
        return EvalReport(questions=0, mean_recall=0.0, unverifiable_citations=0)
    return EvalReport(
        questions=len(results),
        mean_recall=sum(result.recall for result in results) / len(results),
        unverifiable_citations=sum(
            result.unverifiable_citations for result in results
        ),
    )
