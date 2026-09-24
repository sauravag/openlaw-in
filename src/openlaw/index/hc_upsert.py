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
    `openlaw.index.qdrant_restore.ensure_collection`.
    """
    response = client.put(
        f"{qdrant_url}/collections/{HC_COLLECTION}",
        json=build_hc_collection_payload(),
    )
    if response.status_code != COLLECTION_ALREADY_EXISTS:
        response.raise_for_status()


def delete_hc_collection(qdrant_url: str, client: httpx.Client) -> None:
    """Delete the shared HC collection and every point in it, tolerating 404
    when it does not already exist.

    Used only by `scripts/build_hc_index.py --restart`: `hc_judgments_v1` is
    our own derived collection -- unlike `acts_india`, `acts_india_v1`, and
    `sc_judgments_v1`, which this function never touches -- so discarding and
    recreating it is safe. A rebuild that instead skipped ahead via a merely
    cleared checkpoint would leave points embedded with a stale model mixed
    in with the new ones, silently unfindable at query time (a nearest-
    neighbour search still returns *something*, just not the stale point,
    since it sits in the wrong region of vector space). NOTE: since every
    registered High Court shares this one collection (see
    `openlaw.corpus.high_courts`), a --restart for one court currently
    discards every other court's points too -- fine while only Punjab &
    Haryana is registered, but worth revisiting once a second court is added.
    """
    response = client.delete(f"{qdrant_url}/collections/{HC_COLLECTION}")
    if response.status_code != COLLECTION_NOT_FOUND:
        response.raise_for_status()


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
    which this mirrors.
    """
    client.put(
        f"{qdrant_url}/collections/{HC_COLLECTION}/points",
        params={"wait": "true"},
        json={"points": points},
        timeout=120,
    ).raise_for_status()
