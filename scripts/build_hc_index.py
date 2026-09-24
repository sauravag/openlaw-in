"""Embed and index English High Court chunks into the shared `hc_judgments_v1`.

Filters to English, deduplicates, resolves a government URL for every chunk via
the KanoonGPT join, embeds with **voyage-4-lite** (not voyage-4 -- a deliberate
cost choice, verified to share the voyage-4 embedding space closely enough for
retrieval: mean cosine 0.961 against stored voyage-4 vectors, min 0.932), and
upserts into Qdrant with both dense and sparse (BM25 term-frequency) vectors.
Chunks whose citation cannot be resolved are counted and skipped, never
indexed -- the citation contract is absolute.

One collection holds every registered High Court (`openlaw.corpus.high_courts`),
distinguished by the `court_code` payload field, so `--court` selects which
court's source file to stream without needing a new collection per court.

This is a multi-hour, real-money run against the Voyage API with no way to undo
a wasted API bill, so it checkpoints its progress per court: after every batch
that is actually upserted, `data/hc_index_checkpoint_<court>.json` (git-ignored)
records how many chunks of that court's deterministic input stream have been
consumed. A restart resumes by skipping that many chunks rather than
re-embedding from the start. Point IDs are also a deterministic UUID5 of
`chunk_id` (see `openlaw.index.upsert.point_id`), so even a resumed run that
overlaps the previous one updates points rather than duplicating them -- the
checkpoint is an optimization, not a correctness requirement.

`--limit` caps the number of chunks processed before exiting cleanly -- it
exists to prove the pipeline end to end on a bounded slice without paying for
(or waiting many hours on) the full ~1.85M-chunk run. Omit it for a real run;
the checkpoint it leaves behind lets a later unlimited run resume exactly
where the bounded slice left off, rather than wasting that work.

Run:      python scripts/build_hc_index.py
Restart:  python scripts/build_hc_index.py --restart
Bounded:  python scripts/build_hc_index.py --limit 3000
"""

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from openlaw.config import get_settings
from openlaw.corpus.filters import deduplicate, english_only
from openlaw.corpus.high_courts import HighCourt, get_high_court
from openlaw.corpus.join import build_metadata_index
from openlaw.corpus.sources import resolve_judgment_url
from openlaw.datasets.vaquill import read_judgment_chunks
from openlaw.index.embed import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_TOKENS,
    VoyageEmbedder,
    batched_by_tokens,
)
from openlaw.index.hc_upsert import build_hc_point, ensure_hc_collection, upsert_hc_points
from openlaw.index.sparse import average_token_count, bm25_vector

PROGRESS_EVERY = 10_000
DEFAULT_COURT_SLUG = "punjab-and-haryana"
EMBEDDING_MODEL = "voyage-4-lite"
AVERAGE_LENGTH_SAMPLE_SIZE = 20_000


def checkpoint_path_for(slug: str) -> Path:
    """Return the per-court checkpoint path -- courts never share a checkpoint."""
    return Path(f"data/hc_index_checkpoint_{slug}.json")


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    """Return the checkpoint at `path`, or None when it does not exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_checkpoint(path: Path, processed: int, corpus_version: str, court_slug: str) -> None:
    """Atomically record progress, so a crash mid-write never leaves a
    half-written, unparseable checkpoint behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed": processed,
        "corpus_version": corpus_version,
        "court_slug": court_slug,
    }
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload))
    tmp_path.replace(path)


def clear_checkpoint(path: Path) -> None:
    """Remove the checkpoint at `path`, if any -- used by `--restart`."""
    path.unlink(missing_ok=True)


def resume_offset(checkpoint: dict[str, Any] | None, corpus_version: str, court_slug: str) -> int:
    """Return how many chunks to skip on resume.

    Returns 0 when there is no checkpoint. Raises ValueError when the checkpoint
    was written against a different `corpus_version` or `court_slug`: either
    means a different stream order, so the recorded count would skip or
    re-embed the wrong chunks.
    """
    if checkpoint is None:
        return 0
    checkpoint_version = checkpoint.get("corpus_version")
    checkpoint_slug = checkpoint.get("court_slug")
    if checkpoint_version != corpus_version or checkpoint_slug != court_slug:
        raise ValueError(
            f"checkpoint (corpus_version={checkpoint_version!r}, "
            f"court_slug={checkpoint_slug!r}) does not match configured "
            f"(corpus_version={corpus_version!r}, court_slug={court_slug!r}); "
            "refusing to resume from a different snapshot or court. Pass "
            "--restart to discard the checkpoint and start over."
        )
    return int(checkpoint.get("processed", 0))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--court",
        default=DEFAULT_COURT_SLUG,
        help=f"registered High Court slug to build (default: {DEFAULT_COURT_SLUG})",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="ignore and clear any existing checkpoint for this court, and start over",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after processing this many chunks (proves the pipeline on a "
        "bounded slice without running the full multi-hour build)",
    )
    return parser.parse_args(argv)


def estimate_average_length(source: Path, corpus_version: str, sample_size: int) -> float:
    """Return the mean BM25 token count over the first `sample_size` chunks.

    A cheap up-front pass, not a full scan -- BM25 length normalisation needs a
    corpus average, and the statistic is a denominator, so sampling error here
    is immaterial (mirrors `scripts/build_sparse_vectors.py`'s approach).
    """
    sample = itertools.islice(
        deduplicate(english_only(read_judgment_chunks(source, corpus_version=corpus_version))),
        sample_size,
    )
    return average_token_count(chunk.text for chunk in sample)


def _log_progress(processed: int, indexed: int, unresolved: int, started_at: float) -> None:
    """Print a progress line including elapsed time and throughput -- this run
    takes hours, so a rate estimate matters for judging how much is left."""
    elapsed = time.monotonic() - started_at
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"[Index] {processed:,} processed, {indexed:,} indexed, "
        f"{unresolved:,} unresolved ({rate:.1f} chunks/sec, {elapsed:,.0f}s elapsed)"
    )


def _index_batch(
    batch: list[Any],
    metadata_index: Any,
    embedder: VoyageEmbedder,
    avg_len: float,
    qdrant_url: str,
    client: httpx.Client,
) -> tuple[int, int]:
    """Resolve, embed, and upsert one batch. Returns `(indexed, unresolved)`."""
    resolved = [
        (chunk, url)
        for chunk in batch
        if (url := resolve_judgment_url(chunk, metadata_index)) is not None
    ]
    unresolved = len(batch) - len(resolved)
    if not resolved:
        return 0, unresolved

    texts = [chunk.text for chunk, _ in resolved]
    dense_vectors = embedder.embed_documents(texts)

    points = []
    for (chunk, url), dense_vector in zip(resolved, dense_vectors, strict=True):
        found = metadata_index.lookup(chunk.join_key)
        sparse_vector = bm25_vector(chunk.text, avg_len)
        points.append(
            build_hc_point(
                chunk, dense_vector, sparse_vector, url,
                headnote=found.headnote if found else None,
            )
        )

    upsert_hc_points(points, qdrant_url, client)
    return len(points), unresolved


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    if not settings.voyage_api_key:
        print("[Index] FAIL: OPENLAW_VOYAGE_API_KEY is not set")
        return 1

    court: HighCourt = get_high_court(args.court)
    checkpoint_path = checkpoint_path_for(court.slug)
    if args.restart:
        print(f"[Index] --restart: clearing any existing checkpoint for {court.slug}")
        clear_checkpoint(checkpoint_path)

    checkpoint = read_checkpoint(checkpoint_path)
    resume_count = resume_offset(checkpoint, settings.corpus_version, court.slug)
    if resume_count:
        print(f"[Index] resuming {court.slug}: skipping {resume_count:,} already-processed chunks")

    print(f"[Index] building KanoonGPT metadata index ({court.kanoongpt_court_code} only)")
    metadata_index = build_metadata_index(
        settings.kanoongpt_root, court_codes=frozenset({court.kanoongpt_court_code})
    )
    print(f"[Index] metadata index holds {len(metadata_index):,} cases")

    source = settings.vaquill_root / court.source_file
    print("[Index] estimating average chunk length for BM25 normalisation")
    avg_len = estimate_average_length(
        source, settings.corpus_version, AVERAGE_LENGTH_SAMPLE_SIZE
    )
    print(f"[Index] average chunk length: {avg_len:.1f} tokens")

    chunks = deduplicate(
        english_only(read_judgment_chunks(source, corpus_version=settings.corpus_version))
    )
    if resume_count:
        chunks = itertools.islice(chunks, resume_count, None)

    embedder = VoyageEmbedder(settings.voyage_api_key, model=EMBEDDING_MODEL)
    processed = resume_count
    indexed = 0
    unresolved = 0
    started_at = time.monotonic()

    with httpx.Client(timeout=120) as client:
        ensure_hc_collection(settings.qdrant_url, client)

        for batch in batched_by_tokens(
            chunks, max_items=DEFAULT_BATCH_SIZE, max_tokens=MAX_BATCH_TOKENS,
            text_of=lambda chunk: chunk.text,
        ):
            batch_indexed, batch_unresolved = _index_batch(
                batch, metadata_index, embedder, avg_len, settings.qdrant_url, client
            )
            processed += len(batch)
            indexed += batch_indexed
            unresolved += batch_unresolved

            if batch_indexed:
                write_checkpoint(checkpoint_path, processed, settings.corpus_version, court.slug)

            if processed % PROGRESS_EVERY < DEFAULT_BATCH_SIZE:
                _log_progress(processed, indexed, unresolved, started_at)

            if args.limit is not None and processed >= args.limit:
                print(f"[Index] --limit {args.limit:,} reached, stopping")
                break

    _log_progress(processed, indexed, unresolved, started_at)
    print(f"[Index] done: {indexed:,} indexed, {unresolved:,} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
