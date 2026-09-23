from openlaw.index.sparse import (
    BM25_B,
    BM25_K1,
    SPARSE_VECTOR_NAME,
    average_token_count,
    bm25_term_weight,
    bm25_vector,
    token_index,
    tokenise,
)

AVG_LEN = 300.0


def test_sparse_vector_name_matches_the_published_collections() -> None:
    """Both collections already declare a named sparse vector called 'sparse'."""
    assert SPARSE_VECTOR_NAME == "sparse"


def test_tokenise_lowercases_and_splits_on_punctuation() -> None:
    assert tokenise("Short title, and commencement.") == [
        "short",
        "title",
        "and",
        "commencement",
    ]


def test_tokenise_keeps_alphanumeric_section_numbers_intact() -> None:
    """'498A' is a single legal term; splitting it would break section lookup."""
    assert tokenise("Section 498A of the IPC") == ["section", "498a", "of", "the", "ipc"]


def test_tokenise_returns_nothing_for_text_without_words() -> None:
    assert tokenise("   —  ,. ") == []


def test_token_index_is_stable_across_calls() -> None:
    """Indexing and querying happen in different processes; the hash must agree."""
    assert token_index("arbitration") == token_index("arbitration")


def test_token_index_separates_different_tokens() -> None:
    assert token_index("arbitration") != token_index("maintenance")


def test_token_index_fits_in_an_unsigned_32_bit_slot() -> None:
    for token in ("a", "arbitration", "498a", "z" * 64):
        assert 0 <= token_index(token) < 2**32


def test_term_weight_saturates_below_the_bm25_ceiling() -> None:
    """The defect we are fixing: published raw counts reached 16+, unbounded."""
    for frequency in (1, 5, 20, 500):
        weight = bm25_term_weight(frequency, doc_len=AVG_LEN, avg_len=AVG_LEN)
        assert weight <= BM25_K1 + 1


def test_term_weight_grows_with_frequency_but_sublinearly() -> None:
    once = bm25_term_weight(1, doc_len=AVG_LEN, avg_len=AVG_LEN)
    ten_times = bm25_term_weight(10, doc_len=AVG_LEN, avg_len=AVG_LEN)

    assert ten_times > once
    assert ten_times < 10 * once


def test_a_short_document_outweighs_a_long_one_at_equal_frequency() -> None:
    """Length normalisation: one hit in a 50-token section beats one in a 5,000-token one."""
    short = bm25_term_weight(1, doc_len=50, avg_len=AVG_LEN)
    long = bm25_term_weight(1, doc_len=5_000, avg_len=AVG_LEN)

    assert short > long


def test_length_normalisation_can_be_disabled_with_b_zero() -> None:
    same = bm25_term_weight(1, doc_len=50, avg_len=AVG_LEN, b=0.0)
    other = bm25_term_weight(1, doc_len=5_000, avg_len=AVG_LEN, b=0.0)

    assert same == other
    assert BM25_B > 0  # but the default does normalise


def test_bm25_vector_has_one_entry_per_distinct_term() -> None:
    vector = bm25_vector("notice notice notice period", avg_len=AVG_LEN)

    assert len(vector["indices"]) == 2
    assert len(vector["values"]) == 2


def test_bm25_vector_indices_are_unique_and_sorted() -> None:
    vector = bm25_vector("the lessor shall refund the security deposit", avg_len=AVG_LEN)

    assert vector["indices"] == sorted(vector["indices"])
    assert len(set(vector["indices"])) == len(vector["indices"])


def test_bm25_vector_weights_a_repeated_term_above_a_single_one() -> None:
    vector = bm25_vector("deposit deposit deposit refund", avg_len=AVG_LEN)
    weights = dict(zip(vector["indices"], vector["values"], strict=True))

    assert weights[token_index("deposit")] > weights[token_index("refund")]


def test_bm25_vector_never_exceeds_the_saturation_ceiling() -> None:
    """The assertion that proves the two-scheme defect is gone."""
    vector = bm25_vector("shall " * 200 + "arbitration", avg_len=AVG_LEN)

    assert max(vector["values"]) <= BM25_K1 + 1


def test_bm25_vector_is_empty_for_text_without_words() -> None:
    vector = bm25_vector("   .,—  ", avg_len=AVG_LEN)

    assert vector == {"indices": [], "values": []}


def test_bm25_vector_falls_back_when_average_length_is_zero() -> None:
    """An empty sample must not divide by zero mid-build."""
    vector = bm25_vector("notice period", avg_len=0.0)

    assert len(vector["indices"]) == 2


def test_average_token_count_measures_the_sample() -> None:
    assert average_token_count(["one two", "three four five six"]) == 3.0


def test_average_token_count_of_nothing_is_zero() -> None:
    assert average_token_count([]) == 0.0
