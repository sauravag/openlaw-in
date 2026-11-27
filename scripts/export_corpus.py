"""Export the OpenLaw corpus to parquet shards for publication.

The corpus is 79 GB of Qdrant storage that nobody but the author has, which
makes "clone the repo and run it" false. This script is the publisher's half
of making it true: it reads each collection and writes parquet shards that
`scripts/fetch_corpus.py` turns back into a working Qdrant corpus.

Sliced per collection, and for `hc_judgments_v1` per court (filtered on the
`court_code` payload field), because nobody needs all 79 GB. Someone
building a tenant-rights tool in Bengaluru takes statutes + Supreme Court +
Karnataka and skips 2.8M Bombay judgments.

Dense vectors are written **float16**. See `openlaw.index.publish`'s module
docstring for the measurement behind that; `--verify-fp16 N` re-runs it
against a live sample before exporting anything and refuses to continue if
the worst-case cosine similarity falls below `--fp16-floor`.

Resumable. A 7.1M-point export WILL be interrupted, so progress is
checkpointed to `data/corpus_<target>_checkpoint.json` after every flushed
shard (the same pattern as `scripts/build_hc_index.py`), and a re-run with
no flags continues from the last completed shard.

This script only ever READS Qdrant.

Run:       python scripts/export_corpus.py --out data/export
One court: python scripts/export_corpus.py --out data/export --courts karnataka
Restart:   python scripts/export_corpus.py --out data/export --restart
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openlaw.config import get_settings  # noqa: E402
from openlaw.index.publish import (  # noqa: E402
    DENSE_VECTOR_NAME,
    ExportTarget,
    TargetBusyError,
    court_code_filter,
    float16_worst_cosine,
    point_to_row,
    read_checkpoint,
    resolve_targets,
    rows_to_table,
    sha256_file,
    shard_name,
    target_lock,
    write_checkpoint,
)
from openlaw.index.qdrant_http import build_index_client, call_with_retry  # noqa: E402

DEFAULT_OUT_DIR = Path("data/export")
DEFAULT_CHECKPOINT_DIR = Path("data")
SCROLL_BATCH = 256
# ~25k rows lands each shard around 100-250 MB compressed, comfortably under
# the size at which Hugging Face's resolver and most HTTP clients start
# struggling, while keeping the shard count per court in the low hundreds.
DEFAULT_ROWS_PER_SHARD = 25_000
DEFAULT_FP16_SAMPLE = 2_000
# Measured worst case across all three live collections is 0.9999995. A floor
# of 0.9999 leaves four orders of magnitude of headroom and still catches a
# genuinely different vector space (e.g. un-normalised vectors with large
# components, where float16 would actually bite).
DEFAULT_FP16_FLOOR = 0.9999
COMPRESSION = "zstd"
REQUEST_TIMEOUT = 300
BYTES_PER_GIB = 1024**3


@dataclass(frozen=True)
class ExportOptions:
    """Everything the export loop needs that is not the target itself."""

    out_dir: Path
    checkpoint_dir: Path
    rows_per_shard: int
    limit: int | None


@dataclass(frozen=True)
class ShardRecord:
    """One written parquet shard."""

    name: str
    rows: int
    bytes: int
    #: Empty only for shards recorded by a checkpoint that predates hashing;
    #: `write_manifest` refuses to publish those.
    sha256: str = ""


@dataclass(frozen=True)
class ExportState:
    """Resumable progress for one target. Replaced, never mutated."""

    offset: Any
    rows_written: int
    shards: tuple[ShardRecord, ...]
    finished: bool

    @property
    def next_shard_index(self) -> int:
        """Return the index the next flushed shard will be written under."""
        return len(self.shards)

    @property
    def bytes_written(self) -> int:
        """Return the total size of every shard flushed so far."""
        return sum(shard.bytes for shard in self.shards)


EMPTY_STATE = ExportState(offset=None, rows_written=0, shards=(), finished=False)


def scroll_points(
    client: httpx.Client,
    qdrant_url: str,
    collection: str,
    point_filter: dict[str, Any] | None,
    offset: Any,
) -> tuple[list[dict[str, Any]], Any]:
    """Return one page of points with vectors, and the next page's offset."""
    body: dict[str, Any] = {
        "limit": SCROLL_BATCH,
        "with_payload": True,
        "with_vector": True,
    }
    if point_filter is not None:
        body["filter"] = point_filter
    if offset is not None:
        body["offset"] = offset

    def _scroll() -> httpx.Response:
        response = client.post(
            f"{qdrant_url}/collections/{collection}/points/scroll", json=body
        )
        response.raise_for_status()
        return response

    response = call_with_retry(
        _scroll, description=f"POST collections/{collection}/points/scroll"
    )
    result = response.json()["result"]
    return result["points"], result.get("next_page_offset")


def sample_dense_vectors(
    client: httpx.Client, qdrant_url: str, collection: str, size: int
) -> np.ndarray:
    """Return a random sample of `collection`'s dense vectors."""
    body: dict[str, Any] = {
        "query": {"sample": "random"},
        "limit": size,
        "with_vector": [DENSE_VECTOR_NAME],
        "with_payload": False,
    }
    response = client.post(
        f"{qdrant_url}/collections/{collection}/points/query", json=body
    )
    response.raise_for_status()
    points = response.json()["result"]["points"]
    return np.asarray(
        [point["vector"][DENSE_VECTOR_NAME] for point in points], dtype=np.float32
    )


def verify_float16(
    client: httpx.Client,
    qdrant_url: str,
    targets: tuple[ExportTarget, ...],
    size: int,
    floor: float,
) -> None:
    """Measure float16 round-trip fidelity, refusing to export if it is bad.

    Reports the WORST case per collection, not the mean: a mean hides the
    one vector whose rank moved. Raises `SystemExit` rather than quietly
    downgrading to float32, because which precision was used changes how
    every published shard must be read.
    """
    worsts: list[float] = []
    for collection in dict.fromkeys(target.collection for target in targets):
        vectors = sample_dense_vectors(client, qdrant_url, collection, size)
        worst = float16_worst_cosine(vectors)
        print(
            f"[Export] float16 check {collection}: n={len(vectors):,} "
            f"worst cosine(float32, float16 round-trip) = {worst:.9f}"
        )
        worsts.append(worst)
    # `np.min` propagates NaN; the builtin `min` does not, and would turn an
    # unmeasurable or overflowing sample into a silent pass.
    worst_overall = float(np.min(np.asarray(worsts, dtype=np.float64)))
    if not worst_overall >= floor:
        raise SystemExit(
            f"[Export] REFUSING to export: worst float16 round-trip cosine "
            f"{worst_overall:.9f} is below the floor {floor}. These vectors "
            "would materially change retrieval at half precision -- publish "
            "them as float32 instead."
        )
    print(f"[Export] float16 is safe here (worst {worst_overall:.9f} >= {floor}).")


def load_state(target: ExportTarget, options: ExportOptions) -> ExportState:
    """Return `target`'s checkpointed state, or an empty one."""
    checkpoint = read_checkpoint(target.checkpoint_path(options.checkpoint_dir))
    if checkpoint is None:
        return EMPTY_STATE
    shards = tuple(ShardRecord(**record) for record in checkpoint["shards"])
    return ExportState(
        offset=checkpoint["offset"],
        rows_written=checkpoint["rows_written"],
        shards=shards,
        finished=checkpoint["finished"],
    )


def save_state(
    target: ExportTarget, options: ExportOptions, state: ExportState, version: str
) -> None:
    """Checkpoint `state` for `target`, atomically."""
    write_checkpoint(
        target.checkpoint_path(options.checkpoint_dir),
        {
            "target": target.key,
            "collection": target.collection,
            "court_code": target.court_code,
            "corpus_version": version,
            "offset": state.offset,
            "rows_written": state.rows_written,
            "finished": state.finished,
            "shards": [vars(shard) for shard in state.shards],
        },
    )


def flush_shard(
    rows: list[dict[str, Any]], directory: Path, index: int
) -> ShardRecord:
    """Write `rows` as shard `index` under `directory` and describe it."""
    directory.mkdir(parents=True, exist_ok=True)
    name = shard_name(index)
    path = directory / name
    pq.write_table(rows_to_table(rows), path, compression=COMPRESSION)
    return ShardRecord(
        name=name,
        rows=len(rows),
        bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def advance(
    state: ExportState,
    buffer: list[dict[str, Any]],
    directory: Path,
    next_offset: Any,
) -> ExportState:
    """Return `state` advanced by writing `buffer` as the next shard."""
    if not buffer:
        return replace(state, offset=next_offset)
    record = flush_shard(buffer, directory, state.next_shard_index)
    return ExportState(
        offset=next_offset,
        rows_written=state.rows_written + record.rows,
        shards=(*state.shards, record),
        finished=False,
    )


def trim_to_limit(
    buffer: list[dict[str, Any]], state: ExportState, options: ExportOptions
) -> tuple[list[dict[str, Any]], bool]:
    """Return `buffer` cut down to `--limit`, and whether the limit is met.

    `--limit` has to bound the row BUFFER, not just the flushed total: a
    limit smaller than `--rows-per-shard` would otherwise scroll the whole
    7M-point collection waiting for a shard to fill, which is the opposite
    of what "stop after N points" means.
    """
    if options.limit is None:
        return buffer, False
    remaining = max(options.limit - state.rows_written, 0)
    if len(buffer) < remaining:
        return buffer, False
    return buffer[:remaining], True


def progress_line(target: ExportTarget, state: ExportState, started: float) -> str:
    """Return one human-readable progress line for `target`."""
    total = target.expected_points
    of_total = f"/{total:,} ({state.rows_written / total:.1%})" if total else ""
    elapsed = max(time.monotonic() - started, 1e-9)
    return (
        f"[Export] {target.key}: {state.rows_written:,}{of_total} points, "
        f"{len(state.shards)} shards, "
        f"{state.bytes_written / BYTES_PER_GIB:.2f} GiB, "
        f"{state.rows_written / elapsed:,.0f} points/s"
    )


def export_target(
    client: httpx.Client,
    qdrant_url: str,
    target: ExportTarget,
    options: ExportOptions,
    version: str,
) -> ExportState:
    """Export one target's points to parquet shards, resuming if checkpointed.

    The checkpoint is only ever written with an empty row buffer, so the
    recorded offset is always exactly where the next shard begins: an
    interruption mid-buffer re-scrolls that partial page on resume rather
    than losing or duplicating a shard.
    """
    state = load_state(target, options)
    if state.finished:
        print(f"[Export] {target.key}: already complete, {state.rows_written:,} points")
        return state
    if state.rows_written:
        print(f"[Export] {target.key}: resuming after {state.rows_written:,} points")

    directory = target.shard_dir(options.out_dir)
    point_filter = court_code_filter(target.court_code)
    buffer: list[dict[str, Any]] = []
    started = time.monotonic()

    while True:
        points, next_offset = scroll_points(
            client, qdrant_url, target.collection, point_filter, state.offset
        )
        buffer.extend(point_to_row(point) for point in points)
        buffer, at_limit = trim_to_limit(buffer, state, options)
        if at_limit or next_offset is None or len(buffer) >= options.rows_per_shard:
            state = advance(state, buffer, directory, next_offset)
            buffer = []
            save_state(target, options, state, version)
            print(progress_line(target, state, started))
        else:
            state = replace(state, offset=next_offset)
        if at_limit or next_offset is None:
            break

    state = replace(state, finished=True)
    save_state(target, options, state, version)
    return state


def write_manifest(
    target: ExportTarget, options: ExportOptions, state: ExportState, version: str
) -> Path:
    """Write the per-target manifest `fetch_corpus.py` reads back.

    Every shard carries its SHA-256, which is what lets a fetcher prove the
    bytes it downloaded are the bytes published. Refuses a shard without one
    rather than publishing something no fetcher could verify.
    """
    unhashed = [shard.name for shard in state.shards if not shard.sha256]
    if unhashed:
        raise ValueError(
            f"{target.key}: shard(s) {', '.join(unhashed)} have no sha256 "
            "(recorded by an older checkpoint); re-run with --restart so "
            "every shard is hashed"
        )
    directory = target.shard_dir(options.out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "target": target.key,
                "collection": target.collection,
                "court_code": target.court_code,
                "corpus_version": version,
                "embedding_model": "voyage-4-nano",
                "embedding_dimensions": 1024,
                "dense_dtype": "float16",
                "points": state.rows_written,
                "shards": [vars(shard) for shard in state.shards],
            },
            indent=2,
        )
    )
    return path


def pin_line(target: ExportTarget, manifest_path: Path) -> str:
    """Return the `corpus.lock.json` line that pins this manifest.

    The manifest's own hash is the one value that must reach operators
    through git rather than through the download, so it is printed here for
    the publisher to commit.
    """
    return f'    "{target.key}": "{sha256_file(manifest_path)}"'


def report(target: ExportTarget, state: ExportState) -> None:
    """Print every shard's size, then the target's total."""
    for shard in state.shards:
        print(
            f"[Export]   {target.key}/{shard.name}: {shard.rows:,} rows, "
            f"{shard.bytes / BYTES_PER_GIB:.3f} GiB"
        )
    print(
        f"[Export] {target.key}: DONE -- {state.rows_written:,} points in "
        f"{len(state.shards)} shards, "
        f"{state.bytes_written / BYTES_PER_GIB:.2f} GiB total"
    )


def split_list(value: str | None) -> list[str] | None:
    """Return a comma-separated option as a list, or None when absent."""
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_DIR, help="output directory"
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=DEFAULT_CHECKPOINT_DIR,
        help="where checkpoints are written",
    )
    parser.add_argument(
        "--collections", help="comma-separated collections (default: all three)"
    )
    parser.add_argument(
        "--courts", help="comma-separated High Court slugs, e.g. karnataka,delhi"
    )
    parser.add_argument(
        "--rows-per-shard", type=int, default=DEFAULT_ROWS_PER_SHARD
    )
    parser.add_argument("--limit", type=int, help="stop after N points per target")
    parser.add_argument(
        "--restart", action="store_true", help="discard checkpoints and start over"
    )
    parser.add_argument(
        "--verify-fp16",
        type=int,
        nargs="?",
        const=DEFAULT_FP16_SAMPLE,
        help="measure float16 fidelity on N sampled vectors first (default 2000)",
    )
    parser.add_argument("--fp16-floor", type=float, default=DEFAULT_FP16_FLOOR)
    return parser


def clear_checkpoints(
    targets: tuple[ExportTarget, ...], checkpoint_dir: Path
) -> None:
    """Delete every selected target's checkpoint -- `--restart`."""
    for target in targets:
        target.checkpoint_path(checkpoint_dir).unlink(missing_ok=True)
    print("[Export] --restart: checkpoints cleared")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        targets = resolve_targets(split_list(args.collections), split_list(args.courts))
    except ValueError as exc:
        print(f"[Export] {exc}")
        return 2

    options = ExportOptions(
        out_dir=args.out,
        checkpoint_dir=args.checkpoint_dir,
        rows_per_shard=args.rows_per_shard,
        limit=args.limit,
    )
    if args.restart:
        clear_checkpoints(targets, options.checkpoint_dir)

    with build_index_client(timeout=REQUEST_TIMEOUT) as client:
        if args.verify_fp16:
            verify_float16(
                client, settings.qdrant_url, targets, args.verify_fp16, args.fp16_floor
            )
        try:
            for target in targets:
                # One worker per target: a second one writes the same shards and
                # the same checkpoint, and the manifest would then hash whatever
                # the interleaving happened to leave behind.
                with target_lock(target.checkpoint_path(options.checkpoint_dir)):
                    state = export_target(
                        client,
                        settings.qdrant_url,
                        target,
                        options,
                        settings.corpus_version,
                    )
                    manifest = write_manifest(
                        target, options, state, settings.corpus_version
                    )
                    report(target, state)
                    print(
                        "[Export] pin in corpus.lock.json -> manifests:\n"
                        f"{pin_line(target, manifest)}"
                    )
        except TargetBusyError as exc:
            print(f"[Export] FAIL: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
