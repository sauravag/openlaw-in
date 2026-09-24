"""Construction and writing of Qdrant points for High Court judgment chunks.

Shares ONE collection across every registered High Court (see
`openlaw.corpus.high_courts`), filtered at query time by the `court_code`
payload field -- a Punjab & Haryana judgment must never satisfy a query scoped
to another High Court's jurisdiction, because High Court judgments bind only
within their own state(s), unlike Supreme Court judgments. Point IDs reuse
`sc_judgments_v1`'s deterministic UUID5-of-`chunk_id` scheme
(`openlaw.index.upsert.point_id`), so a resumed run updates points rather than
duplicating them.

Unlike `sc_judgments_v1` (which had its sparse vectors retrofitted after the
fact by `scripts/build_sparse_vectors.py`), this collection writes sparse
(BM25 term-frequency) vectors alongside dense vectors from the very first
point -- see `openlaw.index.sparse` for why only the term-frequency component
is stored, with Qdrant applying inverse document frequency at query time via
the `modifier: "idf"` declared on this collection at creation.
"""

from typing import Any

import httpx

from openlaw.corpus.schema import JudgmentChunk
from openlaw.index.qdrant_http import call_with_retry
from openlaw.index.qdrant_restore import (
    COLLECTION_ALREADY_EXISTS,
    CollectionSpec,
    build_collection_payload,
)
from openlaw.index.sparse import SPARSE_VECTOR_NAME
from openlaw.index.upsert import point_id

HC_COLLECTION = "hc_judgments_v1"
COLLECTION_NOT_FOUND = 404


def build_hc_collection_payload(shards: int = 1) -> dict[str, Any]:
    """Return the Qdrant create-collection body for the shared HC collection.

    Same dense geometry as `acts_india`/`sc_judgments_v1` (1024-dim, cosine),
    but the sparse vector's `idf` modifier is declared upfront rather than
    patched on after the fact.
    """
    spec = CollectionSpec(name=HC_COLLECTION, shards=shards, mirror="")
    payload = build_collection_payload(spec)
    payload["sparse_vectors"] = {SPARSE_VECTOR_NAME: {"modifier": "idf"}}
    return payload


def ensure_hc_collection(qdrant_url: str, client: httpx.Client) -> None:
    """Create the shared HC collection, tolerating 409 when it already exists.

    Idempotent and safe to re-run after an interrupted build, mirroring
    `openlaw.index.qdrant_restore.ensure_collection`. Retries transient
    connection failures (see `openlaw.index.qdrant_http`) since this is
    also the first Qdrant call `scripts/build_hc_index.py` makes each run.
    """

    def _create() -> httpx.Response:
        response = client.put(
            f"{qdrant_url}/collections/{HC_COLLECTION}",
            json=build_hc_collection_payload(),
        )
        if response.status_code != COLLECTION_ALREADY_EXISTS:
            response.raise_for_status()
        return response

    call_with_retry(_create, description=f"PUT collections/{HC_COLLECTION}")


def delete_hc_court_points(court_code: str, qdrant_url: str, client: httpx.Client) -> None:
    """Delete every point for `court_code` from the shared HC collection,
    tolerating 404 when the collection does not already exist.

    Used only by `scripts/build_hc_index.py --restart`. `hc_judgments_v1` is
    shared by every registered High Court (`openlaw.corpus.high_courts`),
    distinguished by the `court_code` payload field, so a restart must clear
    ONLY the points belonging to the court being rebuilt -- never the whole
    collection. `court_code` here is the vaquill spelling stored on every
    point's payload (`build_hc_point`), e.g. `"3_22"` for Punjab & Haryana or
    `"7_26"` for Delhi. A rebuild that instead skipped ahead via a merely
    cleared checkpoint would leave points embedded with a stale model mixed
    in with the new ones, silently unfindable at query time (a nearest-
    neighbour search still returns *something*, just not the stale point,
    since it sits in the wrong region of vector space); scoping the delete to
    `court_code` prevents that same hazard from also destroying every OTHER
    court's already-indexed points, which a whole-collection delete would.
    Retries transient connection failures (see `openlaw.index.qdrant_http`).
    """

    def _delete() -> httpx.Response:
        response = client.post(
            f"{qdrant_url}/collections/{HC_COLLECTION}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": [{"key": "court_code", "match": {"value": court_code}}]}},
            timeout=120,
        )
        if response.status_code != COLLECTION_NOT_FOUND:
            response.raise_for_status()
        return response

    call_with_retry(_delete, description=f"POST collections/{HC_COLLECTION}/points/delete")


def build_hc_point(
    chunk: JudgmentChunk,
    dense_vector: list[float],
    sparse_vector: dict[str, list],
    source_url: str,
    headnote: str | None,
) -> dict[str, Any]:
    """Return the Qdrant point for one High Court chunk.

    Carries `court_code` in the payload (e.g. `"3_22"` for Punjab & Haryana)
    so retrieval can filter to a single jurisdiction -- see
    `openlaw.retrieval.search`'s `court_codes` filter.
    """
    payload: dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "case_id": chunk.case_id,
        "join_key": chunk.join_key,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "text_original": chunk.text_original,
        "court_code": chunk.court_code,
        "judges": list(chunk.judges),
        "decision_date": (
            chunk.decision_date.isoformat() if chunk.decision_date else None
        ),
        "language_code": chunk.language_code,
        "corpus_version": chunk.corpus_version,
        "source_url": source_url,
        "headnote": headnote,
    }
    return {
        "id": point_id(chunk.chunk_id),
        "vector": {"dense": dense_vector, SPARSE_VECTOR_NAME: sparse_vector},
        "payload": payload,
    }


def upsert_hc_points(
    points: list[dict[str, Any]], qdrant_url: str, client: httpx.Client
) -> None:
    """Write `points` into the shared High Court collection.

    Sent with `wait=true` so the call does not return until Qdrant has
    actually applied the upsert -- see `openlaw.index.upsert.upsert_points`,
    which this mirrors. Retries transient connection failures (see
    `openlaw.index.qdrant_http`) rather than letting an idle-connection
    drop during this multi-hour build kill the whole run: point IDs are
    deterministic (`openlaw.index.upsert.point_id`), so a retried upsert
    writes the same points again rather than duplicating them.
    """

    def _upsert() -> httpx.Response:
        response = client.put(
            f"{qdrant_url}/collections/{HC_COLLECTION}/points",
            params={"wait": "true"},
            json={"points": points},
            timeout=120,
        )
        response.raise_for_status()
        return response

    call_with_retry(
        _upsert,
        description=f"PUT collections/{HC_COLLECTION}/points ({len(points)} points)",
    )
