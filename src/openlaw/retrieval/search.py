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
"""

from typing import Any, Protocol

from pydantic import ValidationError

from openlaw.index.sparse import token_index, tokenise
from openlaw.index.upsert import SC_COLLECTION
from openlaw.retrieval.context import ChunkKind, RetrievedChunk

ACTS_INDIA_V1 = "acts_india_v1"
SEARCH_COLLECTIONS: tuple[str, ...] = (ACTS_INDIA_V1, SC_COLLECTION)
PREFETCH_MULTIPLIER = 4
SPARSE_QUERY_WEIGHT = 1.0


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
    if court_codes:
        request["filter"] = {
            "must": [{"key": "court_code", "match": {"any": list(court_codes)}}]
        }
    return request


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


def search(
    client: _PostClient,
    qdrant_url: str,
    text: str,
    vector: list[float],
    limit: int = 10,
    court_codes: tuple[str, ...] | None = None,
) -> tuple[RetrievedChunk, ...]:
    """Search every corpus collection and return the merged, score-ordered hits.

    `text` is the raw query -- needed to build the sparse side, which is
    independent of `vector` (the dense embedding of the same query). Both are
    required: a dense-only caller cannot get hybrid search's lexical-match
    benefit for free.
    """
    sparse_vector = build_sparse_query_vector(text)
    request = build_hybrid_request(vector, sparse_vector, limit, court_codes)

    chunks: list[RetrievedChunk] = []
    for collection in SEARCH_COLLECTIONS:
        chunks.extend(_search_collection(client, qdrant_url, collection, request))

    chunks.sort(key=lambda chunk: chunk.score, reverse=True)
    return tuple(chunks[:limit])
