"""Rebuild sparse vectors for the v1 corpus under one consistent BM25 scheme.

Why this exists: the published sparse vectors mix two incompatible weighting
schemes (51.7% BM25-saturated, 47.6% raw term counts), and Supreme Court points
were written dense-only. See spec section 8.2.

Two targets, treated differently because one is ours and one is not:

- `sc_judgments_v1` is ours and cheap to rebuild, so sparse vectors are written
  into it in place. Dense vectors are never sent, so they cannot be disturbed.
- `acts_india` holds the only local copy of the publisher's dense vectors and is
  expensive to restore, so it is **never written to**. Its citable points are
  copied into a new collection instead.

The copy also applies the exclusion the retrieval plans require: provisions with
no government source URL (137,441 regulator instruments, 12.5%) can never be
cited, so they are left out of the retrieval collection entirely rather than
relying on every future query remembering to filter them.

Resumable: progress is checkpointed after every batch, and re-running continues
from the last scroll offset.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openlaw.config import get_settings  # noqa: E402
from openlaw.index.qdrant_restore import CollectionSpec, build_collection_payload  # noqa: E402
from openlaw.index.sparse import (  # noqa: E402
    SPARSE_VECTOR_NAME,
    average_token_count,
    bm25_vector,
)
from openlaw.index.upsert import SC_COLLECTION  # noqa: E402
from openlaw.index.validation import has_source_url  # noqa: E402

ACTS_SOURCE = "acts_india"
ACTS_TARGET = "acts_india_v1"

CHECKPOINT_PATH = Path("data/sparse_checkpoint.json")
SCROLL_BATCH = 256
SAMPLE_SIZE = 20_000
PROGRESS_EVERY = 25_000
REQUEST_TIMEOUT = 180
COLLECTION_ALREADY_EXISTS = 409


def scroll(
    client: httpx.Client,
    qdrant_url: str,
    collection: str,
    offset: Any,
    with_vectors: bool,
) -> tuple[list[dict[str, Any]], Any]:
    """Return one page of points and the offset of the next page."""
    body: dict[str, Any] = {
        "limit": SCROLL_BATCH,
        "with_payload": True,
        "with_vector": ["dense"] if with_vectors else False,
    }
    if offset is not None:
        body["offset"] = offset
    response = client.post(
        f"{qdrant_url}/collections/{collection}/points/scroll", json=body
    )
    response.raise_for_status()
    result = response.json()["result"]
    return result["points"], result.get("next_page_offset")


def measure_average_length(
    client: httpx.Client, qdrant_url: str, collections: list[str]
) -> float:
    """Return the mean token count across a random sample of `collections`.

    BM25's length normalisation needs a corpus average. A random sample is used
    rather than a full pass: the statistic is a denominator, so small sampling
    error is immaterial, and a full pass would double the wall-clock cost of the
    rebuild. One shared average keeps statute and judgment scores comparable.
    """
    texts: list[str] = []
    per_collection = max(SAMPLE_SIZE // max(len(collections), 1), 1)
    for collection in collections:
        response = client.post(
            f"{qdrant_url}/collections/{collection}/points/query",
            json={
                "query": {"sample": "random"},
                "limit": per_collection,
                "with_payload": ["text"],
                "with_vector": False,
            },
        )
        response.raise_for_status()
        for point in response.json()["result"]["points"]:
            text = (point.get("payload") or {}).get("text")
            if text:
                texts.append(text)
    return average_token_count(texts)


def set_idf_modifier(client: httpx.Client, qdrant_url: str, collection: str) -> None:
    """Have Qdrant apply inverse document frequency at query time.

    Stored values carry only BM25's term-frequency part; Qdrant supplies IDF from
    real collection statistics. Verified to work on an existing collection.
    """
    response = client.patch(
        f"{qdrant_url}/collections/{collection}",
        json={"sparse_vectors": {SPARSE_VECTOR_NAME: {"modifier": "idf"}}},
    )
    response.raise_for_status()


def read_checkpoint(collection: str) -> dict[str, Any] | None:
    """Return the checkpoint for `collection`, if one matches."""
    if not CHECKPOINT_PATH.exists():
        return None
    checkpoint = json.loads(CHECKPOINT_PATH.read_text())
    return checkpoint if checkpoint.get("collection") == collection else None


def write_checkpoint(collection: str, offset: Any, written: int, version: str) -> None:
    """Record progress atomically, so a crash cannot leave a torn file."""
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "collection": collection,
                "offset": offset,
                "written": written,
                "corpus_version": version,
            }
        )
    )
    temporary.replace(CHECKPOINT_PATH)


def rebuild_in_place(
    client: httpx.Client, qdrant_url: str, collection: str, avg_len: float, version: str
) -> int:
    """Write sparse vectors onto `collection`'s existing points.

    Only the named sparse vector is sent, so dense vectors and payloads are
    untouched.
    """
    checkpoint = read_checkpoint(collection)
    offset = checkpoint["offset"] if checkpoint else None
    written = checkpoint["written"] if checkpoint else 0
    if checkpoint:
        print(f"[Sparse] {collection}: resuming after {written:,} points")

    while True:
        points, next_offset = scroll(client, qdrant_url, collection, offset, False)
        if not points:
            break

        updates = [
            {
                "id": point["id"],
                "vector": {
                    SPARSE_VECTOR_NAME: bm25_vector(
                        (point.get("payload") or {}).get("text") or "", avg_len
                    )
                },
            }
            for point in points
        ]
        response = client.put(
            f"{qdrant_url}/collections/{collection}/points/vectors?wait=true",
            json={"points": updates},
        )
        response.raise_for_status()

        written += len(updates)
        offset = next_offset
        write_checkpoint(collection, offset, written, version)
        if written % PROGRESS_EVERY < SCROLL_BATCH:
            print(f"[Sparse] {collection}: {written:,} points")
        if next_offset is None:
            break

    return written


def rebuild_into_copy(
    client: httpx.Client, qdrant_url: str, avg_len: float, version: str
) -> tuple[int, int]:
    """Copy citable `acts_india` points into `acts_india_v1` with sparse vectors.

    Returns `(copied, skipped)`. The source collection is only ever read.
    """
    spec = CollectionSpec(name=ACTS_TARGET, shards=2, mirror="")
    payload = build_collection_payload(spec)
    payload["sparse_vectors"] = {SPARSE_VECTOR_NAME: {"modifier": "idf"}}
    created = client.put(f"{qdrant_url}/collections/{ACTS_TARGET}", json=payload)
    if created.status_code != COLLECTION_ALREADY_EXISTS:
        created.raise_for_status()

    checkpoint = read_checkpoint(ACTS_TARGET)
    offset = checkpoint["offset"] if checkpoint else None
    copied = checkpoint["written"] if checkpoint else 0
    skipped = 0
    if checkpoint:
        print(f"[Sparse] {ACTS_TARGET}: resuming after {copied:,} points")

    while True:
        points, next_offset = scroll(client, qdrant_url, ACTS_SOURCE, offset, True)
        if not points:
            break

        batch = []
        for point in points:
            point_payload = point.get("payload") or {}
            if not has_source_url(point_payload):
                skipped += 1
                continue
            text = point_payload.get("text") or ""
            batch.append(
                {
                    "id": point["id"],
                    "vector": {
                        "dense": point["vector"]["dense"],
                        SPARSE_VECTOR_NAME: bm25_vector(text, avg_len),
                    },
                    "payload": {**point_payload, "corpus_version": version},
                }
            )

        if batch:
            response = client.put(
                f"{qdrant_url}/collections/{ACTS_TARGET}/points?wait=true",
                json={"points": batch},
            )
            response.raise_for_status()
            copied += len(batch)

        offset = next_offset
        write_checkpoint(ACTS_TARGET, offset, copied, version)
        if copied % PROGRESS_EVERY < SCROLL_BATCH:
            print(f"[Sparse] {ACTS_TARGET}: {copied:,} copied, {skipped:,} un-citable")
        if next_offset is None:
            break

    return copied, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("sc", "acts", "both"),
        default="both",
        help="which collection to rebuild (default: both)",
    )
    parser.add_argument(
        "--restart", action="store_true", help="ignore and clear any checkpoint"
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.restart and CHECKPOINT_PATH.exists():
        print("[Sparse] --restart: clearing checkpoint")
        CHECKPOINT_PATH.unlink()

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        avg_len = measure_average_length(
            client, settings.qdrant_url, [ACTS_SOURCE, SC_COLLECTION]
        )
        print(f"[Sparse] corpus average length: {avg_len:.1f} tokens")

        if args.target in ("sc", "both"):
            set_idf_modifier(client, settings.qdrant_url, SC_COLLECTION)
            written = rebuild_in_place(
                client,
                settings.qdrant_url,
                SC_COLLECTION,
                avg_len,
                settings.corpus_version,
            )
            print(f"[Sparse] {SC_COLLECTION}: done, {written:,} points")

        if args.target in ("acts", "both"):
            copied, skipped = rebuild_into_copy(
                client, settings.qdrant_url, avg_len, settings.corpus_version
            )
            print(
                f"[Sparse] {ACTS_TARGET}: done, {copied:,} copied, "
                f"{skipped:,} un-citable provisions excluded"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
