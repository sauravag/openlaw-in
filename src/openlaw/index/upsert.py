"""Construction and writing of Qdrant points for Supreme Court chunks.

Point IDs are a deterministic UUID5 of `chunk_id`, so re-running an ingest updates
points rather than duplicating them.
"""

import uuid
from typing import Any

import httpx

from openlaw.corpus.schema import JudgmentChunk

SC_COLLECTION = "sc_judgments_v1"
_ID_NAMESPACE = uuid.UUID("6f1c9d2e-0a3b-4c5d-8e7f-1a2b3c4d5e6f")


def point_id(chunk_id: str) -> str:
    """Return a deterministic UUID for `chunk_id`."""
    return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def build_point(
    chunk: JudgmentChunk,
    vector: list[float],
    source_url: str,
    headnote: str | None,
) -> dict[str, Any]:
    """Return the Qdrant point for `chunk`.

    `source_url` is required by the caller's contract — see
    `openlaw.corpus.sources.require_url`.
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
        "vector": {"dense": vector},
        "payload": payload,
    }


def upsert_points(
    points: list[dict[str, Any]], qdrant_url: str, client: httpx.Client
) -> None:
    """Write `points` into the Supreme Court collection.

    Sent with `wait=true` so the call does not return until Qdrant has actually
    applied the upsert — otherwise Qdrant defaults to `wait=false`, and a
    driver's own "done" progress line (or a validation run started right after)
    could observe the collection before this write lands.
    """
    client.put(
        f"{qdrant_url}/collections/{SC_COLLECTION}/points",
        params={"wait": "true"},
        json={"points": points},
        timeout=120,
    ).raise_for_status()
