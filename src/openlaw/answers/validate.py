"""The citation validator — the core of the product.

Every `chunk_id` a claim cites must be a member of the retrieval set for that
turn (`RetrievalContext.chunk_ids`). This is set membership, not parsing: there
is no regex to mis-read a citation and no second model call to pay for. That is
what makes "verified" compatible with "free".

The check is deliberately exact. A `chunk_id` must match a retrieved id
byte-for-byte — no prefix match, no case-fold, no whitespace normalisation, no
"did you mean". Any of those would let a plausible-looking hallucinated id
(e.g. an invented `IND_central_00042_s012_p1` that merely looks like a real
one) slip past as if it had been retrieved. Weakening the comparison in any of
these ways reopens exactly the hole this module exists to close.

Partial grounding is treated as no grounding. A claim mixing one real citation
with one invented one is dropped whole, because the invented part is exactly
the assertion a reader cannot check.

Scope: this validator only checks that a cited chunk was actually retrieved
this turn. It says nothing about whether that chunk is *relevant* to the
claim — retrieval can and does return irrelevant material (e.g. an RBI
banking circular outranking a Rent Control Act), and a claim citing a
genuinely-retrieved-but-irrelevant chunk will still pass. That is correct and
out of scope for this module: it is not a correctness guarantee about the
law, only a guarantee that every citation shown to the user points at
something that was actually put in front of the model.

A second, distinct hole: `DraftAnswer.summary` is free-form prose the model
writes, has no `chunk_ids` of its own, and this validator has no way to check
it. Nothing stops a model from keeping `claims` modest and well-cited while
putting a hallucination straight into the summary text. The user-visible
answer MUST therefore be composed from `ValidatedAnswer.claims` and
`.sources` — never from the model's prose. This module surfaces that prose
only as `unverified_summary`, named to say what it is: unchecked, for
logging/debugging, and never safe to render to a user. Note too that
`Claim.text` itself is model-written prose and may carry inline statutory
references (e.g. "under Section 9") that nobody has checked against the
claim's `chunk_ids` — validation here only proves the *cited chunks* were
retrieved, not that every assertion inside `text` is supported by them.

A third route for unverified prose, now closed: the text of a claim that
*failed* validation. It was preserved verbatim on `ValidatedAnswer.dropped`,
unconditionally — the one piece of prose in the whole pipeline already known
to be ungrounded, sitting on the object handed to renderers. `DroppedClaim`
now carries chunk ids only; the verbatim text lives on `RejectedClaim`,
produced by `rejected_claims` and consumed solely by the log-only,
`0600`-permissioned `HallucinationEvent` in `openlaw.answers.escalation`.

A fourth route for unverified content, now closed: a claim about the user's
own document dressed as law. Document excerpts carry no government URL;
putting one in `Source` would put a citation on screen that resolves to
nothing. `DocumentCitation` is a separate type with no `source_url` field,
so the renderer cannot accidentally treat one as the other, and rules 2 and
4 below drop any claim that mixes the namespaces.

The rules, all deterministic, all exact string equality, no model in the
loop:

1. A `Claim` survives only if EVERY `chunk_id` is in `context.chunk_ids`.
2. A `Claim` is dropped if ANY of its ids starts with
   `DOCUMENT_EXCERPT_PREFIX` — a document id in the law namespace is a
   contamination event, not a typo. This check does not depend on the two
   id spaces failing to collide: it runs before, and independently of,
   membership, so even a `RetrievalContext` that somehow held a `doc:` id
   could not launder an excerpt into a `Source`.
3. A `DocumentClaim` survives only if EVERY `excerpt_id` is in
   `document_context.excerpt_ids`.
4. A `DocumentClaim` is dropped if any of its ids does NOT start with
   `DOCUMENT_EXCERPT_PREFIX`.
5. `is_answerable = bool(kept_claims) and draft.sufficient`. Surviving
   `document_claims` alone never make an answer answerable: "your notice is
   dated 4 March" establishes nothing about the law.
6. Every dropped claim of either kind is recorded — ids only on
   `ValidatedAnswer.dropped`, verbatim text only on `RejectedClaim`.

**What this module contains, and what it cannot.** `openlaw.documents.fence`
reported honestly that fencing narrows the blast radius of an injected
document without eliminating it, and named the residue: a false
`document_claim` about that document's own contents. No id check can catch
that — such a claim cites a real excerpt, and there is no second, trusted
copy of the document to compare it against. What IS contained here is
everything that would make such a claim dangerous beyond the person's own
paperwork: it can never be a `Source`, never carries a `source_url`, never
makes a turn answerable, and is always bound to one specific excerpt, named
by filename and page, so the reader can check the quote against the
document they themselves uploaded.

One residue survives all of that and must be named, because naming it is
the only control available: a `document_claim`'s `text` is model prose, and
an injected document could get legal-sounding words into it — "your lease
says that under Section 9 you must vacate in seven days". Nothing here can
check that, for the same reason nothing can check `Claim.text`'s own inline
statutory references (see above). What this module guarantees is that such
a sentence arrives as an addendum tied to one named excerpt, with no
government URL, on a turn that is not answerable unless a separate,
corpus-grounded claim made it so. It is therefore a **binding obligation on
whatever renders this object** that `document_claims` be presented as "what
your document says", visibly apart from `claims`, and never under a
heading that reads as law. A renderer that merges the two lists reopens
exactly the hole this type split closes.
"""

import logging
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from openlaw.answers.schema import Claim, DocumentClaim, DraftAnswer
from openlaw.documents import DOCUMENT_EXCERPT_PREFIX
from openlaw.documents.context import EMPTY_DOCUMENT_CONTEXT, DocumentContext
from openlaw.retrieval.context import ChunkKind, RetrievalContext

logger = logging.getLogger(__name__)


class Source(BaseModel):
    """A citation of the LAW rendered to the user, always with a government URL.

    Every field here is copied verbatim off the `RetrievedChunk` that
    produced it — nothing is composed, inferred or defaulted to a
    plausible-looking value. `title` is absent on every judgment payload and
    `case_id`/`decision_date`/`judges`/`headnote` are absent on every
    provision payload, so a renderer must fall back honestly (to the kind,
    say) rather than print "None": absence here means "the corpus does not
    record this", never "we could not be bothered to send it".

    `kind` is REQUIRED because the server always knows it
    (`openlaw.retrieval.search.chunk_from_point` sets it from the payload)
    and the browser must never have to guess it back out of a hostname. A
    citation labelled with the wrong kind of authority is a provenance
    error, and guessing produced exactly that: before this field existed the
    interface classified every citation by URL host and title-nullness.

    The counterpart type for the user's own uploaded document is
    `DocumentCitation`, which deliberately has no `source_url` at all.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_url: str
    kind: ChunkKind
    title: str | None = None
    case_id: str | None = None
    court_code: str | None = None
    decision_date: str | None = None
    judges: tuple[str, ...] = ()
    headnote: str | None = None


class DocumentCitation(BaseModel):
    """A pointer to one excerpt of the user's OWN uploaded document.

    Deliberately has no `source_url` field — not optional, not `None`,
    absent — and `extra="forbid"` (the precedent `DocumentExcerpt` set in
    Task 6) so one cannot be added later by accident. A lease has no
    government URL and cannot acquire one through this type: a `source_url`
    here could only ever be empty, invented, or borrowed from a statute, and
    all three put a citation on screen that lies about where it leads.

    It is not a `Source` and shares no field with one beyond the ordinary
    `str`s, so no renderer iterating `sources` can be handed one, and no
    template written for a `Source` can render one: `.source_url` on this
    object raises rather than resolving to something plausible.

    `filename` and `page_number` are here so the person can find the passage
    in their own paperwork and check the quote — the only verification
    available for a statement about a document only they hold.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    excerpt_id: str
    document_id: str
    filename: str
    page_number: int


class DroppedClaim(BaseModel):
    """The citation-only record of a claim validation refused.

    Carries the unresolvable ids and *nothing else* — in particular, not the
    claim's text. This model is attached to `ValidatedAnswer`, which travels
    to whatever renders the answer, and the text of a claim that failed
    validation is precisely the prose a reader cannot check. It used to be
    carried here, which made `ValidatedAnswer.dropped[].text` a third route
    for unverified prose to reach a user (after `summary`, closed by
    `unverified_summary`). The verbatim text now lives only on
    `RejectedClaim`.

    The two id namespaces stay apart even in the refusal record: a refused
    legal claim populates `unresolvable_chunk_ids`, a refused document claim
    populates `unresolvable_excerpt_ids`, and neither ever holds the other's
    ids. Exactly one is non-empty on any record this module builds.
    """

    model_config = ConfigDict(frozen=True)

    unresolvable_chunk_ids: tuple[str, ...] = ()
    unresolvable_excerpt_ids: tuple[str, ...] = ()


class RejectedClaim(BaseModel):
    """A refused claim *with* its verbatim model-written text — log-only.

    Produced by `rejected_claims`/`rejected_document_claims` and consumed
    only by `openlaw.answers.escalation.build_hallucination_events`, whose
    `HallucinationEvent` log is append-only, `0600`-permissioned and
    retention-bounded. Deliberately not reachable from `ValidatedAnswer`: a
    diagnosis log is the one place this text belongs, and anything holding
    it must be as hard to render by accident as that log is.

    Same namespace rule as `DroppedClaim`: exactly one of the two id tuples
    is populated, according to which kind of claim was refused.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    unresolvable_chunk_ids: tuple[str, ...] = ()
    unresolvable_excerpt_ids: tuple[str, ...] = ()


class ValidatedAnswer(BaseModel):
    """The answer after validation — only verifiable claims survive.

    `unverified_summary` is the model's raw prose, never checked against any
    chunk_ids. It is `None` whenever `is_answerable` is False, so a template
    that carelessly renders it (e.g. `{{ answer.unverified_summary }}`)
    renders nothing rather than a hallucination. It is deliberately not named
    `summary`: a field named `summary` invites exactly that careless render.
    The only content ever safe to show a user is built from `claims` and
    `sources` (for the law) plus `document_claims` and `document_citations`
    (for the person's own paperwork); `unverified_summary` exists solely for
    logging/debugging. There is deliberately no document-flavoured
    equivalent of it — no "document summary" field that would escape
    validation the way `summary` once did.

    `document_claims` and `document_citations` are an ADDENDUM, never the
    legal answer. They do not enter `is_answerable`, so a turn grounded only
    in the user's own document still renders under the caller's fixed "we
    could not find a verifiable answer" message rather than instead of it.

    When every claim is dropped, `claims` and `sources` are empty and
    `is_answerable` is False, but `dropped` is preserved so the caller can
    tell "we caught a problem" apart from "there is nothing to say" — and
    show its own fixed, pre-written message. `dropped` is deliberately *not*
    renderable material: it carries ids only, never the rejected prose, so
    there is nothing on this model that a template could surface as an
    answer.
    """

    model_config = ConfigDict(frozen=True)

    unverified_summary: str | None
    claims: tuple[Claim, ...] = ()
    sources: tuple[Source, ...] = ()
    document_claims: tuple[DocumentClaim, ...] = ()
    document_citations: tuple[DocumentCitation, ...] = ()
    dropped: tuple[DroppedClaim, ...] = ()
    is_answerable: bool = False


#: Either kind of claim the model can emit. Both carry `text` and a tuple of
#: cited ids; `_cited_ids` is the one place that knows which field holds them.
_AnyClaim = TypeVar("_AnyClaim", Claim, DocumentClaim)


def _cited_ids(claim: Claim | DocumentClaim) -> tuple[str, ...]:
    """Return the ids `claim` cites, whichever namespace it belongs to."""
    if isinstance(claim, DocumentClaim):
        return claim.excerpt_ids
    return claim.chunk_ids


def _is_resolvable(
    identifier: str, permitted: frozenset[str], *, document_namespace: bool
) -> bool:
    """Whether one cited id is usable by a claim in this namespace.

    Two conditions, both required. The id must belong to the claim's own
    namespace — `DOCUMENT_EXCERPT_PREFIX` present for a document claim,
    absent for a legal one (rules 2 and 4) — and it must be an exact member
    of the ids actually shown to the model this turn (rules 1 and 3).

    The namespace test is deliberately independent of membership rather than
    left implicit in "the two id spaces never collide". That assumption is
    true of the corpus today and is not something this module should depend
    on to keep a lease out of the citation list.
    """
    belongs_to_document = identifier.startswith(DOCUMENT_EXCERPT_PREFIX)
    if belongs_to_document != document_namespace:
        return False
    return identifier in permitted


def _partition_claims(
    claims: tuple[_AnyClaim, ...],
    permitted: frozenset[str],
    *,
    document_namespace: bool,
) -> tuple[tuple[_AnyClaim, ...], tuple[RejectedClaim, ...]]:
    """Partition `claims` into those fully grounded in `permitted` and those not.

    A claim survives only if every one of its cited ids is resolvable; one
    unresolvable id drops the whole claim, and a claim citing ids from both
    namespaces is therefore dropped whole rather than trimmed back to the
    half that checked out — the half a repair would keep is exactly the
    assertion a reader cannot verify.

    The single implementation behind both `validate_answer` (which strips
    the rejected prose) and `rejected_claims`/`rejected_document_claims`
    (which keep it for the log), and behind both namespaces, so no two views
    of what was refused can ever disagree.
    """
    kept: list[_AnyClaim] = []
    rejected: list[RejectedClaim] = []

    for claim in claims:
        unresolvable = tuple(
            identifier
            for identifier in _cited_ids(claim)
            if not _is_resolvable(
                identifier, permitted, document_namespace=document_namespace
            )
        )
        if not unresolvable:
            kept.append(claim)
            continue
        rejected.append(_rejection(claim.text, unresolvable, document_namespace))
        logger.warning(
            "dropped %s claim citing unresolvable ids: %s",
            "document" if document_namespace else "corpus",
            unresolvable,
        )

    return tuple(kept), tuple(rejected)


def _rejection(
    text: str, unresolvable: tuple[str, ...], document_namespace: bool
) -> RejectedClaim:
    """Build the log-only record, filing the ids under their own namespace."""
    if document_namespace:
        return RejectedClaim(text=text, unresolvable_excerpt_ids=unresolvable)
    return RejectedClaim(text=text, unresolvable_chunk_ids=unresolvable)


def _build_sources(
    claims: tuple[Claim, ...], context: RetrievalContext
) -> tuple[Source, ...]:
    """Build the deduplicated, first-seen-order source list for `claims`.

    Only chunk_ids that survived `_partition_claims` reach here, so every
    lookup against `context` is expected to succeed; the None guard is
    defensive. Every field is copied straight off the chunk — this function
    never composes or infers one.
    """
    sources: list[Source] = []
    seen: set[str] = set()

    for claim in claims:
        for chunk_id in claim.chunk_ids:
            if chunk_id in seen:
                continue
            chunk = context.by_id(chunk_id)
            if chunk is None:
                continue
            seen.add(chunk_id)
            sources.append(
                Source(
                    chunk_id=chunk.chunk_id,
                    source_url=chunk.source_url,
                    kind=chunk.kind,
                    title=chunk.title,
                    case_id=chunk.case_id,
                    court_code=chunk.court_code,
                    decision_date=chunk.decision_date,
                    judges=chunk.judges,
                    headnote=chunk.headnote,
                )
            )

    return tuple(sources)


def _build_document_citations(
    claims: tuple[DocumentClaim, ...], context: DocumentContext
) -> tuple[DocumentCitation, ...]:
    """Build the deduplicated, first-seen-order citation list for `claims`.

    The document counterpart of `_build_sources`, and deliberately a
    separate function returning a separate type: there is no code path on
    which an excerpt could be appended to the `sources` list by mistake.
    Only excerpt_ids that survived `_partition_claims` reach here; the None
    guard is defensive, exactly as in `_build_sources`.
    """
    citations: list[DocumentCitation] = []
    seen: set[str] = set()

    for claim in claims:
        for excerpt_id in claim.excerpt_ids:
            if excerpt_id in seen:
                continue
            excerpt = context.by_id(excerpt_id)
            if excerpt is None:
                continue
            seen.add(excerpt_id)
            citations.append(
                DocumentCitation(
                    excerpt_id=excerpt.excerpt_id,
                    document_id=excerpt.document_id,
                    filename=excerpt.filename,
                    page_number=excerpt.page_number,
                )
            )

    return tuple(citations)


def validate_answer(
    draft: DraftAnswer,
    context: RetrievalContext,
    document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
) -> ValidatedAnswer:
    """Drop every claim citing an id absent from the set shown this turn.

    Legal claims are checked against `context.chunk_ids`, document claims
    against `document_context.excerpt_ids`, and a claim citing into the
    other namespace is dropped whole — see the module docstring's six rules.
    Membership is exact string equality: no case-folding, whitespace
    trimming, or prefix matching. Returns a new `ValidatedAnswer`; neither
    `draft` nor either context is ever mutated.

    With the default (empty) `document_context` this is behaviourally
    identical to the pre-Task-7 validator: no document claim can survive,
    because nothing was shown for one to cite.

    `is_answerable` turns on surviving LEGAL claims only. A turn carrying
    nothing but document claims is not answerable, however well-grounded
    those claims are in the person's own paperwork.

    `unverified_summary` carries `draft.summary` through only when the
    answer is answerable, and is `None` otherwise — it is never itself part
    of the is_answerable decision, and it is never safe to display
    regardless.
    """
    kept, rejected = _partition_claims(
        draft.claims, context.chunk_ids, document_namespace=False
    )
    kept_documents, rejected_documents = _partition_claims(
        draft.document_claims,
        document_context.excerpt_ids,
        document_namespace=True,
    )
    is_answerable = bool(kept) and draft.sufficient

    return ValidatedAnswer(
        unverified_summary=draft.summary if is_answerable else None,
        claims=kept,
        sources=_build_sources(kept, context),
        document_claims=kept_documents,
        document_citations=_build_document_citations(kept_documents, document_context),
        dropped=tuple(
            DroppedClaim(
                unresolvable_chunk_ids=claim.unresolvable_chunk_ids,
                unresolvable_excerpt_ids=claim.unresolvable_excerpt_ids,
            )
            for claim in rejected + rejected_documents
        ),
        is_answerable=is_answerable,
    )


def rejected_claims(
    draft: DraftAnswer, context: RetrievalContext
) -> tuple[RejectedClaim, ...]:
    """Return the LEGAL claims `validate_answer` refuses, with their text.

    Same partition as `validate_answer` (both call `_partition_claims`), but
    this is the log-only view: it keeps the model's prose so
    `openlaw.answers.escalation.build_hallucination_events` can record *what*
    was hallucinated. Never give the result to anything that renders.

    Deliberately corpus-only, and deliberately unchanged in meaning by the
    arrival of document claims: `HallucinationEvent` persists its ids into a
    column named `dropped_chunk_ids`
    (`openlaw.db.models.HallucinationEventRow`), and writing excerpt ids
    into it would be precisely the namespace confusion this module exists to
    prevent, one layer down in the storage schema. Refused document claims
    come from `rejected_document_claims` instead.
    """
    _, rejected = _partition_claims(
        draft.claims, context.chunk_ids, document_namespace=False
    )
    return rejected


def rejected_document_claims(
    draft: DraftAnswer, document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT
) -> tuple[RejectedClaim, ...]:
    """Return the DOCUMENT claims `validate_answer` refuses, with their text.

    The document-namespace counterpart of `rejected_claims`, on the same
    partition helper. A refused document claim is as much a model failure as
    a refused legal one — it cited an excerpt nobody showed it — and is
    worth the same diagnosis. Log-only: never give the result to anything
    that renders.
    """
    _, rejected = _partition_claims(
        draft.document_claims,
        document_context.excerpt_ids,
        document_namespace=True,
    )
    return rejected
