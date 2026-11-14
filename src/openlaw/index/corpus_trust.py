"""The trust chain for a downloaded corpus, kept apart from the fetch loop.

The git repository is the root of trust, not a Hugging Face name:

    corpus.lock.json (in git)  ->  manifest SHA-256  ->  per-shard SHA-256

A corpus supplier controls the text presented as law AND the `source_url`
rendered beside it, and the citation validator cannot see either, so every
failure here raises `IntegrityError` and must stop the run before Qdrant is
written. Nothing in this module logs and continues.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openlaw.index.publish import (
    LOCK_FILE_NAME,
    CorpusLock,
    IntegrityError,
    require_pinned_revision,
    require_sha256_hex,
    verify_sha256,
)

REPO_ID_ENV = "OPENLAW_CORPUS_REPO_ID"
REVISION_ENV = "OPENLAW_CORPUS_REVISION"


@dataclass(frozen=True)
class TrustAnchor:
    """Where to download from, and what the repo pins about it."""

    repo_id: str
    revision: str
    manifest_pins: dict[str, str]


def resolve_trust(
    repo_id: str | None, revision: str | None, lock: CorpusLock
) -> TrustAnchor:
    """Return the trust anchor, or raise `IntegrityError` saying what is missing.

    Nothing is inferred: no repo id is guessed, and a revision is accepted
    only if it names a commit or tag. The revision may come from the lock
    file (committed, reviewed) but the repo id may not, so the one thing a
    typo or a squatter could redirect is always typed by the operator.
    """
    if not repo_id:
        raise IntegrityError(
            "no dataset repo was given. Pass --repo-id OWNER/NAME or set "
            f"{REPO_ID_ENV}. There is no default on purpose: an unowned "
            "Hugging Face name can be registered by anyone, and whoever "
            "serves the shards controls the law text and its citations."
        )
    pinned = require_pinned_revision(revision or lock.revision)
    return TrustAnchor(
        repo_id=repo_id, revision=pinned, manifest_pins=dict(lock.manifests)
    )


def shard_hashes(key: str, manifest: dict[str, Any]) -> dict[str, str]:
    """Return the manifest's `{shard name: sha256}`, refusing any gap."""
    hashes: dict[str, str] = {}
    for entry in manifest["shards"]:
        name = entry.get("name")
        if not isinstance(name, str) or not name or name != Path(name).name:
            raise IntegrityError(
                f"{key}: the manifest lists a shard with an unsafe name "
                f"{name!r}; shard names must be plain filenames"
            )
        hashes[name] = require_sha256_hex(
            entry.get("sha256"), f"{key}/{name}: manifest sha256"
        )
    return hashes


def _refuse_unlisted(key: str, shard: Path, listed: set[str]) -> None:
    """Refuse a parquet file beside `shard` that the manifest does not list."""
    extras = sorted(p.name for p in shard.parent.glob("*.parquet") if p.name not in listed)
    if extras:
        raise IntegrityError(
            f"{key}: {', '.join(extras)} is present in the download "
            "directory but not in the pinned manifest. Delete the cache "
            "directory and re-run; do not ingest it."
        )


def verify_shard(
    key: str, name: str, path: Path, hashes: dict[str, str], listed: set[str]
) -> Path:
    """Return `path` once it matches its manifest hash, else delete and raise."""
    try:
        verify_sha256(path, hashes[name], label=f"{key}/{name}")
        _refuse_unlisted(key, path, listed)
    except IntegrityError as exc:
        path.unlink(missing_ok=True)
        raise IntegrityError(
            f"{exc}\n      Nothing was written to Qdrant by this target. "
            "Do NOT ingest this download: it is not the corpus your "
            "repository pins. Delete the cache directory, confirm --repo-id "
            "and --revision are the ones you intended, and report it to the "
            "maintainers if it persists."
        ) from exc
    return path


__all__ = [
    "LOCK_FILE_NAME",
    "REPO_ID_ENV",
    "REVISION_ENV",
    "TrustAnchor",
    "resolve_trust",
    "shard_hashes",
    "verify_shard",
]
