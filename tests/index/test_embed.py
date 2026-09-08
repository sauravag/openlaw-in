import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, batched


def test_embedding_model_is_voyage_4() -> None:
    """Must match acts_india, or similarity scores are meaningless."""
    assert EMBEDDING_MODEL == "voyage-4"
    assert EMBEDDING_DIMENSIONS == 1024


def test_batched_splits_evenly() -> None:
    assert list(batched([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_batched_yields_a_short_final_batch() -> None:
    assert list(batched([1, 2, 3], 2)) == [[1, 2], [3]]


def test_batched_of_empty_input_yields_nothing() -> None:
    assert list(batched([], 10)) == []


def test_batched_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError):
        list(batched([1, 2], 0))
