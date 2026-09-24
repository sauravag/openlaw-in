"""Hybrid dense + sparse search against Qdrant.

Searches `acts_india_v1` (961,136 citable provisions, one consistent BM25
scheme, `corpus_version` set) rather than the original `acts_india` --
`acts_india` mixes two incompatible sparse-vector weighting schemes, has no
`corpus_version`, and still holds un-citable provisions with no government
source URL. See `openlaw.index.sparse` for the rebuild rationale.

Both `acts_india_v1` and `sc_judgments_v1` declare their sparse vector with
Qdrant's `modifier: "idf"`: stored (document-side) values carry only BM25's
term-frequency component, and Qdrant applies inverse document frequency at
query time from real collection statistics. A QUERY sparse vector must
therefore NOT be built with `openlaw.index.sparse.bm25_vector` -- that is the
document-side function and would double up term-frequency weighting on top of
Qdrant's own IDF. Instead it is built here from `tokenise`/`token_index` with a
weight of 1.0 per distinct query term (`build_sparse_query_vector`).

The Qdrant wire shape below was verified against the live server (Qdrant
1.16.3): `using` is always a sibling of `query`, never nested inside it, and a
sparse `query` must be wrapped in `{"nearest": {...}}` -- a bare
`{"indices": ..., "values": ...}` returns HTTP 400 "Not existing vector name
error". Querying both dense and sparse and fusing with reciprocal rank fusion
recovers exact statutory references that pure dense retrieval misses -- a
citizen asking about "Section 138" needs the lexical match.

`search_dense_only` and `search_sparse_only` (with their matching
`build_*_request` functions) are added for the Task 9 evaluation harness
(`openlaw.evaluation.retrieval_eval`), which must measure whether the sparse
signal helps or hurts on real queries rather than assume it -- see that
module's docstring. Nothing on the production answer path calls them; the
request path always uses hybrid `search`.

`hc_judgments_v1` (High Court judgments, `openlaw.index.hc_upsert`) is a third
collection, searched ALONGSIDE the two above only when a caller explicitly
asks for a High Court jurisdiction via `court_codes`. It shares one collection
across every registered High Court (`openlaw.corpus.high_courts`) rather than
one collection per court, and carries `court_code` in its payload so a caller
can pass `court_codes` to `search()` to scope a query to a single
jurisdiction -- High Court judgments bind only within their own state(s),
unlike Supreme Court judgments. **This collection is excluded by default**
(no production caller passes `court_codes` today): an unscoped nationwide
query must never silently merge one High Court's persuasive-but-non-binding
authority in as if it applied everywhere. `_collections_to_search` is the
single gate this goes through -- see its docstring. Its vectors are embedded
locally with `voyage-4-nano`, the SAME model that embeds queries by default
(`openlaw.retrieval.local_nano.LocalNanoQueryEmbedder`) -- not
`voyage-4-lite`: measured, nano queries against voyage-4-lite HC documents
scored only 15.0% top-1 / 31.7% top-10 (median rank 61 of 300), because nano
and lite's independent approximation errors against full voyage-4 do not
cancel against each other.

**Merging across collections is by RANK, not raw score** (`_merge_by_rank`,
reciprocal-rank fusion). Raw Qdrant scores are not comparable across
collections -- different models and different content give different score
scales -- and that is strictly worse now that HC documents are nano-embedded
alongside voyage-4-embedded acts and Supreme Court judgments: nothing bounds
one collection's scores from structurally outrunning another's. Each
collection's own best-first order is instead fused via each hit's reciprocal
rank, so no collection can out-rank another purely by running a higher score
scale. The `score` on a returned `RetrievedChunk` is therefore the fused
rank-based score, not the raw per-collection Qdrant score.
"""

from typing import Any, Protocol

from pydantic import ValidationError

from openlaw.corpus.high_courts import HIGH_COURTS
from openlaw.index.hc_upsert import HC_COLLECTION
from openlaw.index.sparse import token_index, tokenise
from openlaw.index.upsert import SC_COLLECTION
from openlaw.retrieval.context import ChunkKind, RetrievedChunk

ACTS_INDIA_V1 = "acts_india_v1"
SEARCH_COLLECTIONS: tuple[str, ...] = (ACTS_INDIA_V1, SC_COLLECTION, HC_COLLECTION)
# Every registered High Court's jurisdiction code -- the set `court_codes`
# must intersect for `_collections_to_search` to include HC_COLLECTION.
HIGH_COURT_CODES: frozenset[str] = frozenset(
    court.vaquill_court_code for court in HIGH_COURTS.values()
)
PREFETCH_MULTIPLIER = 4
SPARSE_QUERY_WEIGHT = 1.0
# Standard reciprocal-rank-fusion constant (Qdrant, Elasticsearch, and the
# original Cormack et al. RRF paper all use 60) -- large enough that a hit's
# exact rank matters more than which collection it came from.
RRF_K = 60


class _PostResponse(Protocol):
    """The minimal shape `search` needs back from an HTTP POST."""

    def raise_for_status(self) -> None: ...
    def json(self) -> dict[str, Any]: ...


class _PostClient(Protocol):
    """The minimal shape `search` needs from an HTTP client (`httpx.Client`
    satisfies this; tests supply a lightweight fake)."""

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _PostResponse: ...


def build_sparse_query_vector(text: str) -> dict[str, list]:
    """Return the QUERY-side sparse vector for `text`.

    Every distinct token gets weight 1.0 -- Qdrant's `modifier: "idf"` supplies
    the inverse-document-frequency component from live collection statistics,
    so the query side carries no term-frequency information at all. Indices are
    sorted for a deterministic, easily-diffed request body.
    """
    tokens = set(tokenise(text))
    if not tokens:
        return {"indices": [], "values": []}
    indices = sorted(token_index(token) for token in tokens)
    return {"indices": indices, "values": [SPARSE_QUERY_WEIGHT] * len(indices)}


def build_hybrid_request(
    dense_vector: list[float],
    sparse_vector: dict[str, list],
    limit: int,
    court_codes: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Return the Qdrant query body fusing dense and sparse retrieval."""
    prefetch_limit = limit * PREFETCH_MULTIPLIER
    request: dict[str, Any] = {
        "prefetch": [
            {"query": dense_vector, "using": "dense", "limit": prefetch_limit},
            {
                "query": {"nearest": sparse_vector},
                "using": "sparse",
                "limit": prefetch_limit,
            },
        ],
        "query": {"fusion": "rrf"},
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    return _with_court_filter(request, court_codes)


def _with_court_filter(
    request: dict[str, Any], court_codes: tuple[str, ...] | None
) -> dict[str, Any]:
    """Return `request` with a `court_code` filter added when `court_codes` is
    non-empty. Shared by every single-vector request builder below."""
    if court_codes:
        request["filter"] = {
            "must": [{"key": "court_code", "match": {"any": list(court_codes)}}]
        }
    return request


def build_dense_only_request(
    dense_vector: list[float], limit: int, court_codes: tuple[str, ...] | None
) -> dict[str, Any]:
    """Return the Qdrant query body for dense-vector-only retrieval.

    No `prefetch`/fusion -- a single `query` against the `dense` vector name.
    Evaluation-only: measures dense retrieval in isolation (see the module
    docstring); the production request path never calls this.
    """
    request: dict[str, Any] = {
        "query": dense_vector,
        "using": "dense",
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    return _with_court_filter(request, court_codes)


def build_sparse_only_request(
    sparse_vector: dict[str, list], limit: int, court_codes: tuple[str, ...] | None
) -> dict[str, Any]:
    """Return the Qdrant query body for sparse-vector-only retrieval.

    The sparse vector must be wrapped in `{"nearest": {...}}`, exactly as in
    `build_hybrid_request`'s sparse prefetch -- a bare sparse vector at the
    `query` key returns HTTP 400. Evaluation-only, see the module docstring.
    """
    request: dict[str, Any] = {
        "query": {"nearest": sparse_vector},
        "using": "sparse",
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    return _with_court_filter(request, court_codes)


def chunk_from_point(point: dict[str, Any]) -> RetrievedChunk | None:
    """Map a Qdrant point to a `RetrievedChunk`, or None when it is unusable."""
    payload = point.get("payload") or {}
    kind = ChunkKind.JUDGMENT if payload.get("case_id") else ChunkKind.PROVISION
    try:
        return RetrievedChunk(
            chunk_id=str(payload.get("chunk_id") or ""),
            text=str(payload.get("text") or ""),
            source_url=str(payload.get("source_url") or ""),
            score=float(point.get("score") or 0.0),
            kind=kind,
            title=payload.get("title"),
            court_code=payload.get("court_code"),
            decision_date=payload.get("decision_date"),
            headnote=payload.get("headnote"),
        )
    except ValidationError:
        return None


def _search_collection(
    client: _PostClient,
    qdrant_url: str,
    collection: str,
    request: dict[str, Any],
) -> list[RetrievedChunk]:
    """Run `request` against one collection and return its usable chunks."""
    response = client.post(
        f"{qdrant_url}/collections/{collection}/points/query",
        json=request,
        timeout=30,
    )
    response.raise_for_status()
    points = response.json()["result"]["points"]
    chunks = (chunk_from_point(point) for point in points)
    return [chunk for chunk in chunks if chunk is not None]


def _collections_to_search(court_codes: tuple[str, ...] | None) -> tuple[str, ...]:
    """Return which corpus collections this request actually queries.

    `HC_COLLECTION` is excluded unless `court_codes` names at least one
    registered High Court jurisdiction -- an unscoped (or Supreme-Court-only)
    query must never silently pull in one High Court's persuasive-but-non-
    binding authority as though it bound the whole country. Passing
    `court_codes` that name only non-High-Court jurisdictions (e.g. the
    Supreme Court's own code) does not open this gate either -- the caller
    must actually be asking about a High Court.
    """
    if court_codes and HIGH_COURT_CODES.intersection(court_codes):
        return SEARCH_COLLECTIONS
    return tuple(collection for collection in SEARCH_COLLECTIONS if collection != HC_COLLECTION)


def _merge_by_rank(
    results_by_collection: list[list[RetrievedChunk]], limit: int
) -> tuple[RetrievedChunk, ...]:
    """Fuse each collection's own best-first hits by reciprocal rank.

    Raw scores are not comparable across collections -- see the module
    docstring. Each chunk's contribution is `1 / (RRF_K + rank)`, summed
    across every collection it appears in (rank 1-based, within that
    collection's own returned order); chunks are then ordered by that summed
    score, descending, and capped to `limit`. The chunk kept for a duplicate
    id is whichever collection returned it first (arbitrary but deterministic
    -- duplicates across these collections are not expected in practice). The
    returned `score` is the fused rank score, not any collection's raw score.
    """
    fused_score: dict[str, float] = {}
    first_seen: dict[str, RetrievedChunk] = {}
    for chunks in results_by_collection:
        for rank, chunk in enumerate(chunks, start=1):
            fused_score[chunk.chunk_id] = fused_score.get(chunk.chunk_id, 0.0) + 1.0 / (
                RRF_K + rank
            )
            first_seen.setdefault(chunk.chunk_id, chunk)

    ordered_ids = sorted(fused_score, key=lambda chunk_id: fused_score[chunk_id], reverse=True)
    return tuple(
        first_seen[chunk_id].model_copy(update={"score": fused_score[chunk_id]})
        for chunk_id in ordered_ids[:limit]
    )


def _run_across_collections(
    client: _PostClient,
    qdrant_url: str,
    request: dict[str, Any],
    limit: int,
    court_codes: tuple[str, ...] | None,
) -> tuple[RetrievedChunk, ...]:
    """Run `request` against every applicable corpus collection and return the
    rank-merged, limit-capped hits. Shared by `search`, `search_dense_only`,
    and `search_sparse_only` -- only the request body differs between them.
    """
    results_by_collection = [
        _search_collection(client, qdrant_url, collection, request)
        for collection in _collections_to_search(court_codes)
    ]
    return _merge_by_rank(results_by_collection, limit)


def search(
    client: _PostClient,
    qdrant_url: str,
    text: str,
    vector: list[float],
    limit: int = 10,
    court_codes: tuple[str, ...] | None = None,
) -> tuple[RetrievedChunk, ...]:
    """Search every applicable corpus collection and return the rank-merged
    hits (see the module docstring: `hc_judgments_v1` only joins when
    `court_codes` names a High Court jurisdiction, and the merge is by
    reciprocal rank, not raw score).

    `text` is the raw query -- needed to build the sparse side, which is
    independent of `vector` (the dense embedding of the same query). Both are
    required: a dense-only caller cannot get hybrid search's lexical-match
    benefit for free.
    """
    sparse_vector = build_sparse_query_vector(text)
    request = build_hybrid_request(vector, sparse_vector, limit, court_codes)
    return _run_across_collections(client, qdrant_url, request, limit, court_codes)


def search_dense_only(
    client: _PostClient,
    qdrant_url: str,
    vector: list[float],
    limit: int = 10,
    court_codes: tuple[str, ...] | None = None,
) -> tuple[RetrievedChunk, ...]:
    """Search using only the dense vector -- no sparse/lexical component.

    Evaluation-only (see the module docstring): isolates dense retrieval's own
    accuracy from hybrid fusion, so the evaluation harness can measure whether
    the sparse signal is helping or hurting rather than assume it.
    """
    request = build_dense_only_request(vector, limit, court_codes)
    return _run_across_collections(client, qdrant_url, request, limit, court_codes)


def search_sparse_only(
    client: _PostClient,
    qdrant_url: str,
    text: str,
    limit: int = 10,
    court_codes: tuple[str, ...] | None = None,
) -> tuple[RetrievedChunk, ...]:
    """Search using only the sparse (BM25-style) vector built from `text`.

    Evaluation-only (see the module docstring): isolates the lexical signal
    that hybrid search fuses in, so its real contribution -- positive or
    negative -- can be measured on real queries.
    """
    sparse_vector = build_sparse_query_vector(text)
    request = build_sparse_only_request(sparse_vector, limit, court_codes)
    return _run_across_collections(client, qdrant_url, request, limit, court_codes)
