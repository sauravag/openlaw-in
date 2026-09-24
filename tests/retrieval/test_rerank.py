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
