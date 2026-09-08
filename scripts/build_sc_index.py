"""Embed and index English Supreme Court chunks.

Filters to English, deduplicates, resolves a government URL for every chunk via
the KanoonGPT join, embeds with voyage-4, and upserts into Qdrant. Chunks whose
citation cannot be resolved are counted and skipped, never indexed.

This is a multi-hour, real-money run against the Voyage API with no way to undo
a wasted API bill, so it checkpoints its progress: after every batch that is
actually upserted, `data/sc_index_checkpoint.json` (git-ignored) records how many
chunks of the deterministic input stream have been consumed. A restart resumes
by skipping that many chunks rather than re-embedding from the start. Point IDs
are also a deterministic UUID5 of `chunk_id` (see `openlaw.index.upsert`), so
even a resumed run that overlaps the previous one updates points rather than
duplicating them — the checkpoint is an optimization, not a correctness
requirement, but it is the difference between a five-minute resume and paying
to re-embed 371,247 chunks.

Run:      python scripts/build_sc_index.py
Restart:  python scripts/build_sc_index.py --restart
"""

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from openlaw.config import get_settings
from openlaw.corpus.filters import deduplicate, english_only
from openlaw.corpus.join import build_metadata_index
from openlaw.corpus.sources import resolve_judgment_url
from openlaw.datasets.vaquill import read_judgment_chunks
from openlaw.index.embed import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_TOKENS,
    VoyageEmbedder,
    batched_by_tokens,
)
from openlaw.index.qdrant_restore import CollectionSpec, ensure_collection
from openlaw.index.upsert import SC_COLLECTION, build_point, upsert_points

PROGRESS_EVERY = 10_000
SUPREME_COURT_CODE = "SCI"
DEFAULT_CHECKPOINT_PATH = Path("data/sc_index_checkpoint.json")


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    """Return the checkpoint at `path`, or None when it does not exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_checkpoint(path: Path, processed: int, corpus_version: str) -> None:
    """Atomically record that `processed` chunks of `corpus_version` are done.

    Written via a temp file + rename so a crash mid-write never leaves a
    half-written, unparseable checkpoint behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"processed": processed, "corpus_version": corpus_version}
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload))
    tmp_path.replace(path)


def clear_checkpoint(path: Path) -> None:
    """Remove the checkpoint at `path`, if any — used by `--restart`."""
    path.unlink(missing_ok=True)


def resume_offset(checkpoint: dict[str, Any] | None, corpus_version: str) -> int:
    """Return how many chunks to skip on resume.

    Returns 0 when there is no checkpoint. Raises ValueError when the
    checkpoint was written against a different `corpus_version`: a different
    snapshot means a different stream order, so the recorded count would skip
    or re-embed the wrong chunks.
    """
    if checkpoint is None:
        return 0
    checkpoint_version = checkpoint.get("corpus_version")
    if checkpoint_version != corpus_version:
        raise ValueError(
            f"checkpoint corpus_version {checkpoint_version!r} does not match "
            f"configured corpus_version {corpus_version!r}; refusing to resume "
            "from a different snapshot. Pass --restart to discard the "
            "checkpoint and start over."
        )
    return int(checkpoint.get("processed", 0))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="ignore and clear any existing checkpoint, and start over from chunk 0",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    if not settings.voyage_api_key:
        print("[Index] FAIL: OPENLAW_VOYAGE_API_KEY is not set")
        return 1

    checkpoint_path = DEFAULT_CHECKPOINT_PATH
    if args.restart:
        print("[Index] --restart: clearing any existing checkpoint")
        clear_checkpoint(checkpoint_path)

    checkpoint = read_checkpoint(checkpoint_path)
    resume_count = resume_offset(checkpoint, settings.corpus_version)
    if resume_count:
        print(f"[Index] resuming from checkpoint: skipping {resume_count:,} already-processed chunks")

    print("[Index] building KanoonGPT metadata index (Supreme Court records only)")
    metadata_index = build_metadata_index(
        settings.kanoongpt_root, court_codes=frozenset({SUPREME_COURT_CODE})
    )
    print(f"[Index] metadata index holds {len(metadata_index):,} cases")

    source = settings.vaquill_root / "in_supreme-court_judgments.parquet"
    chunks = deduplicate(
        english_only(read_judgment_chunks(source, corpus_version=settings.corpus_version))
    )
    if resume_count:
        chunks = itertools.islice(chunks, resume_count, None)

    embedder = VoyageEmbedder(settings.voyage_api_key)
    processed = resume_count
    indexed = 0
    unresolved = 0

    with httpx.Client(timeout=120) as client:
        spec = CollectionSpec(name=SC_COLLECTION, shards=1, mirror="")
        ensure_collection(spec, settings.qdrant_url, client)

        for batch in batched_by_tokens(
            chunks,
            max_items=DEFAULT_BATCH_SIZE,
            max_tokens=MAX_BATCH_TOKENS,
            text_of=lambda chunk: chunk.text,
        ):
            resolved = [
                (chunk, url)
                for chunk in batch
                if (url := resolve_judgment_url(chunk, metadata_index)) is not None
            ]
            unresolved += len(batch) - len(resolved)
            processed += len(batch)

            if resolved:
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
                write_checkpoint(checkpoint_path, processed, settings.corpus_version)

            if processed % PROGRESS_EVERY < DEFAULT_BATCH_SIZE:
                print(f"[Index] {processed:,} processed, {indexed:,} indexed, {unresolved:,} unresolved")

    print(f"[Index] done: {indexed:,} indexed, {unresolved:,} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
