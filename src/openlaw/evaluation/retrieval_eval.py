"""Measuring the three open retrieval questions this project has been running on
anecdote instead of numbers (see rulings-addendum.md and task-9-report.md):

1. **Is the sparse signal helping or hurting?** `evaluate_retrieval_modes` runs
   every evaluation question through dense-only, sparse-only, and hybrid
   search and reports precision@1 and recall@k for each. Live probing on three
   real citizen queries found sparse-only returning clear noise (IPC s.375
   "rape" for "punishment for theft"; RBI banking circulars for a landlord
   deposit question) and, on one query, sparse noise taking hybrid's rank 1
   above the correct Rent Control Act -- while dense-only was accurate on all
   three. This is what makes that comparison repeatable on a real question
   set instead of resting on three anecdotes.
2. **Does graph expansion help?** `evaluate_graph_expansion` compares recall
   from primary hybrid hits alone against primary-plus-graph-expanded hits.
   `Settings.graph_expansion_enabled` defaults to False (see
   `openlaw.retrieval.rerank`'s module docstring) because live measurement
   found it returned zero interpreting judgments on three queries and mostly
   injected supernode noise via `REFERENCES`. This function measures whether
   that verdict holds on whatever real, gold-labelled questions exist.
3. **What is the actual recall@10?** Both functions report against
   `openlaw.evaluation.metrics.RECALL_K` by default, the spec's release-gate
   `k`.

Neither function talks to Qdrant or Neo4j directly -- `client`/`qdrant_url`
and `expand_fn` are all caller-supplied (an `httpx.Client` and
`openlaw.graph.queries.expand_chunks` bound to a real driver, in production;
fakes in tests), following the same injection pattern as
`openlaw.answers.generate.ExpandFn`. `client` is typed `Any`, matching
`openlaw.answers.generate.build_retrieval_context`'s `search_client: Any` --
both are duck-typed to whatever `openlaw.retrieval.search.search` accepts.
This keeps every test here a fast unit test with no live service, and keeps
this module blind to which embedder backend is configured
(`openlaw.retrieval.factory.build_query_embedder` decides that).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from openlaw.evaluation.dataset import EvalQuestion
from openlaw.evaluation.metrics import RECALL_K, precision_at_1, recall_at_k
from openlaw.retrieval.query import QueryEmbedder
from openlaw.retrieval.search import search, search_dense_only, search_sparse_only

#: A function turning graph-expansion rows for a set of primary chunk ids into
#: further chunk ids -- the same row shape `openlaw.graph.queries.expand_chunks`
#: returns (dicts carrying at least an `"id"` key). Injected rather than this
#: module calling `expand_chunks` itself, so evaluation never needs a live
#: Neo4j driver in a unit test.
ExpandFn = Callable[[tuple[str, ...]], list[dict[str, Any]]]


class RetrievalMode(Enum):
    """Which retrieval signal produced a set of results."""

    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class ModeMetrics:
    """Aggregate precision@1 and recall@k for one retrieval mode."""

    mode: RetrievalMode
    questions: int
    mean_precision_at_1: float
    mean_recall_at_k: float
    k: int


@dataclass(frozen=True)
class GraphExpansionMetrics:
    """Recall with and without graph expansion merged into the retrieved set.

    `expanded_recall` counts a gold chunk found anywhere in the expanded set,
    not within any fixed `k` -- the question this answers is "can expansion
    ever surface the right source at all", not "does it rank it in the top
    k", since `expand_chunks` returns no relevance score to rank by (see
    `openlaw.retrieval.rerank`'s module docstring).
    """

    questions: int
    baseline_recall: float
    expanded_recall: float
    questions_gaining_gold: int


def _mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean of `values`, or 0.0 when empty."""
    return sum(values) / len(values) if values else 0.0


def _chunk_ids(chunks: Sequence[Any]) -> tuple[str, ...]:
    """Return the `chunk_id` of every chunk in `chunks`, in order."""
    return tuple(chunk.chunk_id for chunk in chunks)


def _retrieve_all_modes(
    client: Any,
    qdrant_url: str,
    embedder: QueryEmbedder,
    question: EvalQuestion,
    limit: int,
) -> dict[RetrievalMode, tuple[str, ...]]:
    """Return the retrieved chunk ids for `question` under all three modes.

    Embeds the query once and reuses the vector for the dense and hybrid
    calls -- sparse-only needs no vector at all, since it is built from the
    raw question text independently (see `openlaw.retrieval.search`).
    """
    vector = embedder.embed_query(question.question)
    return {
        RetrievalMode.DENSE: _chunk_ids(
            search_dense_only(client, qdrant_url, vector, limit=limit)
        ),
        RetrievalMode.SPARSE: _chunk_ids(
            search_sparse_only(client, qdrant_url, question.question, limit=limit)
        ),
        RetrievalMode.HYBRID: _chunk_ids(
            search(client, qdrant_url, question.question, vector, limit=limit)
        ),
    }


def evaluate_retrieval_modes(
    questions: Sequence[EvalQuestion],
    client: Any,
    qdrant_url: str,
    embedder: QueryEmbedder,
    limit: int = 10,
    k: int = RECALL_K,
) -> tuple[ModeMetrics, ModeMetrics, ModeMetrics]:
    """Run every question through dense-only, sparse-only, and hybrid search.

    Returns one `ModeMetrics` per `RetrievalMode`, in `RetrievalMode` order.
    An empty `questions` produces all-zero metrics for every mode rather than
    raising -- the caller (the release-gate script) is responsible for
    reporting that zero questions means nothing was measured.
    """
    precisions: dict[RetrievalMode, list[float]] = {mode: [] for mode in RetrievalMode}
    recalls: dict[RetrievalMode, list[float]] = {mode: [] for mode in RetrievalMode}

    for question in questions:
        retrieved_by_mode = _retrieve_all_modes(client, qdrant_url, embedder, question, limit)
        for mode, retrieved in retrieved_by_mode.items():
            precisions[mode].append(precision_at_1(retrieved, question.gold_chunk_ids))
            recalls[mode].append(recall_at_k(retrieved, question.gold_chunk_ids, k=k))

    return tuple(
        ModeMetrics(
            mode=mode,
            questions=len(questions),
            mean_precision_at_1=_mean(precisions[mode]),
            mean_recall_at_k=_mean(recalls[mode]),
            k=k,
        )
        for mode in RetrievalMode
    )


def _expanded_ids(rows: list[dict[str, Any]], primary_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Return the new chunk ids `rows` add beyond `primary_ids`, deduplicated."""
    seen = set(primary_ids)
    new_ids: list[str] = []
    for row in rows:
        chunk_id = row.get("id")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        new_ids.append(chunk_id)
    return tuple(new_ids)


def evaluate_graph_expansion(
    questions: Sequence[EvalQuestion],
    client: Any,
    qdrant_url: str,
    embedder: QueryEmbedder,
    expand_fn: ExpandFn,
    limit: int = 10,
) -> GraphExpansionMetrics:
    """Compare recall from primary hybrid hits alone against primary-plus-
    graph-expansion.

    Runs regardless of `Settings.graph_expansion_enabled` -- that setting
    gates the production request path, not measurement. Recall here is
    computed over the *entire* retrieved/expanded set (no `k` truncation):
    the question is whether expansion can surface a gold chunk at all, since
    graph-expanded rows carry no relevance score to rank them by.
    """
    if not questions:
        return GraphExpansionMetrics(
            questions=0, baseline_recall=0.0, expanded_recall=0.0, questions_gaining_gold=0
        )

    baseline_recalls: list[float] = []
    expanded_recalls: list[float] = []
    gaining = 0

    for question in questions:
        vector = embedder.embed_query(question.question)
        primary_ids = _chunk_ids(
            search(client, qdrant_url, question.question, vector, limit=limit)
        )
        gold = question.gold_chunk_ids

        baseline = recall_at_k(primary_ids, gold, k=len(primary_ids))
        combined_ids = primary_ids + _expanded_ids(expand_fn(primary_ids), primary_ids)
        expanded = recall_at_k(combined_ids, gold, k=len(combined_ids))

        baseline_recalls.append(baseline)
        expanded_recalls.append(expanded)
        if expanded > baseline:
            gaining += 1

    return GraphExpansionMetrics(
        questions=len(questions),
        baseline_recall=_mean(baseline_recalls),
        expanded_recall=_mean(expanded_recalls),
        questions_gaining_gold=gaining,
    )
