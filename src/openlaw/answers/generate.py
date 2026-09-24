"""The answer pipeline: retrieve, generate, validate, escalate.

Generation is untrusted. Every path out of `generate_answer` passes through
`validate_answer`, including the failure paths -- a malformed response or a
model refusal produces an unanswerable result, never an unvalidated one.

**Crisis routing runs before anything else.** Both `answer_question` and
`generate_answer` call `detect_crisis` first and return immediately when it
fires, before any retrieval or generation call is made. A citizen in danger
must get helpline numbers now, not after a search-and-generate round trip
whose output would be discarded anyway -- see `openlaw.answers.escalation`'s
module docstring: this is a success state, not something bolted onto the end
of the normal path.

**Retrieval wiring corrects the original task-8 brief, which predates these
decisions** (see task-8-report.md for the full accounting):

- The query embedder is always obtained through
  `openlaw.retrieval.factory.build_query_embedder`, never by constructing
  `VoyageQueryEmbedder` directly -- doing the latter would silently make a
  paid API the default and break self-hosting.
- `search()` is called with the raw question text as well as its embedding --
  the query-side sparse vector is built from that text, independently of the
  dense vector.
- Graph expansion (`merge_chunks` folding in `expand_chunks` output) runs
  only when `Settings.graph_expansion_enabled` is True (default False) AND a
  caller supplies `expand_fn`. Nothing else in the codebase reads this
  setting yet, so skipping the gate here would leave the default-off
  decision inert. `expand_fn` is injected rather than this module calling
  `openlaw.graph.queries.expand_chunks` itself, because that query returns
  bare `(id, label, source_url)` rows with no chunk text: resolving real,
  citable text for an expanded row is a separate concern this task does not
  solve, and synthesising placeholder text for a `RetrievedChunk` would
  itself be exactly the kind of unverifiable content this pipeline exists to
  prevent. See `openlaw.retrieval.rerank`'s module docstring for the full,
  measured case against expansion as a request-path default.

**The context sent to the generator is bounded.** `build_context_message`
performs no truncation of its own, so an unbounded retrieved set (primary
hits plus any graph expansion) combined with a long question could exceed
the model's context window at runtime. `generate_answer` caps the chunks it
renders to `MAX_CONTEXT_CHUNKS`, and validates the answer against that same
bounded set -- a claim cannot be credited for citing a chunk that was
retrieved but never actually shown to the model this turn.

**The user-visible answer is never composed from model prose.**
`generate_answer` returns a `ValidatedAnswer` whose only safe-to-render
content is `claims` and `sources`; `unverified_summary` is unchecked model
text and is always `None` on every path in this module where the answer is
not answerable (refusal, malformed JSON, empty completion, or crisis). See
`openlaw.answers.validate` for why that field exists at all and why a caller
must never render it.
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from openlaw.answers.escalation import (
    CrisisKind,
    HallucinationEvent,
    build_hallucination_events,
    detect_crisis,
    escalation_footer,
)
from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole
from openlaw.answers.prompts import SYSTEM_PROMPT, build_context_message
from openlaw.answers.schema import ANSWER_JSON_SCHEMA, DraftAnswer
from openlaw.answers.validate import ValidatedAnswer, validate_answer
from openlaw.config import Settings
from openlaw.retrieval.context import RetrievalContext, RetrievedChunk
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.query import QueryEmbedder
from openlaw.retrieval.rerank import merge_chunks
from openlaw.retrieval.search import search

MAX_ANSWER_TOKENS = 4096

#: Caps the number of retrieved chunks rendered into the generator's context
#: message. 20 covers the default primary search limit (10, see
#: `openlaw.retrieval.search.search`) plus the default graph-expansion cap
#: (`openlaw.retrieval.rerank.DEFAULT_MAX_EXPANDED`, 10) without losing any
#: chunk in the common, expansion-off case.
MAX_CONTEXT_CHUNKS = 20

_UNANSWERABLE_ANSWER = ValidatedAnswer(unverified_summary=None, is_answerable=False)

#: A function that expands a set of primary chunk ids into further,
#: already-resolved `RetrievedChunk`s (real text and source URL included).
#: Injected by the caller rather than this module reaching into
#: `openlaw.graph.queries.expand_chunks` -- see the module docstring.
ExpandFn = Callable[[tuple[str, ...]], tuple[RetrievedChunk, ...]]


class AnswerResult(BaseModel):
    """Everything one turn produces."""

    model_config = ConfigDict(frozen=True)

    answer: ValidatedAnswer
    crisis: CrisisKind | None = None
    footer: str = ""
    events: tuple[HallucinationEvent, ...] = ()


def _crisis_result(crisis: CrisisKind, footer: str) -> AnswerResult:
    """Return the immediate, generation-free result for a crisis question."""
    return AnswerResult(answer=_UNANSWERABLE_ANSWER, crisis=crisis, footer=footer)


def _bounded_context(context: RetrievalContext) -> RetrievalContext:
    """Return `context` capped to `MAX_CONTEXT_CHUNKS` chunks.

    Never mutates `context`. Chunks are assumed already ordered by relevance
    (retrieval score, or a reranker upstream), so capping keeps the strongest
    chunks and drops the weakest ones off the end.
    """
    if len(context.chunks) <= MAX_CONTEXT_CHUNKS:
        return context
    return RetrievalContext(
        query=context.query, chunks=context.chunks[:MAX_CONTEXT_CHUNKS]
    )


def generate_answer(
    question: str,
    context: RetrievalContext,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    state: str | None = None,
) -> AnswerResult:
    """Generate, validate and package one answer.

    Crisis routing runs first and short-circuits before `adapter` is ever
    called. Otherwise a malformed or refused generation still yields an
    unanswerable result, never an unvalidated one, and the answer is always
    validated against the same bounded context that was actually rendered
    into the generator's prompt.
    """
    footer = escalation_footer(state)
    crisis = detect_crisis(question)
    if crisis is not None:
        return _crisis_result(crisis, footer)

    bounded = _bounded_context(context)
    completion = adapter.complete(
        model=registry.for_role(ModelRole.GENERATOR),
        system=SYSTEM_PROMPT,
        messages=[build_context_message(bounded)],
        output_schema=ANSWER_JSON_SCHEMA,
        max_tokens=MAX_ANSWER_TOKENS,
    )

    if completion.stop_reason == "refusal" or not completion.text.strip():
        return AnswerResult(answer=_UNANSWERABLE_ANSWER, footer=footer)

    try:
        draft = DraftAnswer.from_json(completion.text)
    except ValueError:
        return AnswerResult(answer=_UNANSWERABLE_ANSWER, footer=footer)

    validated = validate_answer(draft, bounded)
    return AnswerResult(
        answer=validated,
        footer=footer,
        events=build_hallucination_events(question, validated, bounded),
    )


def build_retrieval_context(
    question: str,
    settings: Settings,
    search_client: Any,
    embedder: QueryEmbedder | None = None,
    expand_fn: ExpandFn | None = None,
    limit: int = 10,
) -> RetrievalContext:
    """Retrieve, and optionally graph-expand, the sources for `question`.

    `search_client` is duck-typed to whatever `openlaw.retrieval.search.search`
    accepts (an `httpx.Client`, or a test fake with a matching `.post`).

    The embedder is always `build_query_embedder(settings)` unless a caller
    injects one directly (e.g. a test, or a caller that already built one) --
    this must never construct `VoyageQueryEmbedder` itself, since that would
    silently make a paid API the default. `search()` receives `question`
    itself, not just its embedding, since the query-side sparse vector is
    built from the raw text.

    Graph expansion runs only when both `settings.graph_expansion_enabled` is
    True and `expand_fn` is supplied -- see the module docstring for why this
    function does not call `expand_chunks` itself.
    """
    active_embedder = embedder or build_query_embedder(settings)
    vector = active_embedder.embed_query(question)
    primary = search(
        search_client, settings.qdrant_url, text=question, vector=vector, limit=limit
    )

    chunks = primary
    if settings.graph_expansion_enabled and expand_fn is not None:
        primary_ids = tuple(chunk.chunk_id for chunk in primary)
        chunks = merge_chunks(primary, expand_fn(primary_ids))

    return RetrievalContext(query=question, chunks=chunks)


def answer_question(
    question: str,
    settings: Settings,
    search_client: Any,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    embedder: QueryEmbedder | None = None,
    expand_fn: ExpandFn | None = None,
    state: str | None = None,
) -> AnswerResult:
    """The full path from a citizen's question to a verified answer.

    Crisis routing comes first, ahead of retrieval and generation both: on a
    crisis question, neither `search_client` nor `adapter` is ever touched --
    a person in danger gets help resources immediately rather than waiting on
    a round trip whose output would be discarded anyway.
    """
    crisis = detect_crisis(question)
    if crisis is not None:
        return _crisis_result(crisis, escalation_footer(state))

    context = build_retrieval_context(
        question, settings, search_client, embedder, expand_fn
    )
    return generate_answer(question, context, adapter, registry, state)
