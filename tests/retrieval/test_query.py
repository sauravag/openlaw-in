import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from openlaw.retrieval.query import QUERY_INPUT_TYPE, StubQueryEmbedder


def test_query_input_type_differs_from_document() -> None:
    """Documents were embedded with input_type='document'; queries must not be."""
    assert QUERY_INPUT_TYPE == "query"


def test_query_embedder_uses_the_same_model_as_the_corpus() -> None:
    """A different model means a different vector space and meaningless scores."""
    assert EMBEDDING_MODEL == "voyage-4"


def test_stub_embedder_returns_the_corpus_dimensionality() -> None:
    vector = StubQueryEmbedder().embed_query("what is my notice period")
    assert len(vector) == EMBEDDING_DIMENSIONS


def test_stub_embedder_is_deterministic() -> None:
    embedder = StubQueryEmbedder()
    assert embedder.embed_query("same text") == embedder.embed_query("same text")


def test_stub_embedder_distinguishes_different_texts() -> None:
    embedder = StubQueryEmbedder()
    assert embedder.embed_query("landlord deposit") != embedder.embed_query("divorce")


def test_stub_embedder_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        StubQueryEmbedder().embed_query("   ")
