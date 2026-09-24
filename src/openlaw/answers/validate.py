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
"""

import logging

from pydantic import BaseModel, ConfigDict

from openlaw.answers.schema import Claim, DraftAnswer
from openlaw.retrieval.context import RetrievalContext

logger = logging.getLogger(__name__)


class Source(BaseModel):
    """A citation rendered to the user, always with a government URL."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_url: str
    title: str | None = None


class DroppedClaim(BaseModel):
    """The citation-only record of a claim validation refused.

    Carries the unresolvable chunk_ids and *nothing else* — in particular,
    not the claim's text. This model is attached to `ValidatedAnswer`, which
    travels to whatever renders the answer, and the text of a claim that
    failed validation is precisely the prose a reader cannot check. It used
    to be carried here, which made `ValidatedAnswer.dropped[].text` a third
    route for unverified prose to reach a user (after `summary`, closed by
    `unverified_summary`). The verbatim text now lives only on
    `RejectedClaim`.
    """

    model_config = ConfigDict(frozen=True)

    unresolvable_chunk_ids: tuple[str, ...]


class RejectedClaim(BaseModel):
    """A refused claim *with* its verbatim model-written text — log-only.

    Produced by `rejected_claims` and consumed only by
    `openlaw.answers.escalation.build_hallucination_events`, whose
    `HallucinationEvent` log is append-only, `0600`-permissioned and
    retention-bounded. Deliberately not reachable from `ValidatedAnswer`: a
    diagnosis log is the one place this text belongs, and anything holding it
    must be as hard to render by accident as that log is.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    unresolvable_chunk_ids: tuple[str, ...]


class ValidatedAnswer(BaseModel):
    """The answer after validation — only verifiable claims survive.

    `unverified_summary` is the model's raw prose, never checked against any
    chunk_ids. It is `None` whenever `is_answerable` is False, so a template
    that carelessly renders it (e.g. `{{ answer.unverified_summary }}`)
    renders nothing rather than a hallucination. It is deliberately not named
    `summary`: a field named `summary` invites exactly that careless render.
    The only content ever safe to show a user is built from `claims` and
    `sources`; `unverified_summary` exists solely for logging/debugging.

    When every claim is dropped, `claims` and `sources` are empty and
    `is_answerable` is False, but `dropped` is preserved so the caller can
    tell "we caught a problem" apart from "there is nothing to say" — and
    show its own fixed, pre-written "we could not find a verifiable answer"
    message. `dropped` is deliberately *not* renderable material: it carries
    chunk ids only, never the rejected prose, so there is nothing on this
    model that a template could surface as an answer. Nothing here is safe to
    render except `claims` and `sources`.
    """

    model_config = ConfigDict(frozen=True)

    unverified_summary: str | None
    claims: tuple[Claim, ...] = ()
    sources: tuple[Source, ...] = ()
    dropped: tuple[DroppedClaim, ...] = ()
    is_answerable: bool = False


def _unresolvable_ids(claim: Claim, permitted: frozenset[str]) -> tuple[str, ...]:
    """Return the chunk_ids on `claim` that are not in `permitted`, in order."""
    return tuple(chunk_id for chunk_id in claim.chunk_ids if chunk_id not in permitted)


def _split_claims(
    claims: tuple[Claim, ...], permitted: frozenset[str]
) -> tuple[tuple[Claim, ...], tuple[RejectedClaim, ...]]:
    """Partition `claims` into those fully grounded in `permitted` and those not.

    A claim survives only if every one of its chunk_ids is a member of
    `permitted`; one unresolvable id drops the whole claim. The single
    implementation behind both `validate_answer` (which strips the rejected
    prose) and `rejected_claims` (which keeps it for the log), so the two
    views can never disagree about what was refused.
    """
    kept: list[Claim] = []
    rejected: list[RejectedClaim] = []

    for claim in claims:
        unresolvable = _unresolvable_ids(claim, permitted)
        if unresolvable:
            rejected.append(
                RejectedClaim(text=claim.text, unresolvable_chunk_ids=unresolvable)
            )
            logger.warning(
                "dropped claim citing unresolvable chunk_ids: %s", unresolvable
            )
            continue
        kept.append(claim)

    return tuple(kept), tuple(rejected)


def _build_sources(
    claims: tuple[Claim, ...], context: RetrievalContext
) -> tuple[Source, ...]:
    """Build the deduplicated, first-seen-order source list for `claims`.

    Only chunk_ids that survived `_split_claims` reach here, so every lookup
    against `context` is expected to succeed; the None guard is defensive.
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
                    title=chunk.title,
                )
            )

    return tuple(sources)


def validate_answer(draft: DraftAnswer, context: RetrievalContext) -> ValidatedAnswer:
    """Drop every claim citing a chunk_id absent from `context.chunk_ids`.

    Membership is exact string equality against the set of ids actually
    retrieved this turn — no case-folding, whitespace trimming, or prefix
    matching. Returns a new `ValidatedAnswer`; `draft` is never mutated.

    `unverified_summary` carries `draft.summary` through only when the answer
    is answerable, and is `None` otherwise — it is never itself part of the
    is_answerable decision, and it is never safe to display regardless: see
    the module and `ValidatedAnswer` docstrings for why the user-visible
    answer must come from `claims`/`sources` instead.
    """
    permitted = context.chunk_ids
    kept, rejected = _split_claims(draft.claims, permitted)
    sources = _build_sources(kept, context)
    is_answerable = bool(kept) and draft.sufficient

    return ValidatedAnswer(
        unverified_summary=draft.summary if is_answerable else None,
        claims=kept,
        sources=sources,
        dropped=tuple(
            DroppedClaim(unresolvable_chunk_ids=claim.unresolvable_chunk_ids)
            for claim in rejected
        ),
        is_answerable=is_answerable,
    )


def rejected_claims(
    draft: DraftAnswer, context: RetrievalContext
) -> tuple[RejectedClaim, ...]:
    """Return the claims `validate_answer` refuses, with their verbatim text.

    Same partition as `validate_answer` (both call `_split_claims`), but this
    is the log-only view: it keeps the model's prose so
    `openlaw.answers.escalation.build_hallucination_events` can record *what*
    was hallucinated. Never give the result to anything that renders.
    """
    _, rejected = _split_claims(draft.claims, context.chunk_ids)
    return rejected
