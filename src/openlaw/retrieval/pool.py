"""The final retrieval pool: guaranteed statute slots, no duplicate sections.

`openlaw.retrieval.search` fuses each collection's ranked hits by reciprocal
rank. Capping that fused list straight to a small limit used to interleave
statutes and judgments about evenly (acts rank 1, SC rank 1, acts rank 2,
...), so a 10-chunk pool kept only statute ranks 1-5. For "my divorce case
has been stuck for years", Hindu Marriage Act s.21B -- the provision that
tells the court to try such petitions day to day and finish within six
months -- sat at statute rank 8 and never reached the generator. Worse,
three of the five statute slots that survived were the same Parsi Marriage
and Divorce Act s.32 text indexed under three ids (one central copy, two
state copies) and the other two were one Portuguese CPC 1939 passage under
two ids.

`select_pool` fixes both, in this order:

1. Drop duplicate statute chunks, keeping the highest-ranked copy
   (`provision_dedupe_key`). This runs BEFORE any cap so a duplicate can
   never spend a slot.
2. Give statutes up to `statute_floor` slots and judgments the rest, each
   kind taking its own best-ranked chunks. When one kind runs short, the
   other fills the remaining slots, so the pool is only ever smaller than
   `limit` when there is genuinely nothing left to put in it.
3. If a `max_chars` budget is given and the pool would overflow it, drop the
   lowest-ranked judgments first, then statutes above the floor -- never a
   statute inside the floor. The generator's own prefix bound
   (`openlaw.answers.generate._bounded_chunks`) would otherwise cut from the
   tail by rank, which could be a statute.

The pool keeps the fused rank order: nothing here re-scores a chunk. Only
statutes are deduplicated. Judgment chunks of different cases can share
boilerplate, and two judgments saying the same thing are two authorities.

`fit_around_reserved` is the follow-up-turn counterpart: chunks an earlier
turn's claims cite (and any graph expansion) are RESERVED first, and the
fresh pool is re-fitted into whatever count and character room is left --
judgments give way first, the statute floor never does -- so a full fresh
pool can no longer push a carried chunk off the generator's prefix bound.
"""

import hashlib
import re

from openlaw.retrieval.context import ChunkKind, RetrievedChunk

#: Every statute chunk opens with one header line naming the Act and where
#: that copy came from ("Act: The Parsi Marriage and Divorce Act, 1936 (Act 3
#: of 1936) | India | State | In Force"). It is exactly the part that differs
#: between central and state copies of the same section, so it is left out of
#: the dedupe key.
_ACT_HEADER_PREFIX = "Act:"
#: How an in-force copy's header line ends; a repealed copy says "Repealed".
_IN_FORCE_MARKER = "| in force"
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _section_body(text: str) -> str:
    """Return `text` without its leading "Act: ..." header line, if any."""
    if not text.startswith(_ACT_HEADER_PREFIX):
        return text
    _, _, body = text.partition("\n")
    return body


def provision_dedupe_key(chunk: RetrievedChunk) -> str:
    """Return the key two copies of the same statute section share.

    A hash of the section text after the "Act:" header line, lower-cased with
    every non-alphanumeric character removed.
    """
    # Body text, not act-title+section-number: titles differ between copies
    # ("..., 1936" vs "..., 1936."), and one long section is split into
    # several chunks (_p0, _p1, ...) that share a title and number but are
    # different text. Measured on the live acts index, this key collapses the
    # Parsi s.32 triple, the Portuguese CPC pair and the SEBI regulation
    # copies, and nothing else in the top 40 for the stuck-divorce question.
    normalised = _NON_ALPHANUMERIC.sub("", _section_body(chunk.text).lower())
    if not normalised:
        # A header-only chunk has no body to compare; every such chunk would
        # otherwise share sha256(""). It is only ever a duplicate of itself.
        return f"chunk_id:{chunk.chunk_id}"
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _is_in_force(chunk: RetrievedChunk) -> bool:
    """True when the chunk's "Act: ..." header line marks it In Force."""
    header, _, _ = chunk.text.partition("\n")
    return header.startswith(_ACT_HEADER_PREFIX) and _IN_FORCE_MARKER in header.lower()


def drop_duplicate_provisions(
    chunks: tuple[RetrievedChunk, ...],
) -> tuple[RetrievedChunk, ...]:
    """Return `chunks` with all but one copy of each statute section removed.

    The copy kept is the highest-ranked IN FORCE one, or the highest-ranked
    copy when none is in force; it stays at its own rank. Order is otherwise
    kept. Judgments pass through untouched.
    """
    winners: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        if chunk.kind != ChunkKind.PROVISION:
            continue
        key = provision_dedupe_key(chunk)
        current = winners.get(key)
        if current is None or (_is_in_force(chunk) and not _is_in_force(current)):
            winners[key] = chunk
    kept_ids = {chunk.chunk_id for chunk in winners.values()}
    return tuple(
        chunk
        for chunk in chunks
        if chunk.kind != ChunkKind.PROVISION or chunk.chunk_id in kept_ids
    )


def _total_chars(chunks: tuple[RetrievedChunk, ...]) -> int:
    return sum(len(chunk.text) for chunk in chunks)


def _trim_to_budget(
    statutes: tuple[RetrievedChunk, ...],
    judgments: tuple[RetrievedChunk, ...],
    statute_floor: int,
    max_chars: int,
) -> tuple[tuple[RetrievedChunk, ...], tuple[RetrievedChunk, ...]]:
    """Drop judgments, then statutes above the floor, until both fit
    `max_chars`. Lowest-ranked first in each case."""
    while judgments and _total_chars(statutes + judgments) > max_chars:
        judgments = judgments[:-1]
    while len(statutes) > statute_floor and _total_chars(statutes) > max_chars:
        statutes = statutes[:-1]
    return statutes, judgments


def select_pool(
    ranked: tuple[RetrievedChunk, ...],
    limit: int,
    statute_floor: int,
    max_chars: int | None = None,
) -> tuple[RetrievedChunk, ...]:
    """Return the final pool from best-first `ranked` chunks.

    See the module docstring for the three steps. `ranked` must already be
    in fused rank order; the result is a subsequence of it.
    """
    deduped = drop_duplicate_provisions(ranked)
    all_statutes = tuple(c for c in deduped if c.kind == ChunkKind.PROVISION)
    all_judgments = tuple(c for c in deduped if c.kind == ChunkKind.JUDGMENT)

    statute_slots = min(statute_floor, len(all_statutes), limit)
    judgment_slots = min(limit - statute_slots, len(all_judgments))
    # Judgments ran short: hand their unused slots back to statutes.
    statute_slots = min(limit - judgment_slots, len(all_statutes))

    statutes = all_statutes[:statute_slots]
    judgments = all_judgments[:judgment_slots]
    if max_chars is not None:
        statutes, judgments = _trim_to_budget(
            statutes, judgments, min(statute_floor, len(statutes)), max_chars
        )

    chosen = {chunk.chunk_id for chunk in statutes + judgments}
    return tuple(chunk for chunk in deduped if chunk.chunk_id in chosen)


def fit_around_reserved(
    fresh: tuple[RetrievedChunk, ...],
    reserved: tuple[RetrievedChunk, ...],
    max_chunks: int,
    max_chars: int,
    statute_floor: int,
) -> tuple[RetrievedChunk, ...]:
    """Return `fresh` re-fitted around `reserved`, plus `reserved` itself.

    `reserved` (carried and expanded chunks) is always kept whole. Its count
    and characters come off `max_chunks`/`max_chars` first, and `fresh` is
    re-selected with `select_pool` into the room left, so judgments go
    before statutes and the statute floor is kept. A reserved chunk fresh
    search also returned keeps its FRESH copy, in place (fresh retrieval
    wins a collision, as `openlaw.retrieval.rerank.merge_chunks` does).
    """
    fresh_ids = {chunk.chunk_id for chunk in fresh}
    reserved_ids = {chunk.chunk_id for chunk in reserved}
    protected = tuple(chunk for chunk in fresh if chunk.chunk_id in reserved_ids)
    reserved_only = tuple(chunk for chunk in reserved if chunk.chunk_id not in fresh_ids)
    taken = protected + reserved_only

    refitted = select_pool(
        tuple(chunk for chunk in fresh if chunk.chunk_id not in reserved_ids),
        limit=max(0, max_chunks - len(taken)),
        statute_floor=statute_floor,
        max_chars=max(0, max_chars - _total_chars(taken)),
    )
    kept_ids = reserved_ids | {chunk.chunk_id for chunk in refitted}
    return tuple(chunk for chunk in fresh if chunk.chunk_id in kept_ids) + reserved_only
