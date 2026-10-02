import pytest

from openlaw.retrieval.context import ChunkKind, RetrievedChunk
from openlaw.retrieval.rerank import AuthorityReranker, ScoreReranker, merge_chunks


def _chunk(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://x.gov.in/{chunk_id}",
        score=score,
        kind=ChunkKind.JUDGMENT,
    )


def test_merge_deduplicates_by_chunk_id() -> None:
    merged = merge_chunks((_chunk("a", 0.9),), (_chunk("a", 0.1), _chunk("b", 0.2)))
    assert {chunk.chunk_id for chunk in merged} == {"a", "b"}


def test_merge_keeps_the_primary_chunk_when_ids_collide() -> None:
    """Graph expansion must not overwrite a directly retrieved chunk's score."""
    merged = merge_chunks((_chunk("a", 0.9),), (_chunk("a", 0.1),))
    assert merged[0].score == 0.9


def test_merge_preserves_primary_ordering_first() -> None:
    merged = merge_chunks((_chunk("a", 0.9),), (_chunk("b", 0.8),))
    assert [chunk.chunk_id for chunk in merged] == ["a", "b"]


def test_merge_of_empty_expansion_returns_primary() -> None:
    assert len(merge_chunks((_chunk("a", 0.9),), ())) == 1


def test_score_reranker_orders_by_descending_score() -> None:
    ranked = ScoreReranker().rerank("q", (_chunk("a", 0.2), _chunk("b", 0.9)))
    assert [chunk.chunk_id for chunk in ranked] == ["b", "a"]


def test_score_reranker_handles_empty_input() -> None:
    assert ScoreReranker().rerank("q", ()) == ()


def test_authority_reranker_promotes_a_more_authoritative_judgment() -> None:
    """A landmark precedent should outrank a marginally closer obscure one."""
    reranker = AuthorityReranker(
        authority_weight=0.5, authority={"landmark": 1.0, "obscure": 0.0}
    )
    ranked = reranker.rerank("q", (_chunk("obscure", 0.60), _chunk("landmark", 0.55)))
    assert ranked[0].chunk_id == "landmark"


def test_authority_reranker_without_authority_data_falls_back_to_score() -> None:
    reranker = AuthorityReranker(authority_weight=0.5, authority={})
    ranked = reranker.rerank("q", (_chunk("a", 0.2), _chunk("b", 0.9)))
    assert ranked[0].chunk_id == "b"


def test_authority_weight_of_zero_is_pure_score_ordering() -> None:
    reranker = AuthorityReranker(authority_weight=0.0, authority={"landmark": 1.0})
    ranked = reranker.rerank("q", (_chunk("other", 0.9), _chunk("landmark", 0.1)))
    assert ranked[0].chunk_id == "other"


def test_authority_reranker_rejects_a_weight_below_zero() -> None:
    with pytest.raises(ValueError):
        AuthorityReranker(authority_weight=-0.01)


def test_authority_reranker_rejects_a_weight_above_one() -> None:
    with pytest.raises(ValueError):
        AuthorityReranker(authority_weight=1.01)


def test_authority_reranker_accepts_the_boundary_weights() -> None:
    """0.0 and 1.0 are valid weights, not just the interior of the range."""
    AuthorityReranker(authority_weight=0.0)
    AuthorityReranker(authority_weight=1.0)


def _provision(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://x.gov.in/{chunk_id}",
        score=score,
        kind=ChunkKind.PROVISION,
    )


def test_merge_rejects_a_negative_max_expanded() -> None:
    with pytest.raises(ValueError):
        merge_chunks((_chunk("a", 0.9),), (_provision("b", 0.1),), max_expanded=-1)


def test_merge_caps_the_number_of_expanded_chunks_added() -> None:
    """Everything merged becomes citable -- expansion must not be able to
    balloon the citable surface unboundedly."""
    expanded = tuple(_provision(f"p{i}", 0.5) for i in range(20))
    merged = merge_chunks((_chunk("a", 0.9),), expanded, max_expanded=3)
    assert len(merged) == 1 + 3


def test_merge_with_max_expanded_zero_adds_nothing() -> None:
    merged = merge_chunks((_chunk("a", 0.9),), (_provision("b", 0.1),), max_expanded=0)
    assert [chunk.chunk_id for chunk in merged] == ["a"]


def test_merge_orders_surviving_expansion_by_relationship_type() -> None:
    """A Judgment-kind expanded chunk can only have come from INTERPRETS
    (nothing else touches a Judgment node under the restricted expand_chunks
    relationship set) -- it must survive a cap ahead of PROVISION-kind
    (REFERENCES-derived) expanded chunks."""
    expanded = (_provision("ref", 0.9), _chunk("interprets", 0.1))
    merged = merge_chunks((), expanded, max_expanded=1)
    assert [chunk.chunk_id for chunk in merged] == ["interprets"]


def test_merge_default_max_expanded_does_not_truncate_small_inputs() -> None:
    """The default cap must not surprise existing small-scale callers."""
    expanded = tuple(_provision(f"p{i}", 0.5) for i in range(5))
    merged = merge_chunks((_chunk("a", 0.9),), expanded)
    assert len(merged) == 1 + 5
