"""Download a prebuilt OpenLaw corpus into your own Qdrant.

This is step one of actually running OpenLaw.in. The corpus is 79 GB of
Qdrant storage and roughly 30 days of local compute to build from the
upstream datasets; this script downloads the finished vectors instead, in
minutes to hours depending on how much of India you need.

================================================================================
!! READ THIS BEFORE YOU QUERY ANYTHING YOU DOWNLOAD HERE !!

EMBEDDER COMPATIBILITY IS NOT OPTIONAL.

These dense vectors were produced by voyage-4-nano at 1024 dimensions,
Matryoshka-truncated from the model's full width and then renormalised to
unit length. Any query you search them with MUST be embedded by that same
model, at those same 1024 dimensions, truncated and renormalised the same
way.

Pairing them with a different embedder DOES NOT RAISE AN ERROR. Vector
search always returns its nearest neighbours, so a mismatched embedder
returns confidently-ranked near-garbage, silently, for every query. This
project measured exactly that: voyage-4-nano queries against documents
embedded by the Voyage API's voyage-4-lite scored 15.0% top-1 and 31.7%
top-10 accuracy (median rank 61 of 300), because nano's and lite's
independent approximation errors against full voyage-4 do not cancel.
A legal-aid answer built on that retrieval cites the wrong law.

The shipped default (OPENLAW_QUERY_EMBEDDER_BACKEND=local_nano) is the
matching embedder and needs no API key. If you set anything else, you are
responsible for proving it produces the same vector space.
================================================================================

Take only what you need. Approximate download sizes:

    acts_india_v1   (statutes, 961,136 points)             2.6 GiB
    sc_judgments_v1 (Supreme Court, 371,247 points)        1.6 GiB
    hc_judgments_v1/punjab-and-haryana (1,845,344)         5.5 GiB
    hc_judgments_v1/delhi              (1,053,138)         3.9 GiB
    hc_judgments_v1/bombay             (2,788,379)         9.4 GiB
    hc_judgments_v1/karnataka          (1,467,133)         5.5 GiB

Statutes and the Supreme Court bind everywhere in India and are the useful
minimum (~4.2 GiB). A High Court binds only in its own jurisdiction, so add
the one you are in and skip the rest.

This script REFUSES to overwrite a collection that already holds points.
Nothing is written until every selected target has been checked.

Run:        python scripts/fetch_corpus.py                      # everything
Minimum:    python scripts/fetch_corpus.py --collections acts_india_v1,sc_judgments_v1
One court:  python scripts/fetch_corpus.py --courts karnataka
Plan only:  python scripts/fetch_corpus.py --courts karnataka --dry-run
Resume:     python scripts/fetch_corpus.py        # same command, it continues

TRUST. There is no default dataset repo: a Hugging Face name is not a root of
trust, and whoever registers an unowned one controls both the text this
system presents as law and the government URL cited beside it. Nothing here
can detect that after the fact. So you must say where to download from
(`--repo-id` / `OPENLAW_CORPUS_REPO_ID`) and which revision
(`--revision` / `OPENLAW_CORPUS_REVISION` / `corpus.lock.json`), and the
SHA-256 of each target's manifest is read from `corpus.lock.json` in this git
repository. Every shard is then checked against the manifest. Any failure
stops the run before one point is written to Qdrant.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openlaw.config import Settings, get_settings  # noqa: E402
from openlaw.index.publish import (  # noqa: E402
    EMBEDDING_COMPATIBILITY_WARNING,
    PUBLISHED_COLLECTIONS,
    LOCK_FILE_NAME,
    ExportTarget,
    IntegrityError,
    build_published_collection_payload,
    court_code_filter,
    load_corpus_lock,
    read_checkpoint,
    resolve_targets,
    row_to_point,
    verify_sha256,
    write_checkpoint,
)
from openlaw.index.corpus_clobber import (  # noqa: E402,F401
    Existing,
    clobber_refusal,
    inspect_collection,
    refuse_to_clobber,
)
from openlaw.index.corpus_trust import (  # noqa: E402
    REPO_ID_ENV,
    REVISION_ENV,
    resolve_trust,
    shard_hashes,
    verify_shard,
)
from openlaw.index.qdrant_http import build_index_client, call_with_retry  # noqa: E402

# There is deliberately no default repo id. The project's Hugging Face
# namespace is not registered yet, and guessing one would recreate the
# squatting risk under a different name.
# TODO: once the namespace is registered, document it in the README as the
# *suggested* value -- never as a default this script trusts on its own.
DEFAULT_LOCK_FILE = Path(__file__).resolve().parent.parent / LOCK_FILE_NAME
MANIFEST_NAME = "manifest.json"
DEFAULT_BATCH_SIZE = 256
DEFAULT_CACHE_DIR = Path("data/corpus_download")
DEFAULT_CHECKPOINT_DIR = Path("data")
REQUEST_TIMEOUT = 300
COLLECTION_ALREADY_EXISTS = 409
COLLECTION_NOT_FOUND = 404
EXPECTED_DENSE_DTYPE = "float16"
BYTES_PER_GIB = 1024**3

#: Measured bytes per point in the published zstd parquet shards, from a
#: 3,000-point sample of each slice. Used only for the size estimate printed
#: before anything is downloaded.
PUBLISHED_BYTES_PER_POINT: dict[str, int] = {
    "acts_india_v1": 2879,
    "sc_judgments_v1": 4525,
    "hc_judgments_v1/punjab-and-haryana": 3216,
    "hc_judgments_v1/delhi": 3932,
    "hc_judgments_v1/bombay": 3608,
    "hc_judgments_v1/karnataka": 3999,
}


@dataclass(frozen=True)
class CorpusSettings:
    """The only two settings this script needs."""

    qdrant_url: str
    corpus_version: str


def _settings_default(field: str) -> str:
    """Return `openlaw.config.Settings`' own default for `field`."""
    return str(Settings.model_fields[field].default)


def resolve_settings() -> CorpusSettings:
    """Return `qdrant_url` and `corpus_version`, with or without a `.env`.

    `openlaw.config.Settings` REQUIRES `OPENLAW_VAQUILL_ROOT` and
    `OPENLAW_KANOONGPT_ROOT` -- the raw upstream dataset snapshots. Someone
    downloading a prebuilt corpus has neither, by definition: not having to
    build from those snapshots is the entire point of this script. Requiring
    them here would fail a fresh clone's very first command with a pydantic
    stack trace naming two paths the user is right not to have.

    So the real `Settings` is used whenever it validates -- a configured
    machine's `.env` stays authoritative -- and these two fields, both of
    which have defaults, are read straight from the environment when it does
    not. The defaults are read OFF `Settings` rather than respelled, so they
    cannot drift from the rest of the application.
    """
    try:
        settings = get_settings()
    except ValidationError:
        return CorpusSettings(
            qdrant_url=os.environ.get(
                "OPENLAW_QDRANT_URL", _settings_default("qdrant_url")
            ),
            corpus_version=os.environ.get(
                "OPENLAW_CORPUS_VERSION", _settings_default("corpus_version")
            ),
        )
    return CorpusSettings(
        qdrant_url=settings.qdrant_url, corpus_version=settings.corpus_version
    )


@dataclass(frozen=True)
class RestoreState:
    """Resumable progress for one target. Replaced, never mutated."""

    shard_index: int
    row_offset: int
    points: int


@dataclass(frozen=True)
class FetchOptions:
    """Everything the fetch loop needs that is not the target itself."""

    checkpoint_dir: Path
    batch_size: int
    keep_downloads: bool


EMPTY_STATE = RestoreState(shard_index=0, row_offset=0, points=0)



# --- creating the collection --------------------------------------------------


def create_collection(client: httpx.Client, qdrant_url: str, collection: str) -> None:
    """Create `collection` with the published geometry, tolerating 409.

    The body comes from `openlaw.index.publish
    .build_published_collection_payload`, which reuses
    `openlaw.index.qdrant_restore.build_collection_payload` -- so the dense
    geometry here is literally the same code the original build used, and
    the sparse vector carries `modifier: "idf"`. That modifier is required:
    stored sparse values hold only BM25's term-frequency component, so
    without it hybrid search scores wrongly and silently.
    """

    def _create() -> httpx.Response:
        response = client.put(
            f"{qdrant_url}/collections/{collection}",
            json=build_published_collection_payload(collection),
        )
        if response.status_code != COLLECTION_ALREADY_EXISTS:
            response.raise_for_status()
        return response

    call_with_retry(_create, description=f"PUT collections/{collection}")


# --- upserting shards ---------------------------------------------------------


def iter_row_batches(
    path: Path, batch_size: int, start_row: int
) -> Iterator[list[dict[str, Any]]]:
    """Yield `path`'s rows in `batch_size` batches, skipping `start_row`.

    Re-batched rather than yielding parquet's own record batches, so batch
    boundaries are deterministic regardless of how the shard's row groups
    happen to be laid out, and a resumed run lands on the same rows.
    """
    seen = 0
    pending: list[dict[str, Any]] = []
    for record_batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        rows = record_batch.to_pylist()
        if seen + len(rows) > start_row:
            pending.extend(rows[max(start_row - seen, 0) :])
        seen += len(rows)
        while len(pending) >= batch_size:
            yield pending[:batch_size]
            pending = pending[batch_size:]
    if pending:
        yield pending


def upsert_batch(
    client: httpx.Client,
    qdrant_url: str,
    collection: str,
    points: list[dict[str, Any]],
) -> None:
    """Write one batch of points, with `wait=true` and connection retries."""

    def _upsert() -> httpx.Response:
        response = client.put(
            f"{qdrant_url}/collections/{collection}/points",
            params={"wait": "true"},
            json={"points": points},
        )
        response.raise_for_status()
        return response

    call_with_retry(
        _upsert,
        description=f"PUT collections/{collection}/points ({len(points)} points)",
    )


def upsert_shard(
    client: httpx.Client,
    qdrant_url: str,
    collection: str,
    shard: Path,
    batch_size: int,
    start_row: int,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Upsert one parquet shard, returning how many rows were written.

    `on_batch` is called with the cumulative row offset within this shard
    after each successful batch, which is how the caller checkpoints at
    batch granularity. Point IDs are a deterministic UUID5 of `chunk_id`
    (`openlaw.index.upsert.point_id`), so a replayed batch updates points
    rather than duplicating them -- an over-eager resume costs time, never
    correctness.
    """
    written = 0
    for rows in iter_row_batches(shard, batch_size, start_row):
        upsert_batch(
            client, qdrant_url, collection, [row_to_point(row) for row in rows]
        )
        written += len(rows)
        if on_batch is not None:
            on_batch(start_row + written)
    return written


# --- the dataset repo ---------------------------------------------------------


def repo_path(target: ExportTarget, name: str) -> str:
    """Return the path of one of `target`'s files inside the dataset repo."""
    return f"{target.key}/{name}"


def validate_manifest(target: ExportTarget, manifest: dict[str, Any]) -> dict[str, Any]:
    """Return `manifest` if it really describes `target`, else raise.

    A manifest is the only thing telling this script what the shards hold,
    so a mismatch has to stop the run: upserting Karnataka judgments while
    believing they are Delhi's would put judgments that bind in one state
    behind a jurisdiction filter for another.
    """
    collection = manifest.get("collection")
    if collection != target.collection:
        raise ValueError(
            f"{repo_path(target, MANIFEST_NAME)} describes collection "
            f"{collection!r}, not {target.collection!r}"
        )
    court_code = manifest.get("court_code")
    if court_code != target.court_code:
        raise ValueError(
            f"{repo_path(target, MANIFEST_NAME)} describes court_code "
            f"{court_code!r}, not {target.court_code!r}"
        )
    dtype = manifest.get("dense_dtype", EXPECTED_DENSE_DTYPE)
    if dtype != EXPECTED_DENSE_DTYPE:
        raise ValueError(
            f"{repo_path(target, MANIFEST_NAME)} stores dense vectors as "
            f"{dtype!r}; this version reads {EXPECTED_DENSE_DTYPE!r} only"
        )
    return manifest


def build_downloader(
    repo_id: str, revision: str, cache_dir: Path
) -> Callable[[str], Path]:
    """Return a function downloading one repo path to a local file.

    `huggingface_hub` is imported lazily, with an actionable error naming
    the install command, so the rest of this script -- and its tests --
    never require it. Optional for the same reason the local embedder is: an
    API-only deployment should not have to install it.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "[Fetch] huggingface_hub is not installed. Install it with:\n"
            '    pip install -e ".[corpus]"'
        ) from exc

    def download(path: str) -> Path:
        return Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=path,
                repo_type="dataset",
                revision=revision,
                local_dir=str(cache_dir),
            )
        )

    return download


# --- integrity ----------------------------------------------------------------


def load_pinned_manifest(
    target: ExportTarget, download: Callable[[str], Path], pins: dict[str, str]
) -> dict[str, Any]:
    """Download `target`'s manifest and prove it is the one git pins.

    The hash is checked on the raw bytes BEFORE they are parsed, so a hostile
    manifest never reaches `json.loads` or any later logic.
    """
    pin = pins.get(target.key)
    if pin is None:
        raise IntegrityError(
            f"{target.key}: {LOCK_FILE_NAME} has no manifest pin for this "
            "target, so nothing ties the download to a hash reviewed in git. "
            "Add the manifest SHA-256 printed by scripts/export_corpus.py to "
            f"{LOCK_FILE_NAME} (under \"manifests\") from a source you trust."
        )
    path = download(repo_path(target, MANIFEST_NAME))
    verify_sha256(path, pin, label=f"{repo_path(target, MANIFEST_NAME)} (manifest)")
    return validate_manifest(target, json.loads(path.read_text()))


def download_verified_shards(
    target: ExportTarget,
    manifest: dict[str, Any],
    first: int,
    download: Callable[[str], Path],
) -> list[Path]:
    """Download shards `first..` and return their paths, every one verified.

    All of a target's shards are verified before the caller writes any of
    them, so a forged shard 40 cannot be discovered after shards 0-39 are
    already in Qdrant. See `openlaw.index.corpus_trust.verify_shard`.
    """
    hashes = shard_hashes(target.key, manifest)
    names = shard_names(manifest)
    return [
        verify_shard(
            target.key, name, download(repo_path(target, name)), hashes, set(names)
        )
        for name in names[first:]
    ]


# --- checkpoints --------------------------------------------------------------


def save_state(
    target: ExportTarget, checkpoint_dir: Path, state: RestoreState, version: str
) -> None:
    """Checkpoint `state` for `target`, atomically."""
    write_checkpoint(
        target.checkpoint_path(checkpoint_dir),
        {
            "target": target.key,
            "collection": target.collection,
            "court_code": target.court_code,
            "corpus_version": version,
            "shard_index": state.shard_index,
            "row_offset": state.row_offset,
            "points": state.points,
        },
    )


def load_state(
    target: ExportTarget, checkpoint_dir: Path, expected_version: str | None = None
) -> RestoreState:
    """Return `target`'s checkpointed progress, or a fresh state.

    A checkpoint written against a different `corpus_version` is refused
    rather than resumed: a different snapshot means different shards, so the
    recorded shard index and row offset point somewhere else entirely.
    """
    checkpoint = read_checkpoint(target.checkpoint_path(checkpoint_dir))
    if checkpoint is None:
        return EMPTY_STATE
    found = checkpoint.get("corpus_version")
    if expected_version is not None and found != expected_version:
        raise SystemExit(
            f"[Fetch] the checkpoint for {target.key} was written against "
            f"corpus_version {found!r}, not {expected_version!r}. Pass "
            "--restart to discard it and start this target over."
        )
    return RestoreState(
        shard_index=checkpoint["shard_index"],
        row_offset=checkpoint["row_offset"],
        points=checkpoint["points"],
    )


# --- the run ------------------------------------------------------------------


def shard_names(manifest: dict[str, Any]) -> list[str]:
    """Return the manifest's shard filenames, in order."""
    return [shard["name"] for shard in manifest["shards"]]


def _shard_checkpointer(
    target: ExportTarget, options: FetchOptions, index: int, base: int, version: str
) -> Callable[[int], None]:
    """Return the per-batch checkpoint callback for one shard.

    `base` is how many points were already in before this shard started, so
    the recorded total stays right across a resume.
    """

    def record(row: int) -> None:
        save_state(
            target,
            options.checkpoint_dir,
            RestoreState(shard_index=index, row_offset=row, points=base + row),
            version,
        )

    return record


def fetch_target(
    client: httpx.Client,
    qdrant_url: str,
    target: ExportTarget,
    manifest: dict[str, Any],
    download: Callable[[str], Path],
    options: FetchOptions,
    version: str,
) -> RestoreState:
    """Download and upsert every shard of one target, resuming if checkpointed."""
    state = load_state(target, options.checkpoint_dir, version)
    names = shard_names(manifest)
    total = manifest.get("points") or target.expected_points
    if state.shard_index >= len(names):
        print(f"[Fetch] {target.key}: already complete, {state.points:,} points")
        return state
    if state.points:
        print(
            f"[Fetch] {target.key}: resuming at shard {state.shard_index} "
            f"row {state.row_offset} ({state.points:,} points already in)"
        )

    # Verify every remaining shard BEFORE the first write for this target.
    shards = download_verified_shards(target, manifest, state.shard_index, download)
    create_collection(client, qdrant_url, target.collection)
    for index, shard in enumerate(shards, start=state.shard_index):
        base = state.points - state.row_offset
        written = upsert_shard(
            client,
            qdrant_url,
            target.collection,
            shard,
            options.batch_size,
            state.row_offset,
            _shard_checkpointer(target, options, index, base, version),
        )
        state = RestoreState(
            shard_index=index + 1,
            row_offset=0,
            points=base + state.row_offset + written,
        )
        save_state(target, options.checkpoint_dir, state, version)
        share = f" ({state.points / total:.1%})" if total else ""
        print(
            f"[Fetch] {target.key}: shard {index + 1}/{len(names)}, "
            f"{state.points:,}/{total:,} points{share}"
        )
        if not options.keep_downloads:
            shard.unlink(missing_ok=True)
    return state


def describe_plan(targets: Sequence[ExportTarget]) -> int:
    """Print what would be fetched and roughly how big it is, in bytes."""
    print("[Fetch] plan:")
    total = 0
    for target in targets:
        points = target.expected_points
        estimate = points * PUBLISHED_BYTES_PER_POINT.get(target.key, 0)
        total += estimate
        print(
            f"[Fetch]   {target.key}: ~{points:,} points, "
            f"~{estimate / BYTES_PER_GIB:.1f} GiB download"
        )
    print(f"[Fetch] total: ~{total / BYTES_PER_GIB:.1f} GiB")
    return total


def split_list(value: str | None) -> list[str] | None:
    """Return a comma-separated option as a list, or None when absent."""
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _add_trust_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the options that say where the corpus comes from and what to trust."""
    parser.add_argument(
        "--repo-id",
        default=os.environ.get(REPO_ID_ENV),
        help=f"REQUIRED (or set {REPO_ID_ENV}): Hugging Face dataset repo. No default.",
    )
    parser.add_argument(
        "--revision",
        default=os.environ.get(REVISION_ENV),
        help=(
            f"REQUIRED unless {LOCK_FILE_NAME} pins one (or set {REVISION_ENV}): "
            "a commit hash or release tag. Branch names are refused."
        ),
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK_FILE,
        help=f"git-tracked pins: manifest SHA-256s and revision (default: {LOCK_FILE_NAME})",
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser.

    `--help` reproduces the whole module docstring, warning included, and
    repeats it as the epilog: a stranger's first contact with this corpus is
    very likely `--help`, and the embedder-compatibility failure is silent,
    so it cannot be left to a file they may never open.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EMBEDDING_COMPATIBILITY_WARNING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_trust_arguments(parser)
    parser.add_argument(
        "--collections",
        help=f"comma-separated: {', '.join(PUBLISHED_COLLECTIONS)} (default: all)",
    )
    parser.add_argument(
        "--courts", help="comma-separated High Court slugs, e.g. karnataka,delhi"
    )
    parser.add_argument("--qdrant-url", help="override OPENLAW_QDRANT_URL")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--keep-downloads",
        action="store_true",
        help="keep each parquet shard after upserting it (doubles disk use)",
    )
    parser.add_argument(
        "--restart", action="store_true", help="discard checkpoints for these targets"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and sizes, download and write nothing",
    )
    return parser


def resumable_targets(
    targets: Sequence[ExportTarget], checkpoint_dir: Path
) -> frozenset[str]:
    """Return the keys of targets a checkpoint says this fetch already started."""
    return frozenset(
        target.key for target in targets if load_state(target, checkpoint_dir).points
    )


def fetch_all(
    client: httpx.Client,
    qdrant_url: str,
    targets: Sequence[ExportTarget],
    download: Callable[[str], Path],
    options: FetchOptions,
    version: str,
    manifest_pins: dict[str, str],
) -> None:
    """Verify every manifest, then download and restore each target in order.

    Called only after `refuse_to_clobber` has cleared every target. Every
    manifest is checked against its git pin first, so a bad pin on the last
    target is found before the first target writes anything.
    """
    manifests = {
        target.key: load_pinned_manifest(target, download, manifest_pins)
        for target in targets
    }
    for target in targets:
        state = fetch_target(
            client, qdrant_url, target, manifests[target.key], download, options, version
        )
        print(f"[Fetch] {target.key}: DONE -- {state.points:,} points")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = resolve_settings()
    qdrant_url = args.qdrant_url or settings.qdrant_url
    try:
        targets = resolve_targets(split_list(args.collections), split_list(args.courts))
    except ValueError as exc:
        print(f"[Fetch] {exc}")
        return 2

    print(EMBEDDING_COMPATIBILITY_WARNING)
    describe_plan(targets)
    if args.dry_run:
        print("[Fetch] --dry-run: nothing downloaded, nothing written.")
        return 0

    try:
        trust = resolve_trust(
            args.repo_id, args.revision, load_corpus_lock(args.lock_file)
        )
    except IntegrityError as exc:
        print(f"[Fetch] REFUSING to start -- {exc}")
        return 2

    options = FetchOptions(
        checkpoint_dir=args.checkpoint_dir,
        batch_size=args.batch_size,
        keep_downloads=args.keep_downloads,
    )
    if args.restart:
        for target in targets:
            target.checkpoint_path(options.checkpoint_dir).unlink(missing_ok=True)
        print("[Fetch] --restart: checkpoints cleared")

    resumable = resumable_targets(targets, options.checkpoint_dir)
    download = build_downloader(trust.repo_id, trust.revision, args.cache_dir)

    with build_index_client(timeout=REQUEST_TIMEOUT) as client:
        refuse_to_clobber(client, qdrant_url, targets, resumable)
        try:
            fetch_all(
                client,
                qdrant_url,
                targets,
                download,
                options,
                settings.corpus_version,
                trust.manifest_pins,
            )
        except IntegrityError as exc:
            print(f"[Fetch] INTEGRITY FAILURE -- {exc}")
            return 1
    print(
        "[Fetch] all done. `curl localhost:8000/readyz` reports `degraded` "
        "until acts_india_v1 and sc_judgments_v1 both hold vectors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
