"""Run the evaluation harness and the release gate.

The spec's two hard gates: recall@10 >= 0.85 and zero unverifiable citations
(`openlaw.evaluation.metrics.RECALL_THRESHOLD`/`EvalReport.passes`). This
script reports real numbers wherever they can be measured in the current
environment, and says so plainly wherever they cannot -- see
`gate_verdict` for the exact conditions.

Retrieval is always measurable: the default query embedder
(`openlaw.retrieval.factory.build_query_embedder`) needs no API key, and both
Qdrant collections are read-only inputs. Generation needs
`OPENLAW_ANTHROPIC_API_KEY`; when it is absent, the unverifiable-citations half
of the gate is reported NOT RUN rather than assumed zero.

Beyond the recall/citation gate, this also answers the two other open
questions from task-9-report.md: whether the sparse signal helps or hurts
(`evaluate_retrieval_modes`, dense-only vs sparse-only vs hybrid), and whether
graph expansion helps (`evaluate_graph_expansion`, run only if Neo4j is
reachable; `Settings.graph_expansion_enabled` gates production traffic, not
this measurement).

Run: python scripts/run_eval.py
"""

import sys
from pathlib import Path
from typing import Any

import httpx
import neo4j

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.generate import (
    build_retrieval_context,
    generate_answer,
    persist_answer_events,
)
from openlaw.answers.models import ModelRegistry
from openlaw.config import Settings, get_settings
from openlaw.evaluation.dataset import MINIMUM_QUESTIONS, EvalQuestion, load_eval_set
from openlaw.evaluation.metrics import QuestionResult, evaluate, recall_at_k
from openlaw.evaluation.retrieval_eval import (
    GraphExpansionMetrics,
    ModeMetrics,
    evaluate_graph_expansion,
    evaluate_retrieval_modes,
)
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.query import QueryEmbedder

VERIFIED_EVAL_PATH = Path("data/eval/citizen_questions_verified.jsonl")
BACKLOG_EVAL_PATH = Path("data/eval/citizen_questions.jsonl")


def _load_verified_questions(path: Path = VERIFIED_EVAL_PATH) -> tuple[EvalQuestion, ...]:
    """Return the loadable (gold-filled) eval questions, or () if none exist."""
    try:
        return load_eval_set(path)
    except FileNotFoundError:
        print(f"[Eval] no verified eval set at {path} -- run scripts/build_eval_set.py first")
        return ()


def _backlog_count(path: Path = BACKLOG_EVAL_PATH) -> int:
    """Count real, still-unresearched questions waiting for a gold lookup."""
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _print_mode_metrics(results: tuple[ModeMetrics, ...]) -> None:
    print("\n[Eval] retrieval mode comparison (dense-only vs sparse-only vs hybrid)")
    for result in results:
        print(
            f"  {result.mode.value:8s} precision@1={result.mean_precision_at_1:.3f}"
            f"  recall@{result.k}={result.mean_recall_at_k:.3f}  n={result.questions}"
        )


def _measure_graph_expansion(
    questions: tuple[EvalQuestion, ...],
    client: httpx.Client,
    settings: Settings,
    embedder: QueryEmbedder,
) -> GraphExpansionMetrics | None:
    """Return graph-expansion metrics, or None if Neo4j is unreachable.

    Runs regardless of `settings.graph_expansion_enabled` -- that flag gates
    the production request path, not measurement.
    """
    try:
        with graph_driver(settings) as driver:
            driver.verify_connectivity()

            def expand_fn(ids: tuple[str, ...]) -> list[dict[str, Any]]:
                return expand_chunks(driver, list(ids))

            return evaluate_graph_expansion(
                questions, client, settings.qdrant_url, embedder, expand_fn
            )
    except neo4j.exceptions.ServiceUnavailable as exc:
        print(f"[Eval] graph expansion: NOT MEASURED (Neo4j unreachable: {exc})")
        return None


def _print_graph_expansion(metrics: GraphExpansionMetrics | None) -> None:
    if metrics is None or metrics.questions == 0:
        return
    print("\n[Eval] graph expansion (primary hybrid hits vs primary+expanded, uncapped)")
    print(f"  baseline recall : {metrics.baseline_recall:.3f}")
    print(f"  expanded recall : {metrics.expanded_recall:.3f}")
    print(
        f"  questions where expansion added a gold chunk: "
        f"{metrics.questions_gaining_gold}/{metrics.questions}"
    )


def _run_generation_gate(
    questions: tuple[EvalQuestion, ...],
    client: httpx.Client,
    settings: Settings,
    embedder: QueryEmbedder,
) -> tuple[QuestionResult, ...] | None:
    """Run full generation (recall@10 + unverifiable citations) when an
    Anthropic key is configured; otherwise report NOT RUN and return None."""
    if not settings.anthropic_api_key:
        print("\n[Eval] unverifiable citations: NOT RUN (no OPENLAW_ANTHROPIC_API_KEY)")
        return None

    adapter = AnthropicAdapter(settings.anthropic_api_key)
    registry = ModelRegistry.from_settings(settings)

    results = []
    for question in questions:
        context = build_retrieval_context(question.question, settings, client, embedder)
        retrieved_ids = tuple(chunk.chunk_id for chunk in context.chunks)
        recall = recall_at_k(retrieved_ids, question.gold_chunk_ids)
        outcome = generate_answer(question.question, context, adapter, registry)
        # `generate_answer` takes no Settings, so it returns its events
        # unpersisted; this loop holds one, so it writes them to the
        # 0600-hardened logs -- an eval run is exactly when a classifier
        # outage or a burst of hallucinated citations must leave a record.
        persist_answer_events(outcome, settings)
        unverifiable = sum(len(d.unresolvable_chunk_ids) for d in outcome.answer.dropped)
        results.append(QuestionResult(recall=recall, unverifiable_citations=unverifiable))
    return tuple(results)


def gate_verdict(
    n_verified: int,
    minimum: int,
    generation_results: tuple[QuestionResult, ...] | None,
) -> str:
    """Return the honest release-gate verdict.

    Never reports PASS or FAIL unless both preconditions hold: enough
    verified questions to trust the number, and generation actually ran (so
    unverifiable citations were actually counted, not assumed zero). A fake
    PASS on an inadequate set is worse than an admitted "cannot certify" --
    see task-9-report.md.
    """
    reasons = []
    if n_verified < minimum:
        reasons.append(
            f"only {n_verified} of {minimum} required questions have a verified gold answer"
        )
    if generation_results is None:
        reasons.append(
            "generation did not run (no Anthropic API key) -- unverifiable "
            "citations were not counted"
        )
    if reasons:
        return "CANNOT BE CERTIFIED: " + "; ".join(reasons)

    report = evaluate(generation_results)
    verdict = "PASS" if report.passes else "FAIL"
    return f"{verdict} (mean_recall={report.mean_recall:.3f}, unverifiable={report.unverifiable_citations})"


def _run_measurements(
    questions: tuple[EvalQuestion, ...], client: httpx.Client, settings: Settings
) -> tuple[tuple[ModeMetrics, ...], tuple[QuestionResult, ...] | None]:
    """Run every measurable part of the harness, printing as it goes."""
    embedder = build_query_embedder(settings)

    mode_results = evaluate_retrieval_modes(questions, client, settings.qdrant_url, embedder)
    _print_mode_metrics(mode_results)

    expansion_metrics = _measure_graph_expansion(questions, client, settings, embedder)
    _print_graph_expansion(expansion_metrics)

    generation_results = _run_generation_gate(questions, client, settings, embedder)
    return mode_results, generation_results


def main() -> int:
    settings = get_settings()
    questions = _load_verified_questions()
    backlog = _backlog_count()

    print(
        f"[Eval] verified questions : {len(questions)} "
        f"(need >= {MINIMUM_QUESTIONS} for the release gate)"
    )
    print(f"[Eval] backlog questions  : {backlog} real questions still need a gold_chunk_ids lookup")

    if not questions:
        print("\n[Eval] FAIL: no question has a verified gold answer -- nothing to measure")
        return 1

    with httpx.Client(timeout=30) as client:
        mode_results, generation_results = _run_measurements(questions, client, settings)

    hybrid = next(result for result in mode_results if result.mode.value == "hybrid")
    print(
        f"\n[Eval] measured recall@{hybrid.k} (hybrid, production default): "
        f"{hybrid.mean_recall_at_k:.3f} (spec gate: >= 0.850)"
    )

    verdict = gate_verdict(len(questions), MINIMUM_QUESTIONS, generation_results)
    print(f"\n[Eval] RELEASE GATE: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
