"""Re-fetching previously-cited chunks by id, for carried conversation history.

Task 8 (`openlaw.answers.history`) made `carried_chunk_ids` available: the
corpus-namespace chunk ids an earlier turn's surviving claims depend on. That
function is deliberately inert on its own -- the design it implements
(explicitly chosen over the obvious alternative of teaching `validate_answer`
a "cited in an earlier turn" branch) is that a carried id becomes citable
again only by being shown to the model again, THIS turn, like any other
retrieved chunk. `fetch_chunks_by_id` is the half of that design that makes it
true: it turns an id into the same `RetrievedChunk` `search()` would have
produced, by reading the one Qdrant point that id resolves to, rather than by
re-running a similarity search (which could legitimately return a different
chunk, or none, for the same query) or by trusting a cached copy of what the
chunk looked like when it was first retrieved (which could by now be stale --
see "payload can change" below).

**Deterministic by id, not by search.** `openlaw.index.upsert.point_id` maps
a `chunk_id` to a stable Qdrant point id (a UUID5 derived from the string
itself), so retrieving by that id is the exact inverse of how the point was
written: the same `chunk_id` always names the same point, and a direct
points-retrieve (`POST /collections/{collection}/points`, NOT the
`points/query` endpoint `openlaw.retrieval.search` uses) returns it unchanged
by relevance ranking, fusion, or any other search-time behaviour. Reusing
`chunk_from_point` (the exact function `search()` already builds
`RetrievedChunk`s with) means a carried chunk is constructed on an identical
path to a freshly-retrieved one -- there is no second, divergent
construction for the validator (or a reader of this code) to reconcile.

**A chunk that no longer resolves drops silently and safely -- exactly like
an invented id.** Corpora get rebuilt (see `openlaw.index.sparse`'s
`acts_india` -> `acts_india_v1` rebuild for a real example): an id a claim
cited last week may legitimately not exist in today's collection. Qdrant's
points-retrieve endpoint already behaves this way at the wire level --
missing ids are simply absent from `result`, no error -- so this module adds
no special-casing on top: an absent id produces no `RetrievedChunk`, the
calling turn's assembly (`openlaw.answers.generate.build_retrieval_context`)
never adds it to `RetrievalContext.chunk_ids`, and
`openlaw.answers.history.build_history_messages` then treats any claim that
cited it exactly as `validate_answer` treats a claim citing an id that was
never retrieved at all: dropped, logged as a `HallucinationEvent`. No new
branch, anywhere.

**"Payload changed" is not a special case either.** Because this module
always re-reads the live point rather than replaying a cached `RetrievedChunk`
from the earlier turn, a chunk whose text or `source_url` has since been
re-indexed is shown to the model in its CURRENT form -- the only form that
could ever be correct to cite from this turn onward. There is nothing to
detect or reconcile: the old copy was never kept.

**A re-fetch failure must never raise, and must never fail the turn.**
Unlike `openlaw.retrieval.search.search` (whose failure already means there
is no context for the new question at all, so letting it propagate is
correct), a failure here is strictly less severe: the new question can still
be answered from fresh retrieval, only the carried history would go quietly
empty. "Quietly" is exactly the risk Task 8's docstring flagged: an empty
carry looks identical, from the inside, to a perfectly ordinary set of
chunks that simply aged out of the corpus. `_retrieve_from_collection` below
is the one place that distinction is made observable: a transport or HTTP
failure is caught, logged at `ERROR` (loud enough for an operator watching
ordinary logs to notice, the same bar `openlaw.answers.crisis_classifier`
sets for a classifier outage), and treated as "this collection returned
nothing" for that call -- never raised, never silently identical to an
ordinary miss in the one place a human could tell them apart. A plain
"id not found" is NOT logged: that is the expected, routine shape of a
corpus that gets rebuilt, and logging it would bury the real signal (a
transport failure) in noise on every ordinary conversation.

**No write of any kind.** Every request below is a `POST` to the points
retrieve endpoint with no mutation -- `with_vector=False` to avoid pulling
back embeddings nothing here uses. This module never calls `.put` or
`.delete` on its client, and never touches the retired `acts_india`
collection (`openlaw.retrieval.search.ACTS_INDIA_V1` is the only acts
collection it knows about, via `SEARCH_COLLECTIONS`).

**One batched retrieve per collection, not one per id.** `chunk_ids` can
legitimately span multiple collections (a carried statute citation and a
carried judgment citation, say); which collection a given id lives in is not
known in advance, so each of `SEARCH_COLLECTIONS` -- the same set
`search()` queries -- gets exactly one request carrying every requested
point id, and whichever ids that collection actually holds come back.
"""

from __future__ import annotations

import logging
from typing import Any

from openlaw.index.upsert import point_id
from openlaw.retrieval.context import RetrievedChunk
from openlaw.retrieval.search import SEARCH_COLLECTIONS, chunk_from_point

logger = logging.getLogger(__name__)


def _retrieve_from_collection(
    client: Any, qdrant_url: str, collection: str, point_ids: list[str]
) -> list[dict[str, Any]]:
    """Return the points `point_ids` resolve to in `collection`.

    Returns `[]`, logged at `ERROR`, on any failure of the call itself (a
    transport error, a non-2xx response, or a malformed body) -- see the
    module docstring for why this is the one place "re-fetch failed" is
    distinguished from "the id just does not exist any more", and why that
    distinction must never raise into the caller.
    """
    try:
        response = client.post(
            f"{qdrant_url}/collections/{collection}/points",
            json={"ids": point_ids, "with_payload": True, "with_vector": False},
            timeout=30,
        )
        response.raise_for_status()
        return list(response.json()["result"])
    except Exception as error:  # noqa: BLE001 -- see module docstring: must never raise.
        logger.error(
            "[carried-chunks] re-fetch failed for collection %s (%d id(s) "
            "requested): %s: %s",
            collection,
            len(point_ids),
            type(error).__name__,
            error,
        )
        return []


def fetch_chunks_by_id(
    client: Any, qdrant_url: str, chunk_ids: tuple[str, ...]
) -> tuple[RetrievedChunk, ...]:
    """Read-only retrieve of specific points by deterministic point id
    (`openlaw.index.upsert.point_id`) across the same collections `search`
    uses. Missing ids are simply absent from the result -- a chunk that no
    longer exists in the corpus stops being citable, which is correct.

    An id that fails to resolve for ANY reason -- not found, or the retrieve
    call itself failing -- is absent from the result and this function never
    raises; see the module docstring for how the two cases are still made
    distinguishable to an operator. Results are built with the same
    `chunk_from_point` `search()` uses, so a carried chunk is
    indistinguishable in shape from a freshly-retrieved one, and a chunk
    whose payload changed since it was first cited comes back in its
    current, not its original, form.

    Returned in the order `chunk_ids` was given, deduplicated. Callers merge
    this result into a turn's fresh retrieval with `openlaw.retrieval.rerank.
    merge_chunks`, which dedupes by `chunk_id` with the fresh side winning --
    so a carried id that also came back from this turn's own search is not
    duplicated.
    """
    if not chunk_ids:
        return ()

    point_ids = [point_id(chunk_id) for chunk_id in chunk_ids]
    resolved: dict[str, RetrievedChunk] = {}
    for collection in SEARCH_COLLECTIONS:
        points = _retrieve_from_collection(client, qdrant_url, collection, point_ids)
        for point in points:
            chunk = chunk_from_point(point)
            if chunk is not None and chunk.chunk_id not in resolved:
                resolved[chunk.chunk_id] = chunk

    return tuple(resolved[chunk_id] for chunk_id in chunk_ids if chunk_id in resolved)
