from openlaw.evaluation.metrics import (
    RECALL_THRESHOLD,
    QuestionResult,
    evaluate,
    precision_at_1,
    recall_at_k,
)


def test_recall_threshold_is_eighty_five_percent() -> None:
    assert RECALL_THRESHOLD == 0.85


def test_full_recall_when_every_gold_chunk_is_retrieved() -> None:
    assert recall_at_k(("a", "b", "c"), ("a", "b"), k=10) == 1.0


def test_half_recall_when_one_of_two_gold_chunks_is_retrieved() -> None:
    assert recall_at_k(("a", "z"), ("a", "b"), k=10) == 0.5


def test_zero_recall_when_no_gold_chunk_is_retrieved() -> None:
    assert recall_at_k(("x", "y"), ("a",), k=10) == 0.0


def test_only_the_first_k_retrieved_chunks_count() -> None:
    """A gold chunk ranked 11th does not count for recall@10."""
    retrieved = tuple(f"filler{index}" for index in range(10)) + ("gold",)
    assert recall_at_k(retrieved, ("gold",), k=10) == 0.0


def test_recall_of_empty_gold_is_zero() -> None:
    assert recall_at_k(("a",), (), k=10) == 0.0


def test_report_passes_when_recall_is_high_and_nothing_unverifiable() -> None:
    report = evaluate(
        [
            QuestionResult(recall=0.9, unverifiable_citations=0),
            QuestionResult(recall=0.9, unverifiable_citations=0),
        ]
    )
    assert report.mean_recall == 0.9
    assert report.passes is True


def test_report_fails_when_recall_is_below_threshold() -> None:
    assert evaluate([QuestionResult(recall=0.5, unverifiable_citations=0)]).passes is (
        False
    )


def test_a_single_unverifiable_citation_fails_the_gate() -> None:
    """Zero is the threshold — one fabricated citation is a product failure."""
    assert evaluate([QuestionResult(recall=1.0, unverifiable_citations=1)]).passes is (
        False
    )


def test_empty_results_do_not_pass() -> None:
    assert evaluate([]).passes is False


# --- precision@1 --------------------------------------------------------
#
# Added beyond the brief: one of the three open questions this harness must
# answer is "is the sparse signal helping or hurting", measured via precision@1
# as well as recall@k (see task-9-report.md and rulings-addendum.md — sparse
# noise took hybrid rank 1 above the correct Rent Control Act on a real query).


def test_precision_at_1_is_one_when_the_top_hit_is_gold() -> None:
    assert precision_at_1(("a", "b"), ("a",)) == 1.0


def test_precision_at_1_is_one_when_the_top_hit_is_any_gold_chunk() -> None:
    assert precision_at_1(("b", "a"), ("x", "b")) == 1.0


def test_precision_at_1_is_zero_when_the_top_hit_is_not_gold() -> None:
    assert precision_at_1(("x", "a"), ("a",)) == 0.0


def test_precision_at_1_is_zero_when_nothing_was_retrieved() -> None:
    assert precision_at_1((), ("a",)) == 0.0


def test_precision_at_1_is_zero_when_there_is_no_gold_chunk() -> None:
    assert precision_at_1(("a",), ()) == 0.0
