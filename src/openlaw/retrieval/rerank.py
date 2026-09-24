"""Merging graph-expanded chunks and reranking the result.

`ScoreReranker` is the v1 default — plain score ordering. `AuthorityReranker`
blends retrieval score with the PageRank authority precomputed by the knowledge
graph, so a landmark precedent can outrank a marginally closer obscure one. Which
reranker runs is a configuration decision, not a code change.

**Why `merge_chunks` caps and orders (fix round 1, Plan 3 Task 3).** Under the
answer contract, everything merged into the retrieval context becomes citable.
Live verification found `merge_chunks` uncapped meant ~10 real hits plus ~39
graph-expanded ones per query — roughly 80% of the citable surface was filler
the retriever never endorsed, and a model could validly cite a graph-expanded
chunk that is legally wrong for the question (worse than a missed citation).
`max_expanded` bounds that surface. Ordering survivors by `ChunkKind` before
truncating is a *categorical* proxy for relationship type, not a relevance
score: `expand_chunks` (see `openlaw.graph.queries`) now walks only
`INTERPRETS` and `REFERENCES` (`PART_OF` was removed there for the same
reason), and under that restricted set a `Judgment`-kind expanded chunk can
only have come from `INTERPRETS` — nothing else touches a Judgment node — a
genuine "this case interprets this statute" fact, ranked ahead of the weaker,
structural `REFERENCES` links that produce `PROVISION`-kind chunks. This is
deliberately coarse: a provision reached by two co-interpreting judgments
would also count as `REFERENCES`-tier here, because `RetrievedChunk` carries
no relationship metadata to distinguish it. That imprecision is accepted
rather than attempting a general relevance ordering on graph-expanded rows —
no principled one exists without re-embedding them against the query, which
is retrieval again at higher cost.

**Graph expansion is OFF the request path by default (fix round 2, Plan 3
Task 3).** `Settings.graph_expansion_enabled` (env `OPENLAW_GRAPH_EXPANSION_
ENABLED`) defaults to `False`. This is not "the graph was a mistake" — the
knowledge graph, `expand_chunks`, and everything in this module are correct
code serving a data condition that does not hold yet. Two measured facts
drove the default:

- **The statute-to-case-law bridge is too sparse to add signal.** Only
  ~4% of `INTERPRETS` edges reach a real `Section` (10,525 of 248,411 on the
  full corpus). Live-tested on three real citizen questions across two fix
  rounds, `INTERPRETS` fired zero times. A caller enabling expansion today
  is, in practice, only ever getting `REFERENCES` hits.
- **`REFERENCES` has its own supernode failure mode, so it is not the safe
  fallback it looks like.** Round 1 restricted expansion to
  `INTERPRETS`/`REFERENCES` (removing `PART_OF`) on the theory that a real
  cross-document legal link would be high-precision even if rare. Round 2's
  live measurement falsified that: one marginal primary hit that happened to
  reference the IT Act 2000 (6,405 other sections reference it) pulled in
  unrelated SEBI/TRAI regulatory noise two hops away, and a different query
  fanned out through *unresolved stub Acts* — ambiguous titles like
  `"establishments act 1953"` shared by 139 unrelated sections — because
  stub exclusion only guards the final hop, not an intermediate one. This is
  the same supernode pathology `expand_chunks_cypher` already documents for
  `LegalSubject`/`DefinedTerm`, just showing up through `REFERENCES` instead.

Under the answer contract every chunk `merge_chunks` adds becomes citable, so
until the data changes, an uncapped *or* capped-but-noisy addition is a real
risk (a validly-cited, legally wrong answer), and a missed citation is not.
The safe default is therefore to call `search()` alone and never call
`expand_chunks`/`merge_chunks` at all in the ordinary request path.

**When to turn it back on.** Flip `graph_expansion_enabled` only once one of
these becomes true, and re-verify against real queries before shipping the
flip, not just on the strength of a corpus-wide count:
1. `INTERPRETS` density on retrieved sections becomes measurably non-trivial
   (i.e. a meaningful share of real queries' primary hits have at least one
   real interpreting judgment) — the corpus needs better section-act
   resolution for judgment citations, not a code change here; or
2. `expand_chunks` can be restricted to links that provably do not traverse
   a supernode (e.g. capping by target-node degree, or dropping the second
   hop), so a `REFERENCES` hit is reliably precise rather than occasionally
   catastrophic.

**What the default actually costs, stated precisely.** Two earlier claims in
this docstring were wrong and are corrected here:

- `ScoreReranker` and `AuthorityReranker` do *not* "stay in the request
  path": no `Reranker` is constructed anywhere in `src/` or `scripts/`
  today — only in this module's tests. They are unaffected by the expansion
  default in the sense that they never depended on it, and they remain
  ready to use: wiring one in means constructing it and calling `rerank` on
  `search()`'s output (`AuthorityReranker` additionally needs the PageRank
  map from `openlaw.graph.algorithms`). Until a call site does that,
  retrieval order is whatever `search()` returned.
- Re-enabling expansion is *not* "flipping one setting". Setting
  `graph_expansion_enabled=True` only opens the gate in
  `openlaw.answers.generate.build_retrieval_context`; that gate also
  requires an `expand_fn`, and a production `ExpandFn` must return resolved
  `RetrievedChunk`s — real text, real `source_url`, since everything it
  returns becomes citable. Nothing in this repository can produce those:
  `openlaw.graph.queries.expand_chunks` returns bare `(id, label,
  source_url)` rows with no chunk text, and the evaluation harness's
  `openlaw.evaluation.retrieval_eval.ExpandFn` is an id-only callable — a
  different type that happens to share the name. Re-enabling therefore
  needs new code: a resolver that fetches each expanded id's real chunk
  text (from Qdrant or the corpus) and builds `RetrievedChunk`s from it,
  plus a call site that injects it. The setting is the last step, not the
  only one.
"""

from typing import Protocol

from openlaw.retrieval.context import ChunkKind, RetrievedChunk

DEFAULT_MAX_EXPANDED = 10


def _relationship_rank(chunk: RetrievedChunk) -> int:
    """Rank an expanded chunk by which relationship most plausibly produced
    it — 0 (INTERPRETS-only-reachable) ranks ahead of 1 (everything else).
    See the module docstring for why this is a coarse proxy, not a score.
    """
    return 0 if chunk.kind == ChunkKind.JUDGMENT else 1


def merge_chunks(
    primary: tuple[RetrievedChunk, ...],
    expanded: tuple[RetrievedChunk, ...],
    max_expanded: int = DEFAULT_MAX_EXPANDED,
) -> tuple[RetrievedChunk, ...]:
    """Merge graph-expanded chunks into directly retrieved ones.

    Deduplicates by `chunk_id`; the primary chunk wins a collision so graph
    expansion can never overwrite a directly retrieved chunk's score. At most
    `max_expanded` expanded chunks survive, chosen by `_relationship_rank`
    (INTERPRETS-derived before REFERENCES-derived) with original order kept
    within each tier — see the module docstring for why expansion is capped
    and ordered this way.
    """
    if max_expanded < 0:
        raise ValueError(f"max_expanded must be >= 0, got {max_expanded}")
    seen: set[str] = {chunk.chunk_id for chunk in primary}
    candidates: list[RetrievedChunk] = []
    for chunk in expanded:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        candidates.append(chunk)
    candidates.sort(key=_relationship_rank)
    return tuple(primary) + tuple(candidates[:max_expanded])


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
        """Raises ValueError when `authority_weight` is outside [0.0, 1.0] —
        a value outside that range silently inverts or overweights the blend
        in `rerank` rather than failing fast."""
        if not 0.0 <= authority_weight <= 1.0:
            raise ValueError(
                f"authority_weight must be between 0.0 and 1.0, got {authority_weight}"
            )
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
