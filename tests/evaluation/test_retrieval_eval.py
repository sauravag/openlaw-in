"""Tests for the dense/sparse/hybrid comparison and graph-expansion measurement.

Uses a programmable fake HTTP client (mirroring `tests/retrieval/test_search.py`'s
`_FakeClient`, but keyed by request shape/vector rather than URL alone) so a
single test can distinguish "which mode fired" without a live Qdrant -- exactly
the mechanism `openlaw.evaluation.retrieval_eval` is meant to drive against real
data.
"""

from typing import Any

from openlaw.evaluation.dataset import EvalQuestion
from openlaw.evaluation.retrieval_eval import (
    GraphExpansionMetrics,
    ModeMetrics,
    RetrievalMode,
    evaluate_graph_expansion,
    evaluate_retrieval_modes,
)
from openlaw.retrieval.query import StubQueryEmbedder
from openlaw.retrieval.search import SC_COLLECTION, build_sparse_query_vector


class _FakeResponse:
    def __init__(self, points: list[dict[str, Any]]) -> None:
        self._points = points

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"result": {"points": self._points}}


def _point(chunk_id: str, score: float) -> dict[str, Any]:
    return {
        "score": score,
        "payload": {
            "chunk_id": chunk_id,
            "text": f"text for {chunk_id}",
            "source_url": f"https://indiacode.nic.in/{chunk_id}",
        },
    }


class _ProgrammableClient:
    """Answers a POST based on the request's shape (`prefetch`/`using`) --
    lets a test assign distinct canned results to dense-only, sparse-only, and
    hybrid requests without depending on call order.
    """

    def __init__(
        self,
        dense_points: list[dict[str, Any]],
        sparse_points: list[dict[str, Any]],
        hybrid_points: list[dict[str, Any]],
    ) -> None:
        self._dense_points = dense_points
        self._sparse_points = sparse_points
        self._hybrid_points = hybrid_points
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.requests.append((url, json))
        if SC_COLLECTION in url:
            return _FakeResponse([])
        if "prefetch" in json:
            return _FakeResponse(self._hybrid_points)
        if json.get("using") == "dense":
            return _FakeResponse(self._dense_points)
        return _FakeResponse(self._sparse_points)


def test_evaluate_retrieval_modes_isolates_dense_from_sparse_noise() -> None:
    """Reproduces the measured failure mode: sparse-only surfaces noise, hybrid's
    fusion lets that noise outrank the correct chunk, dense-only is accurate."""
    question = EvalQuestion(
        question="my landlord will not return my security deposit",
        gold_chunk_ids=("rent_control_s9",),
    )
    client = _ProgrammableClient(
        dense_points=[_point("rent_control_s9", 0.9)],
        sparse_points=[_point("rbi_circular_noise", 0.8)],
        hybrid_points=[_point("rbi_circular_noise", 0.95), _point("rent_control_s9", 0.4)],
    )

    results = evaluate_retrieval_modes(
        [question], client, "http://127.0.0.1:6333", StubQueryEmbedder(), limit=10, k=10
    )

    by_mode = {result.mode: result for result in results}
    assert by_mode[RetrievalMode.DENSE].mean_precision_at_1 == 1.0
    assert by_mode[RetrievalMode.DENSE].mean_recall_at_k == 1.0
    assert by_mode[RetrievalMode.SPARSE].mean_precision_at_1 == 0.0
    assert by_mode[RetrievalMode.SPARSE].mean_recall_at_k == 0.0
    # Hybrid recovers the gold chunk (still in the top 10) but ranks noise
    # above it -- precision@1 fails even though recall@10 does not.
    assert by_mode[RetrievalMode.HYBRID].mean_precision_at_1 == 0.0
    assert by_mode[RetrievalMode.HYBRID].mean_recall_at_k == 1.0


def test_evaluate_retrieval_modes_averages_across_questions() -> None:
    perfect = EvalQuestion(question="what is the punishment for theft", gold_chunk_ids=("g1",))
    miss = EvalQuestion(question="grounds for divorce", gold_chunk_ids=("g2",))
    client = _ProgrammableClient(
        dense_points=[_point("g1", 0.9)],  # matches `perfect`'s gold only
        sparse_points=[_point("g1", 0.9)],
        hybrid_points=[_point("g1", 0.9)],
    )

    results = evaluate_retrieval_modes(
        [perfect, miss], client, "http://127.0.0.1:6333", StubQueryEmbedder(), limit=10, k=10
    )

    for result in results:
        assert result.questions == 2
        assert result.mean_precision_at_1 == 0.5  # 1.0 for `perfect`, 0.0 for `miss`


def test_evaluate_retrieval_modes_returns_zeroed_metrics_for_an_empty_question_set() -> None:
    results = evaluate_retrieval_modes(
        [], _ProgrammableClient([], [], []), "http://127.0.0.1:6333", StubQueryEmbedder()
    )
    assert {result.mode for result in results} == set(RetrievalMode)
    for result in results:
        assert result.questions == 0
        assert result.mean_precision_at_1 == 0.0
        assert result.mean_recall_at_k == 0.0


def test_evaluate_retrieval_modes_uses_the_shared_sparse_vector_builder() -> None:
    """The sparse-only request must reflect the raw question text, matching the
    production `search` code path's sparse vector exactly."""
    question = EvalQuestion(question="theft punishment", gold_chunk_ids=("g",))
    client = _ProgrammableClient([], [], [])

    evaluate_retrieval_modes([question], client, "http://127.0.0.1:6333", StubQueryEmbedder())

    sparse_requests = [body for _, body in client.requests if body.get("using") == "sparse"]
    assert sparse_requests
    assert sparse_requests[0]["query"] == {
        "nearest": build_sparse_query_vector("theft punishment")
    }


# --- graph expansion -----------------------------------------------------


def _search_only_client(points: list[dict[str, Any]]) -> _ProgrammableClient:
    """A client whose hybrid `search()` calls return `points` -- used by the
    graph-expansion tests, which only ever call hybrid `search`."""
    return _ProgrammableClient(dense_points=[], sparse_points=[], hybrid_points=points)


def test_graph_expansion_recovers_a_gold_chunk_missed_by_primary_search() -> None:
    question = EvalQuestion(question="q", gold_chunk_ids=("expanded_gold",))
    client = _search_only_client([_point("primary_miss", 0.5)])

    def expand_fn(primary_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        assert primary_ids == ("primary_miss",)
        return [{"id": "expanded_gold", "label": "Section", "source_url": "https://x"}]

    metrics = evaluate_graph_expansion(
        [question], client, "http://127.0.0.1:6333", StubQueryEmbedder(), expand_fn
    )

    assert metrics.questions == 1
    assert metrics.baseline_recall == 0.0
    assert metrics.expanded_recall == 1.0
    assert metrics.questions_gaining_gold == 1


def test_graph_expansion_reports_no_gain_when_expansion_adds_nothing_new() -> None:
    question = EvalQuestion(question="q", gold_chunk_ids=("primary_hit",))
    client = _search_only_client([_point("primary_hit", 0.9)])

    metrics = evaluate_graph_expansion(
        [question],
        client,
        "http://127.0.0.1:6333",
        StubQueryEmbedder(),
        expand_fn=lambda ids: [{"id": "noise_1"}, {"id": "noise_2"}],
    )

    assert metrics.baseline_recall == 1.0
    assert metrics.expanded_recall == 1.0
    assert metrics.questions_gaining_gold == 0


def test_graph_expansion_deduplicates_rows_already_in_the_primary_set() -> None:
    question = EvalQuestion(question="q", gold_chunk_ids=("primary_hit",))
    client = _search_only_client([_point("primary_hit", 0.9)])
    calls: list[tuple[str, ...]] = []

    def expand_fn(primary_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        calls.append(primary_ids)
        return [{"id": "primary_hit"}]  # already in the primary set

    metrics = evaluate_graph_expansion(
        [question], client, "http://127.0.0.1:6333", StubQueryEmbedder(), expand_fn
    )

    assert calls == [("primary_hit",)]
    assert metrics.expanded_recall == metrics.baseline_recall == 1.0


def test_graph_expansion_ignores_rows_with_no_id() -> None:
    question = EvalQuestion(question="q", gold_chunk_ids=("gold",))
    client = _search_only_client([])

    metrics = evaluate_graph_expansion(
        [question],
        client,
        "http://127.0.0.1:6333",
        StubQueryEmbedder(),
        expand_fn=lambda ids: [{"id": None}, {"label": "Act"}],
    )

    assert metrics.baseline_recall == 0.0
    assert metrics.expanded_recall == 0.0


def test_graph_expansion_of_an_empty_question_set_is_all_zero() -> None:
    metrics = evaluate_graph_expansion(
        [],
        _search_only_client([]),
        "http://127.0.0.1:6333",
        StubQueryEmbedder(),
        expand_fn=lambda ids: [],
    )
    assert metrics == GraphExpansionMetrics(
        questions=0, baseline_recall=0.0, expanded_recall=0.0, questions_gaining_gold=0
    )


def test_mode_metrics_are_frozen() -> None:
    """Immutability contract (project-wide): results are never mutated in place."""
    metrics = ModeMetrics(
        mode=RetrievalMode.DENSE, questions=1, mean_precision_at_1=1.0, mean_recall_at_k=1.0, k=10
    )
    try:
        metrics.questions = 2  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised
