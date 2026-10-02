"""The document context -- a second, disjoint retrieval namespace (Plan 6 Task 6).

A person may upload their own paperwork (a lease, a notice, an FIR copy) and
ask a question against it. That document's text must never be presentable
as a citable source of law: a lease is not a statute, and nothing about the
excerpt it produces should let it be confused with one.

**Disjointness is structural, not conventional.** `DocumentExcerpt` and
`openlaw.retrieval.context.RetrievedChunk` are separate Pydantic models
whose required field sets do not overlap: a chunk requires `chunk_id`,
`source_url`, `score`, and `kind`; an excerpt requires `excerpt_id`,
`document_id`, `filename`, and `page_number`. Passing one where the other is
expected fails validation outright -- there is no field-compatible way to
coerce one into the other. `DocumentExcerpt.model_config` also sets
`extra="forbid"`, so it can never grow a `source_url` field by accident: a
lease has no government URL and cannot acquire one through this type. On
top of that, `excerpt_id` is validated to always start with
`DOCUMENT_EXCERPT_PREFIX` ("doc:"), which never collides with any
`chunk_id` the corpus produces (e.g. "IND_central_2385_s12_p0" or
"2016_INSC_1151_c3" -- neither begins with "doc:", and nothing in the
corpus pipeline can produce an id that does).

**Selection costs nothing and touches no vector store.** A document is at
most 20 pages, so its excerpts number in the dozens, not millions. Ranking
them is done with the project's existing BM25 implementation
(`openlaw.index.sparse`) run entirely in process: `tokenise` and
`bm25_term_weight` supply the term-frequency half; this module supplies a
classic BM25 IDF computed over the *document's own excerpts only* (never
the corpus, never an embedding model, never a Qdrant write). Reusing the
existing scoring primitives, rather than writing a second BM25 that could
drift from the first, is deliberate.

**Selection is deterministic.** The score for a given `(query, excerpts)`
pair depends only on those inputs; ties are broken by `(document_id,
ordinal)`, where `ordinal` is the excerpt's position in the input tuple.
The same question against the same document always selects the same
excerpts -- this matters because a later turn's citations bind to excerpt
ids.

**Selection degrades honestly.** An excerpt that shares no token with the
query scores zero and is dropped entirely, never padded in to fill
`MAX_DOCUMENT_EXCERPTS`. If nothing in the document matches the question,
`select_excerpts` returns `EMPTY_DOCUMENT_CONTEXT` rather than the
document's first few excerpts -- irrelevant text in the context is an
invitation for the model to cite it.

**The fence is wired here.** `render_document_context` is the one path by
which a document excerpt's text can reach a rendered prompt, and every
excerpt it renders is passed through `fence_excerpt` (Task 5) first, which
sanitises the text and wraps it so a model can tell "this is the person's
own paperwork" from "this is an instruction." Nothing in this module
renders excerpt text any other way.
"""

from __future__ import annotations

import math
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openlaw.documents import (
    DOCUMENT_EXCERPT_PREFIX,
    MAX_DOCUMENT_CONTEXT_CHARS,
    MAX_DOCUMENT_EXCERPTS,
)
from openlaw.documents.fence import fence_excerpt
from openlaw.index.sparse import average_token_count, bm25_term_weight, tokenise


class DocumentExcerpt(BaseModel):
    """One excerpt of a user's own uploaded document.

    Deliberately has no `source_url` field, and `extra="forbid"` means one
    cannot be added by accident later -- a document excerpt is never
    citable the way a corpus chunk is.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    excerpt_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    page_number: int = Field(gt=0)
    text: str = Field(min_length=1)

    @field_validator("excerpt_id")
    @classmethod
    def _requires_document_prefix(cls, value: str) -> str:
        if not value.startswith(DOCUMENT_EXCERPT_PREFIX):
            raise ValueError(
                f"excerpt_id must start with {DOCUMENT_EXCERPT_PREFIX!r}, got {value!r}"
            )
        return value


class DocumentContext(BaseModel):
    """The excerpts selected from one document for one turn."""

    model_config = ConfigDict(frozen=True)

    excerpts: tuple[DocumentExcerpt, ...] = ()

    @property
    def excerpt_ids(self) -> frozenset[str]:
        """The set of excerpt ids a document claim may cite this turn."""
        return frozenset(excerpt.excerpt_id for excerpt in self.excerpts)

    def by_id(self, excerpt_id: str) -> DocumentExcerpt | None:
        """Return the excerpt with `excerpt_id`, or None if not selected."""
        for excerpt in self.excerpts:
            if excerpt.excerpt_id == excerpt_id:
                return excerpt
        return None


EMPTY_DOCUMENT_CONTEXT = DocumentContext()


def _document_frequencies(token_lists: list[list[str]]) -> dict[str, int]:
    """Return, for each token, how many excerpts contain it at least once."""
    frequencies: dict[str, int] = {}
    for tokens in token_lists:
        for token in set(tokens):
            frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


def _inverse_document_frequency(term: str, frequencies: dict[str, int], total: int) -> float:
    """Classic BM25 IDF, computed over this document's own excerpts only.

    `openlaw.index.sparse.bm25_term_weight` already supplies the
    term-frequency/saturation half of BM25; that module leaves IDF to
    Qdrant's `modifier: "idf"` at query time, which has no analogue here
    (there is no Qdrant collection of one document's excerpts), so it is
    computed directly from the standard formula.
    """
    document_frequency = frequencies.get(term, 0)
    return math.log((total - document_frequency + 0.5) / (document_frequency + 0.5) + 1.0)


def _score_excerpt(
    query_tokens: list[str],
    excerpt_tokens: list[str],
    frequencies: dict[str, int],
    total_excerpts: int,
    avg_len: float,
) -> float:
    """Return the BM25 score of one excerpt's tokens against the query."""
    counts = Counter(excerpt_tokens)
    doc_len = float(len(excerpt_tokens))
    score = 0.0
    for term in query_tokens:
        frequency = counts.get(term, 0)
        if frequency == 0:
            continue
        idf = _inverse_document_frequency(term, frequencies, total_excerpts)
        score += idf * bm25_term_weight(frequency, doc_len=doc_len, avg_len=avg_len)
    return score


def _rank_excerpts(
    query_tokens: list[str], excerpts: tuple[DocumentExcerpt, ...]
) -> list[tuple[float, str, int, DocumentExcerpt]]:
    """Return excerpts that share a token with the query, best score first.

    Each entry is `(score, document_id, ordinal, excerpt)`; sorting on this
    tuple ranks by score descending (via negation) and breaks ties by
    `(document_id, ordinal)`, exactly as the interface specifies.
    """
    token_lists = [tokenise(excerpt.text) for excerpt in excerpts]
    frequencies = _document_frequencies(token_lists)
    avg_len = average_token_count(excerpt.text for excerpt in excerpts)

    scored = (
        (
            _score_excerpt(query_tokens, tokens, frequencies, len(excerpts), avg_len),
            excerpt.document_id,
            ordinal,
            excerpt,
        )
        for ordinal, (excerpt, tokens) in enumerate(zip(excerpts, token_lists))
    )
    matching = [entry for entry in scored if entry[0] > 0.0]
    matching.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    return matching


def select_excerpts(query: str, excerpts: tuple[DocumentExcerpt, ...]) -> DocumentContext:
    """Return at most `MAX_DOCUMENT_EXCERPTS` excerpts, capped at
    `MAX_DOCUMENT_CONTEXT_CHARS` total, ranked by BM25 score over this
    document's own excerpts against `query`. Excerpts that share no token
    with the query are dropped, never padded in; if none match, returns
    `EMPTY_DOCUMENT_CONTEXT`.
    """
    query_tokens = tokenise(query)
    if not excerpts or not query_tokens:
        return EMPTY_DOCUMENT_CONTEXT

    ranked = _rank_excerpts(query_tokens, excerpts)
    if not ranked:
        return EMPTY_DOCUMENT_CONTEXT

    selected: list[DocumentExcerpt] = []
    total_chars = 0
    for _, _, _, excerpt in ranked:
        if len(selected) >= MAX_DOCUMENT_EXCERPTS:
            break
        if total_chars + len(excerpt.text) > MAX_DOCUMENT_CONTEXT_CHARS:
            break
        selected.append(excerpt)
        total_chars += len(excerpt.text)

    return DocumentContext(excerpts=tuple(selected))


def render_document_context(context: DocumentContext) -> str:
    """Render `context` as fenced text ready to place in a prompt.

    This is the one path in this module that turns excerpt text into
    prompt-ready text, and every excerpt passes through `fence_excerpt`
    (`openlaw.documents.fence`) first -- sanitised and wrapped -- before
    being joined. Unfenced document text has no other way out of this
    module. Returns the empty string for `EMPTY_DOCUMENT_CONTEXT`.
    """
    return "\n\n".join(
        fence_excerpt(excerpt.excerpt_id, excerpt.page_number, excerpt.text)
        for excerpt in context.excerpts
    )
