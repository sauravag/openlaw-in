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
    """A claim removed because it cited chunks that were not retrieved."""

    model_config = ConfigDict(frozen=True)

    text: str
    unresolvable_chunk_ids: tuple[str, ...]


class ValidatedAnswer(BaseModel):
    """The answer after validation — only verifiable claims survive.

    When every claim is dropped, `claims` and `sources` are empty and
    `is_answerable` is False, but `summary` and `dropped` are preserved: the
    caller decides how to present the "could not verify" state (e.g. showing
    a fixed "we could not find a verifiable answer" message) rather than
    silently handing back an empty-looking answer that could be mistaken for
    "there is nothing to say" instead of "we caught a problem".
    """

    model_config = ConfigDict(frozen=True)

    summary: str
    claims: tuple[Claim, ...] = ()
    sources: tuple[Source, ...] = ()
    dropped: tuple[DroppedClaim, ...] = ()
    is_answerable: bool = False


def _unresolvable_ids(claim: Claim, permitted: frozenset[str]) -> tuple[str, ...]:
    """Return the chunk_ids on `claim` that are not in `permitted`, in order."""
    return tuple(chunk_id for chunk_id in claim.chunk_ids if chunk_id not in permitted)


def _split_claims(
    claims: tuple[Claim, ...], permitted: frozenset[str]
) -> tuple[tuple[Claim, ...], tuple[DroppedClaim, ...]]:
    """Partition `claims` into those fully grounded in `permitted` and those not.

    A claim survives only if every one of its chunk_ids is a member of
    `permitted`; one unresolvable id drops the whole claim.
    """
    kept: list[Claim] = []
    dropped: list[DroppedClaim] = []

    for claim in claims:
        unresolvable = _unresolvable_ids(claim, permitted)
        if unresolvable:
            dropped.append(
                DroppedClaim(text=claim.text, unresolvable_chunk_ids=unresolvable)
            )
            logger.warning(
                "dropped claim citing unresolvable chunk_ids: %s", unresolvable
            )
            continue
        kept.append(claim)

    return tuple(kept), tuple(dropped)


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
    """
    permitted = context.chunk_ids
    kept, dropped = _split_claims(draft.claims, permitted)
    sources = _build_sources(kept, context)

    return ValidatedAnswer(
        summary=draft.summary,
        claims=kept,
        sources=sources,
        dropped=dropped,
        is_answerable=bool(kept) and draft.sufficient,
    )
