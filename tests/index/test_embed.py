import sys
import types

import pytest

from openlaw.index.embed import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_RETRIES,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    MAX_BATCH_TOKENS,
    VoyageEmbedder,
    batched,
    batched_by_tokens,
)


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


# --- Critical 1: retries survive a transient API error ------------------------


def test_default_max_retries_is_five() -> None:
    assert DEFAULT_MAX_RETRIES == 5


def test_voyage_embedder_passes_max_retries_to_the_client(monkeypatch) -> None:
    """A single 429/503/timeout among ~2,900 calls must not kill the run."""
    captured: dict[str, object] = {}

    class FakeVoyageClient:
        def __init__(self, api_key: str, max_retries: int) -> None:
            captured["api_key"] = api_key
            captured["max_retries"] = max_retries

    fake_module = types.SimpleNamespace(Client=FakeVoyageClient)
    monkeypatch.setitem(sys.modules, "voyageai", fake_module)

    VoyageEmbedder(api_key="k")

    assert captured["max_retries"] == DEFAULT_MAX_RETRIES


def test_voyage_embedder_accepts_an_explicit_max_retries(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeVoyageClient:
        def __init__(self, api_key: str, max_retries: int) -> None:
            captured["max_retries"] = max_retries

    fake_module = types.SimpleNamespace(Client=FakeVoyageClient)
    monkeypatch.setitem(sys.modules, "voyageai", fake_module)

    VoyageEmbedder(api_key="k", max_retries=9)

    assert captured["max_retries"] == 9


# --- Critical 2: batches are sized in tokens too -------------------------------


def _text_of(item: str) -> str:
    return item


def test_batched_by_tokens_splits_evenly_by_item_count() -> None:
    items = ["a", "b", "c", "d"]
    result = list(batched_by_tokens(items, max_items=2, max_tokens=1_000, text_of=_text_of))
    assert result == [["a", "b"], ["c", "d"]]


def test_batched_by_tokens_yields_a_short_final_batch() -> None:
    items = ["a", "b", "c"]
    result = list(batched_by_tokens(items, max_items=2, max_tokens=1_000, text_of=_text_of))
    assert result == [["a", "b"], ["c"]]


def test_batched_by_tokens_of_empty_input_yields_nothing() -> None:
    assert list(batched_by_tokens([], max_items=10, max_tokens=1_000, text_of=_text_of)) == []


def test_batched_by_tokens_splits_on_the_token_budget_before_item_count() -> None:
    # Each item is CHARS_PER_TOKEN * 100 chars -> ~100 estimated tokens.
    item = "x" * (CHARS_PER_TOKEN * 100)
    items = [item, item, item]
    result = list(
        batched_by_tokens(items, max_items=10, max_tokens=150, text_of=_text_of)
    )
    # 100 + 100 > 150, so the token budget splits the batch before max_items does.
    assert result == [[item], [item], [item]]


def test_batched_by_tokens_yields_an_oversized_single_item_alone() -> None:
    """An item that alone exceeds max_tokens must not be dropped or loop forever."""
    huge = "x" * (CHARS_PER_TOKEN * 1_000)
    normal = "y" * CHARS_PER_TOKEN
    items = [normal, huge, normal]

    result = list(batched_by_tokens(items, max_items=10, max_tokens=100, text_of=_text_of))

    assert result == [[normal], [huge], [normal]]


def test_batched_by_tokens_never_exceeds_max_tokens_unless_a_single_item() -> None:
    max_tokens = 50
    items = ["a" * (CHARS_PER_TOKEN * 7) for _ in range(37)]

    for batch in batched_by_tokens(items, max_items=100, max_tokens=max_tokens, text_of=_text_of):
        total = sum(len(t) / CHARS_PER_TOKEN for t in batch)
        assert total <= max_tokens or len(batch) == 1


def test_batched_by_tokens_rejects_non_positive_max_items() -> None:
    with pytest.raises(ValueError):
        list(batched_by_tokens(["a"], max_items=0, max_tokens=100, text_of=_text_of))


def test_batched_by_tokens_rejects_non_positive_max_tokens() -> None:
    with pytest.raises(ValueError):
        list(batched_by_tokens(["a"], max_items=10, max_tokens=0, text_of=_text_of))


def test_max_batch_tokens_is_conservative_headroom() -> None:
    assert MAX_BATCH_TOKENS == 60_000
