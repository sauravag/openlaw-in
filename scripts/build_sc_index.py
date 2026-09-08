"""Embed and index English Supreme Court chunks.

Filters to English, deduplicates, resolves a government URL for every chunk via
the KanoonGPT join, embeds with voyage-4, and upserts into Qdrant. Chunks whose
citation cannot be resolved are counted and skipped, never indexed.

Run: python scripts/build_sc_index.py
"""

import sys

import httpx

from openlaw.config import get_settings
from openlaw.corpus.filters import deduplicate, english_only
from openlaw.corpus.join import build_metadata_index
from openlaw.corpus.sources import resolve_judgment_url
from openlaw.datasets.vaquill import read_judgment_chunks
from openlaw.index.embed import DEFAULT_BATCH_SIZE, VoyageEmbedder, batched
from openlaw.index.qdrant_restore import CollectionSpec, build_collection_payload
from openlaw.index.upsert import SC_COLLECTION, build_point, upsert_points

PROGRESS_EVERY = 10_000


def main() -> int:
    settings = get_settings()
    if not settings.voyage_api_key:
        print("[Index] FAIL: OPENLAW_VOYAGE_API_KEY is not set")
        return 1

    print("[Index] building KanoonGPT metadata index (17M records, be patient)")
    metadata_index = build_metadata_index(settings.kanoongpt_root)
    print(f"[Index] metadata index holds {len(metadata_index):,} cases")

    source = settings.vaquill_root / "in_supreme-court_judgments.parquet"
    chunks = deduplicate(
        english_only(read_judgment_chunks(source, corpus_version=settings.corpus_version))
    )

    embedder = VoyageEmbedder(settings.voyage_api_key)
    indexed = 0
    unresolved = 0

    with httpx.Client(timeout=120) as client:
        spec = CollectionSpec(name=SC_COLLECTION, shards=1, mirror="")
        client.put(
            f"{settings.qdrant_url}/collections/{SC_COLLECTION}",
            json=build_collection_payload(spec),
        )

        for batch in batched(chunks, DEFAULT_BATCH_SIZE):
            resolved = [
                (chunk, url)
                for chunk in batch
                if (url := resolve_judgment_url(chunk, metadata_index)) is not None
            ]
            unresolved += len(batch) - len(resolved)
            if not resolved:
                continue

            texts = [chunk.text for chunk, _ in resolved]
            vectors = embedder.embed_documents(texts)

            points = []
            for (chunk, url), vector in zip(resolved, vectors, strict=True):
                found = metadata_index.lookup(chunk.join_key)
                points.append(
                    build_point(
                        chunk, vector, url, headnote=found.headnote if found else None
                    )
                )

            upsert_points(points, settings.qdrant_url, client)
            indexed += len(points)
            if indexed % PROGRESS_EVERY < DEFAULT_BATCH_SIZE:
                print(f"[Index] {indexed:,} indexed, {unresolved:,} unresolved")

    print(f"[Index] done: {indexed:,} indexed, {unresolved:,} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
