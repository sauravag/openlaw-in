"""Restore published Qdrant collection snapshots.

Snapshots are per-shard, so the collection must be created with a matching
`shard_number` before each shard is recovered. Qdrant fetches each snapshot itself and
verifies the published SHA256 before accepting it.

`acts_india` holds 1,098,269 legislation and regulator provisions across 2 shards
(11,207,628,288 bytes). Its geometry — voyage-4, 1024 dimensions, cosine — is the
vector space every other collection must match.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from openlaw.index.embed import EMBEDDING_DIMENSIONS

MIRROR_ROOT = "https://oss-data-in.vaquill.ai/qdrant"
# Same 1024 dimensions as `EMBEDDING_DIMENSIONS` — imported, not duplicated, so the
# two constants cannot drift apart.
VECTOR_SIZE = EMBEDDING_DIMENSIONS
DISTANCE = "Cosine"
COLLECTION_ALREADY_EXISTS = 409


@dataclass(frozen=True)
class CollectionSpec:
    """A published collection and where its shard snapshots live."""

    name: str
    shards: int
    mirror: str


ACTS_INDIA = CollectionSpec(
    name="acts_india", shards=2, mirror=f"{MIRROR_ROOT}/acts_india"
)


def build_collection_payload(spec: CollectionSpec) -> dict[str, Any]:
    """Return the Qdrant create-collection body for `spec`."""
    return {
        "shard_number": spec.shards,
        "vectors": {
            "dense": {
                "size": VECTOR_SIZE,
                "distance": DISTANCE,
                "on_disk": True,
                "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99}},
            }
        },
        "sparse_vectors": {"sparse": {}},
    }


def shard_snapshot_urls(
    spec: CollectionSpec, shard: int, snapshot: str
) -> tuple[str, str]:
    """Return the `(snapshot_url, checksum_url)` pair for one shard."""
    base = f"{spec.mirror}/shard-{shard}/{snapshot}"
    return base, f"{base}.checksum"


def ensure_collection(spec: CollectionSpec, qdrant_url: str, client: httpx.Client) -> None:
    """Create `spec`'s collection, tolerating 409 when it already exists.

    Idempotent and safe to re-run after an interrupted run: if the collection
    already exists (Qdrant returns 409), that is treated as success rather than
    an error. Any other error status is raised.
    """
    response = client.put(
        f"{qdrant_url}/collections/{spec.name}",
        json=build_collection_payload(spec),
    )
    if response.status_code != COLLECTION_ALREADY_EXISTS:
        response.raise_for_status()


def restore_collection(
    spec: CollectionSpec, qdrant_url: str, client: httpx.Client
) -> None:
    """Create `spec`'s collection and recover every shard snapshot into it.

    Idempotent and safe to re-run after an interrupted download: if the collection
    already exists (Qdrant returns 409), that is treated as success rather than an
    error, and shard recovery proceeds against the existing collection. Shard
    recovery replaces shard contents, so recovering into an existing (even
    partially populated) collection is the documented Qdrant flow and never
    deletes or recreates the collection.
    """
    ensure_collection(spec, qdrant_url, client)

    for shard in range(spec.shards):
        index = client.get(f"{spec.mirror}/shard-{shard}/index.json").json()
        snapshot = index["snapshot"]
        snapshot_url, checksum_url = shard_snapshot_urls(spec, shard, snapshot)
        checksum = client.get(checksum_url).text.strip()
        client.put(
            f"{qdrant_url}/collections/{spec.name}/shards/{shard}/snapshots/recover",
            json={
                "location": snapshot_url,
                "checksum": checksum,
                "priority": "snapshot",
            },
            timeout=None,
        ).raise_for_status()
