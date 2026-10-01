"""The answer pipeline: retrieve, generate, validate, escalate.

Generation is untrusted. Every path out of `generate_answer` passes through
`validate_answer`, including the failure paths -- a malformed response or a
model refusal produces an unanswerable result, never an unvalidated one.

**Crisis routing runs before anything else.** Both `answer_question` and
`generate_answer` call `detect_crisis_with_classifier` first and return
immediately when it reports a crisis, before any retrieval or generation
call is made. That function itself runs two layers in a fixed order -- the
fast, free, deterministic regex from `openlaw.answers.escalation` first,
then, only when the regex stays silent, the CLASSIFIER-role model, which
catches euphemism, third-person framing, and non-Latin-script/other-language
phrasing that no realistic regex set reaches (see
`openlaw.answers.crisis_classifier`'s module docstring for the measured
gaps). `answer_question` performs this check exactly once and passes the
already-cleared question straight through to generation, rather than
letting `generate_answer` re-run it a second time -- a second classifier
call per question would double the crisis-detection cost of every ordinary
question for no benefit.

**A classifier failure does not short-circuit into a crisis.** If the
classifier call errors or returns something unparseable,
`detect_crisis_with_classifier` proceeds on the regex verdict (already "no
crisis" by construction) rather than guessing at a category with no
evidence -- see that module's docstring for why treating every classifier
outage as a fabricated self-harm crisis would be a worse failure mode than
the one it was meant to prevent. The failure is still never silent: it is
logged at `ERROR` and returned as a `ClassifierFailureEvent` on
`AnswerResult.classifier_failures`, so a caller can persist it (mirroring
`HallucinationEvent`'s "build now, persist later" shape) and a sustained
outage stays discoverable after the fact.

A citizen in danger must get helpline numbers now, not after a
search-and-generate round trip whose output would be discarded anyway -- see
`openlaw.answers.escalation`'s module docstring: this is a success state, not
something bolted onto the end of the normal path. The crisis short-circuit's
`footer` is built from `crisis_response(crisis)` (the category-specific
helpline: police/NHRC, Tele-MANAS, the women's helpline, or the trafficking
numbers) followed by the standard `escalation_footer` -- not the standard
footer alone. A version of this module once returned only the generic NALSA
footer on a crisis short-circuit, which meant "my husband beats me" produced
no legal content *and* no helpline, only a lawyer referral -- the exact
inversion of what crisis routing exists to do.

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

**The context sent to the generator is bounded, by count AND by size.**
`build_turn_messages` performs no truncation of its own, so an unbounded
retrieved set (primary hits plus any graph expansion) combined with a long
question could exceed the model's context window at runtime.
`generate_answer` caps the chunks it renders to `MAX_CONTEXT_CHUNKS` *and*
their combined text to `MAX_CONTEXT_CHARS` -- a count alone is not a bound,
since 20 long judgment chunks are effectively unbounded input. It then
validates the answer against that same bounded set -- a claim cannot be
credited for citing a chunk that was retrieved but never actually shown to
the model this turn.

**What gets trimmed first when a turn is saturated**, stated once so nobody
has to re-derive it from four modules. A full turn carries conversation
history, chunks carried forward from earlier turns, this turn's own fresh
retrieval, and document excerpts. They are bounded independently and they
give way in this order:

1. CARRIED CHUNKS, first and entirely if need be. `build_retrieval_context`
   merges them in AFTER the fresh hits and caps them at
   `MAX_CARRIED_CHUNKS`; `_bounded_chunks` then keeps a PREFIX, so the tail
   -- which is where the carried chunks are -- is what falls off. A claim
   whose chunk was trimmed is simply not replayed into the prompt
   (`openlaw.answers.history.build_history_messages` narrows to the ids
   actually shown), never shown with a dangling id.
2. HISTORY, oldest turn first, by `MAX_HISTORY_TURNS` then
   `MAX_HISTORY_CHARS`. Whole turns go, so a question never loses its
   answer; the newest turn is always kept.
3. FRESH RETRIEVAL, last, and only once every carried chunk is already
   gone: the person's new question keeps its own evidence.

DOCUMENT EXCERPTS are not in that contest at all. They are bounded by
`MAX_DOCUMENT_EXCERPTS`/`MAX_DOCUMENT_CONTEXT_CHARS` at selection time and
rendered into the OTHER pass's prompt (see below), so they can neither
crowd out a statute nor be crowded out by one.

**Generation is two passes when a document is attached** (Plan 6 Task 13,
ruling A). `_generate_from_context` produces `claims` and has no parameter
able to carry a document; `openlaw.answers.document_analysis
.analyse_document` produces `document_claims` and has no parameter able to
carry a chunk. `generate_turn` runs both and merges them through
`openlaw.answers.validate.with_document_answer`, which contributes the
document addendum and nothing else -- never a `Source`, never
`is_answerable`. That structure, not the prompt's instructions, is what
makes it impossible for text inside an uploaded file to steer a statement
about the law; `openlaw.answers.validate`'s "fifth route" paragraph records
the finding this closes. A turn with no document still costs exactly one
generation call.

**Both event logs a turn can produce are actually written.**
`answer_question` calls `persist_answer_events`, which appends
`HallucinationEvent`s and `ClassifierFailureEvent`s to their configured,
`0600`-permissioned paths (`Settings.hallucination_log_path` and
`Settings.classifier_failure_log_path`). `generate_answer` returns the
events without persisting them, because it takes no `Settings` -- a caller
holding one (`scripts/run_eval.py`, for instance) calls
`persist_answer_events` itself. The multilingual path
(`openlaw.language.pipeline.answer_in_language`) follows the same split and
reuses this same function: `persist_answer_events` takes the structural
`AnswerEvents` rather than `AnswerResult` specifically, so a
`LocalisedAnswer` persists through it too and a non-English turn's events
stop being built and then dropped on the floor.

**The user-visible answer is never composed from model prose.**
`generate_answer` returns a `ValidatedAnswer` whose only safe-to-render
content is `claims` and `sources`; `unverified_summary` is unchecked model
text and is always `None` on every path in this module where the answer is
not answerable (refusal, malformed JSON, empty completion, or crisis), and
`dropped` carries chunk ids only, never the rejected prose. See
`openlaw.answers.validate` for why those fields exist at all and why a
caller must never render them.
"""

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from openlaw.answers.crisis_classifier import (
    ClassifierFailureEvent,
    CrisisDetection,
    append_classifier_failure_events,
    detect_crisis_with_classifier,
)
from openlaw.answers.escalation import (
    CrisisKind,
    HallucinationEvent,
    append_events,
    build_hallucination_events,
    crisis_response,
    escalation_footer,
)
from openlaw.answers.document_analysis import analyse_document
from openlaw.answers.history import HistoryTurn, carried_chunk_ids
from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole
from openlaw.answers.prompts import SYSTEM_PROMPT, build_turn_messages
from openlaw.answers.schema import ANSWER_JSON_SCHEMA, DraftAnswer
from openlaw.answers.validate import (
    ValidatedAnswer,
    rejected_claims,
    rejected_document_claims,
    validate_answer,
    with_document_answer,
)
from openlaw.config import Settings
from openlaw.documents import MAX_CARRIED_CHUNKS
from openlaw.documents.context import EMPTY_DOCUMENT_CONTEXT, DocumentContext
from openlaw.retrieval.context import RetrievalContext, RetrievedChunk
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.fetch import fetch_chunks_by_id
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

#: Caps the total SIZE of those chunks. A count alone is not a bound: a
#: Supreme Court judgment chunk can run to tens of thousands of characters,
#: so 20 of them is effectively unbounded input on every question — an
#: unbounded per-question cost on a product priced per citizen, and a
#: context-window risk at the tail. 60,000 characters is roughly 15-20k
#: tokens (Indic scripts tokenise worse than English, so the token figure is
#: the pessimistic one) and leaves the common provision-chunk case —
#: 20 chunks of a few thousand characters — entirely untouched.
MAX_CONTEXT_CHARS = 60_000

_UNANSWERABLE_ANSWER = ValidatedAnswer(unverified_summary=None, is_answerable=False)

#: A function that expands a set of primary chunk ids into further,
#: already-resolved `RetrievedChunk`s (real text and source URL included).
#: Injected by the caller rather than this module reaching into
#: `openlaw.graph.queries.expand_chunks` -- see the module docstring.
ExpandFn = Callable[[tuple[str, ...]], tuple[RetrievedChunk, ...]]


class AnswerResult(BaseModel):
    """Everything one turn produces.

    `classifier_failures` is non-empty only when the crisis classifier could
    not be consulted this turn (a failed call, or an unparseable response) --
    see `openlaw.answers.crisis_classifier`'s module docstring for why that
    does NOT set `crisis`. It follows the same "build now, persist later"
    shape as `events` (`HallucinationEvent`), and `persist_answer_events`
    does the persisting -- `answer_question` calls it on every turn -- so a
    silent, sustained classifier outage is countable and discoverable after
    the fact, not just a single log line at the moment it happened.
    """

    model_config = ConfigDict(frozen=True)

    answer: ValidatedAnswer
    crisis: CrisisKind | None = None
    footer: str = ""
    events: tuple[HallucinationEvent, ...] = ()
    classifier_failures: tuple[ClassifierFailureEvent, ...] = ()


def _crisis_result(crisis: CrisisKind, state: str | None) -> AnswerResult:
    """Return the immediate, generation-free result for a crisis question.

    The footer leads with `crisis_response(crisis)` -- the category-specific
    helpline numbers (police/NHRC, Tele-MANAS, the women's helpline, or the
    trafficking numbers) -- followed by the standard `escalation_footer`. A
    crisis short-circuit that surfaced only the generic NALSA footer would
    hand a person in danger a lawyer referral instead of the emergency
    numbers this code path exists to deliver; see the module docstring.
    """
    combined_footer = f"{crisis_response(crisis)}\n\n{escalation_footer(state)}"
    return AnswerResult(
        answer=_UNANSWERABLE_ANSWER, crisis=crisis, footer=combined_footer
    )


def _bounded_chunks(chunks: tuple[RetrievedChunk, ...]) -> tuple[RetrievedChunk, ...]:
    """Return the leading chunks that fit within BOTH context bounds.

    Keeps a prefix rather than cherry-picking whichever chunks happen to fit:
    chunks arrive ordered by relevance, and skipping a long strong hit to fit
    a short weak one behind it would quietly reorder the evidence.

    The first chunk is always kept, even when it alone exceeds
    `MAX_CONTEXT_CHARS` — dropping everything would make the question
    unanswerable outright, which is a worse outcome than one oversized chunk,
    and the bound still holds for every chunk after it.
    """
    kept: list[RetrievedChunk] = []
    remaining = MAX_CONTEXT_CHARS
    for chunk in chunks[:MAX_CONTEXT_CHUNKS]:
        if kept and len(chunk.text) > remaining:
            break
        kept.append(chunk)
        remaining -= len(chunk.text)
    return tuple(kept)


def _bounded_context(question: str, context: RetrievalContext) -> RetrievalContext:
    """Return a context carrying `question` as its query, capped to
    `MAX_CONTEXT_CHUNKS` chunks and `MAX_CONTEXT_CHARS` characters of text.

    Never mutates `context`. Chunks are assumed already ordered by relevance
    (retrieval score, or a reranker upstream), so capping keeps the strongest
    chunks and drops the weakest ones off the end.

    Always rewrites `query` to `question` rather than trusting
    `context.query`: a caller can thread a `RetrievalContext` built from a
    different string than the `question` argument (this happens in tests,
    and nothing prevents it in production callers either), which would
    otherwise let the text rendered into the generator's prompt diverge from
    the text crisis detection and hallucination logging saw for the same
    turn.
    """
    return RetrievalContext(query=question, chunks=_bounded_chunks(context.chunks))


def _generate_from_context(
    question: str,
    context: RetrievalContext,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    footer: str,
    classifier_failures: tuple[ClassifierFailureEvent, ...] = (),
    history: tuple[HistoryTurn, ...] = (),
) -> AnswerResult:
    """Generate, validate and package one answer, assuming crisis routing has
    already cleared `question`.

    Shared by `generate_answer` and `answer_question` so the latter never
    re-runs crisis detection (and therefore never re-asks the classifier) for
    a question it already checked once. A malformed or refused generation
    still yields an unanswerable result, never an unvalidated one, and the
    answer is always validated against the same bounded context that was
    actually rendered into the generator's prompt. `classifier_failures` is
    threaded through onto every returned `AnswerResult` unchanged -- a
    classifier outage does not stop generation (see
    `openlaw.answers.crisis_classifier`'s module docstring), but the record
    of it must still reach the caller regardless of how generation itself
    turns out.

    **This function has no `document_context` parameter, and that absence is
    the containment for a HIGH security finding** (Plan 6 Task 13, ruling
    A). It is the only function in the codebase that produces `claims`, and
    it cannot be handed a document: uploaded text therefore cannot reach the
    prompt that writes legal assertions, so it cannot steer one under a real,
    retrieved `chunk_id` -- the route `openlaw.answers.validate`'s "fifth
    route" paragraph names and cannot itself close. A `document_context`
    parameter was added here by Task 11 and is deliberately removed again;
    document analysis is a separate pass
    (`openlaw.answers.document_analysis.analyse_document`), and the two are
    assembled by `generate_turn` below. `validate_answer` and
    `rejected_document_claims` are consequently called with their empty
    defaults: this pass is shown no excerpt, so any `document_claims` in its
    draft are fabrications, and they are dropped and logged as exactly that.

    `history` defaults to empty, so a caller without a conversation gets
    exactly the single-shot behaviour. Messages are built with
    `openlaw.answers.prompts.build_turn_messages`, which orders them
    stable-oldest-first and the question last for cache stability (see that
    module's docstring) -- the per-turn SAFETY contract (fencing,
    claims-only history, exact-id citation checking) is unchanged by that
    reordering. Because the document context it passes is empty, history is
    narrowed to legal claims only: no earlier document claim is replayed
    into this prompt either.
    """
    bounded = _bounded_context(question, context)
    completion = adapter.complete(
        model=registry.for_role(ModelRole.GENERATOR),
        system=SYSTEM_PROMPT,
        messages=build_turn_messages(bounded, EMPTY_DOCUMENT_CONTEXT, history),
        output_schema=ANSWER_JSON_SCHEMA,
        max_tokens=MAX_ANSWER_TOKENS,
    )

    if completion.stop_reason == "refusal" or not completion.text.strip():
        return AnswerResult(
            answer=_UNANSWERABLE_ANSWER,
            footer=footer,
            classifier_failures=classifier_failures,
        )

    try:
        draft = DraftAnswer.from_json(completion.text)
    except ValueError:
        return AnswerResult(
            answer=_UNANSWERABLE_ANSWER,
            footer=footer,
            classifier_failures=classifier_failures,
        )

    return AnswerResult(
        answer=validate_answer(draft, bounded),
        footer=footer,
        events=build_hallucination_events(
            question,
            # Both namespaces (security review MEDIUM-1). The document
            # namespace is checked against the EMPTY context on purpose --
            # see the docstring: this pass is shown no excerpt, so a
            # document claim here cites something nobody showed it and is a
            # fabrication worth logging, never a claim worth keeping.
            rejected_claims(draft, bounded) + rejected_document_claims(draft),
            bounded,
        ),
        classifier_failures=classifier_failures,
    )


def generate_turn(
    question: str,
    context: RetrievalContext,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    footer: str,
    classifier_failures: tuple[ClassifierFailureEvent, ...] = (),
    document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
    history: tuple[HistoryTurn, ...] = (),
) -> AnswerResult:
    """Run both generation passes for one turn and assemble their results.

    The LEGAL pass (`_generate_from_context`) runs always and is never shown
    a document. The DOCUMENT pass
    (`openlaw.answers.document_analysis.analyse_document`) runs only when
    excerpts were actually selected, and is never shown a corpus chunk. The
    two meet exactly once, in
    `openlaw.answers.validate.with_document_answer`, which copies the
    document addendum onto the legal answer and copies nothing else -- see
    ruling A, recorded in both of those modules' docstrings.

    Order matters for one thing only: the legal pass runs first, so a turn
    whose document analysis fails still carries a complete legal answer.
    Events from both passes are concatenated, legal first, so one
    `persist_answer_events` call writes both passes' refusals.

    A turn with no document costs exactly one generation call, as it did
    before the second pass existed.
    """
    result = _generate_from_context(
        question, context, adapter, registry, footer, classifier_failures, history
    )
    if not document_context.excerpts:
        return result

    analysis = analyse_document(question, document_context, adapter, registry, history)
    return result.model_copy(
        update={
            "answer": with_document_answer(result.answer, analysis.answer),
            "events": result.events + analysis.events,
        }
    )


def _failures_of(detection: CrisisDetection) -> tuple[ClassifierFailureEvent, ...]:
    """Return `(detection.failure,)` if a classifier failure was recorded,
    else `()`. Shared by `generate_answer` and `answer_question` to build the
    tuple `_generate_from_context` expects."""
    return (detection.failure,) if detection.failure is not None else ()


class AnswerEvents(Protocol):
    """Anything carrying the two event logs one turn can produce.

    Structural on purpose: `AnswerResult` (the English path) and
    `openlaw.language.pipeline.LocalisedAnswer` (the multilingual one) both
    build exactly these two tuples and neither writes them, so both can be
    handed to `persist_answer_events` at their respective boundaries without
    the multilingual path growing a second, drifting copy of the same two
    appends.
    """

    @property
    def events(self) -> tuple[HallucinationEvent, ...]:
        """Hallucination events built this turn."""
        ...

    @property
    def classifier_failures(self) -> tuple[ClassifierFailureEvent, ...]:
        """Crisis-classifier failures recorded this turn."""
        ...


def persist_answer_events(result: AnswerEvents, settings: Settings) -> None:
    """Write both event logs one turn can produce, to their configured paths.

    Both writes go through `escalation.append_jsonl_records`, so both logs
    are created (and re-asserted) `0600` and appended to, never rewritten;
    both carry verbatim user text and need identical handling. Neither log
    file is created when there is nothing to write, so an ordinary,
    event-free question touches the filesystem not at all.

    Called by `answer_question` on every non-crisis turn. Without it the
    events were built and then dropped on the floor, which made "a classifier
    outage is countable and discoverable after the fact" untrue: a sustained
    outage left nothing behind but log lines nobody was watching.
    """
    append_events(result.events, settings.hallucination_log_path)
    append_classifier_failure_events(
        result.classifier_failures, settings.classifier_failure_log_path
    )


def generate_answer(
    question: str,
    context: RetrievalContext,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    state: str | None = None,
    document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
    history: tuple[HistoryTurn, ...] = (),
) -> AnswerResult:
    """Generate, validate and package one answer.

    Crisis routing (regex, then classifier -- see
    `openlaw.answers.crisis_classifier.detect_crisis_with_classifier`) runs
    first and short-circuits before `adapter` is ever called for generation.
    A classifier failure does not short-circuit -- it proceeds to generation
    with the failure recorded on the result (see that module's docstring).

    `document_context` and `history` both default to empty, so every
    existing caller keeps the single-shot, single-pass behaviour. When a
    document IS attached, generation runs as two passes (`generate_turn`):
    this function never hands `document_context` to the pass that produces
    `claims`, because that pass has no parameter able to accept it.
    """
    detection = detect_crisis_with_classifier(question, adapter, registry)
    if detection.crisis is not None:
        return _crisis_result(detection.crisis, state)

    footer = escalation_footer(state)
    return generate_turn(
        question,
        context,
        adapter,
        registry,
        footer,
        _failures_of(detection),
        document_context,
        history,
    )


def build_retrieval_context(
    question: str,
    settings: Settings,
    search_client: Any,
    embedder: QueryEmbedder | None = None,
    expand_fn: ExpandFn | None = None,
    limit: int = 10,
    carried_chunk_ids: tuple[str, ...] = (),
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

    `carried_chunk_ids` (Task 9) is `openlaw.answers.history.carried_chunk_ids`'s
    output: corpus chunk ids an earlier turn's surviving claims depend on.
    They are re-fetched here, by id, via `openlaw.retrieval.fetch.
    fetch_chunks_by_id` -- never replayed from a cached `RetrievedChunk` --
    and merged in AFTER this turn's own primary/expanded hits, via the same
    `merge_chunks` expansion already uses: dedup by `chunk_id` with the fresh
    side winning, so an id both carried and freshly retrieved this turn is
    not duplicated, and relevance order is unchanged (fresh hits always sort
    before carried ones). This is also how the two caps interact when both
    are saturated: `_bounded_context`/`_bounded_chunks` keep a PREFIX up to
    `MAX_CONTEXT_CHUNKS`/`MAX_CONTEXT_CHARS` and trim from the tail, so a
    full carried budget is what gets trimmed first -- carried history can
    never crowd out this turn's own fresh retrieval. `max_expanded=
    MAX_CARRIED_CHUNKS` additionally bounds how many carried chunks this
    function will merge in regardless of how many ids a caller passes,
    rather than trusting every caller to have already applied that cap.
    `fetch_chunks_by_id` never raises -- an id that fails to resolve (gone
    from the corpus, or the re-fetch itself failing) is simply absent, the
    same way a never-retrieved id is absent, so no new validator branch is
    needed for carried claims to validate.
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

    carried = fetch_chunks_by_id(search_client, settings.qdrant_url, carried_chunk_ids)
    chunks = merge_chunks(chunks, carried, max_expanded=MAX_CARRIED_CHUNKS)

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
    document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
    history: tuple[HistoryTurn, ...] = (),
) -> AnswerResult:
    """The full path from a citizen's question to a verified answer.

    Crisis routing (regex, then classifier) comes first, ahead of retrieval
    and generation both: on a crisis question, neither `search_client` nor
    `adapter` is ever touched for generation -- a person in danger gets help
    resources immediately rather than waiting on a round trip whose output
    would be discarded anyway. The check runs exactly once here (not again
    inside generation), since `adapter` and `registry` are already available
    and a question cleared once does not need re-checking.

    `history` and `document_context` both default to empty. When `history`
    is given, the chunk ids its surviving claims depend on
    (`openlaw.answers.history.carried_chunk_ids`) are re-fetched into this
    turn's own retrieval, so "cited implies shown this turn" stays literally
    true and the validator needs no "cited earlier" branch. When
    `document_context` is given, generation runs as two passes -- see
    `generate_turn`.

    This is the ENGLISH entry point; the multilingual one is
    `openlaw.language.pipeline.answer_in_conversation`, which performs the
    same assembly around translation.
    """
    detection = detect_crisis_with_classifier(question, adapter, registry)
    if detection.crisis is not None:
        return _crisis_result(detection.crisis, state)

    context = build_retrieval_context(
        question,
        settings,
        search_client,
        embedder,
        expand_fn,
        carried_chunk_ids=carried_chunk_ids(history),
    )
    footer = escalation_footer(state)
    result = generate_turn(
        question,
        context,
        adapter,
        registry,
        footer,
        _failures_of(detection),
        document_context,
        history,
    )
    persist_answer_events(result, settings)
    return result
