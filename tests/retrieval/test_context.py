import pytest
from pydantic import ValidationError

from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk


def _chunk(chunk_id: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://www.indiacode.nic.in/{chunk_id}",
        score=score,
        kind=ChunkKind.PROVISION,
    )


def test_chunk_requires_a_source_url() -> None:
    """The core guarantee: nothing without a government URL enters the context."""
    with pytest.raises(ValidationError):
        RetrievedChunk(
            chunk_id="c1", text="t", source_url="", score=0.5,
            kind=ChunkKind.PROVISION,
        )


def test_chunk_requires_non_empty_text() -> None:
    with pytest.raises(ValidationError):
        RetrievedChunk(
            chunk_id="c1", text="", source_url="https://x.gov.in",
            score=0.5, kind=ChunkKind.PROVISION,
        )


def test_chunk_is_immutable() -> None:
    chunk = _chunk("c1")
    with pytest.raises(ValidationError):
        chunk.score = 0.1


def test_context_exposes_its_chunk_ids_as_a_set() -> None:
    context = RetrievalContext(query="q", chunks=(_chunk("a"), _chunk("b")))
    assert context.chunk_ids == frozenset({"a", "b"})


def test_context_looks_up_a_chunk_by_id() -> None:
    found = RetrievalContext(query="q", chunks=(_chunk("a"),)).by_id("a")
    assert found is not None
    assert found.chunk_id == "a"


def test_context_returns_none_for_an_unknown_chunk_id() -> None:
    assert RetrievalContext(query="q", chunks=()).by_id("missing") is None


def test_empty_context_has_no_chunk_ids() -> None:
    assert RetrievalContext(query="q", chunks=()).chunk_ids == frozenset()
