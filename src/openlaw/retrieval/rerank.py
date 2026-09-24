"""Merging graph-expanded chunks and reranking the result.

`ScoreReranker` is the v1 default — plain score ordering. `AuthorityReranker`
blends retrieval score with the PageRank authority precomputed by the knowledge
graph, so a landmark precedent can outrank a marginally closer obscure one. Which
reranker runs is a configuration decision, not a code change.
"""

from typing import Protocol

from openlaw.retrieval.context import RetrievedChunk


def merge_chunks(
    primary: tuple[RetrievedChunk, ...], expanded: tuple[RetrievedChunk, ...]
) -> tuple[RetrievedChunk, ...]:
    """Merge graph-expanded chunks into directly retrieved ones.

    Deduplicates by `chunk_id`; the primary chunk wins a collision so graph
    expansion can never overwrite a directly retrieved chunk's score.
    """
    seen: set[str] = {chunk.chunk_id for chunk in primary}
    merged = list(primary)
    for chunk in expanded:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        merged.append(chunk)
    return tuple(merged)


class Reranker(Protocol):
    """Anything that can reorder retrieved chunks for a query."""

    def rerank(
        self, query: str, chunks: tuple[RetrievedChunk, ...]
    ) -> tuple[RetrievedChunk, ...]:
        """Return `chunks` reordered by relevance to `query`."""
        ...


class ScoreReranker:
    """Orders purely by retrieval score. The v1 default."""

    def rerank(
        self, query: str, chunks: tuple[RetrievedChunk, ...]
    ) -> tuple[RetrievedChunk, ...]:
        """Return `chunks` ordered by descending score."""
        return tuple(sorted(chunks, key=lambda chunk: chunk.score, reverse=True))


class AuthorityReranker:
    """Blends retrieval score with graph PageRank authority."""

    def __init__(
        self,
        authority_weight: float = 0.3,
        authority: dict[str, float] | None = None,
    ) -> None:
        self._weight = authority_weight
        self._authority = authority or {}

    def rerank(
        self, query: str, chunks: tuple[RetrievedChunk, ...]
    ) -> tuple[RetrievedChunk, ...]:
        """Return `chunks` ordered by a blend of retrieval score and authority."""

        def blended(chunk: RetrievedChunk) -> float:
            authority = self._authority.get(chunk.chunk_id, 0.0)
            return (1.0 - self._weight) * chunk.score + self._weight * authority

        return tuple(sorted(chunks, key=blended, reverse=True))
