"""Publishing and restoring the OpenLaw corpus as parquet shards.

The corpus this project queries is 79 GB of Qdrant storage across three
collections, which means a stranger with a fresh clone has no path to a
working system unless the vectors themselves are published. This module is
the shared contract between the two halves of that path:

- `scripts/export_corpus.py` reads Qdrant and writes parquet shards here.
- `scripts/fetch_corpus.py` downloads those shards and writes Qdrant.

Both halves must agree on the row shape, the shard naming, the collection
geometry and the checkpoint format, so all four live here rather than being
spelled twice and drifting.

Three decisions are load-bearing:

1. **Dense vectors are stored float16, not float32.** The vectors are
   unit-norm (see `EMBEDDING_COMPATIBILITY_WARNING`) with no component
   larger than ~0.15, so every value sits comfortably inside float16's
   normal range and keeps ~11 bits of mantissa. Measured over 6,000 random
   points drawn from all three live collections, the worst float32 ->
   float16 -> float32 round-trip cosine similarity was **0.9999995**, and
   the worst change to any query-document score was 1.2e-4 -- far below the
   gap between adjacent search results. Halving the dominant cost is worth
   that. `float16_worst_cosine` is the measurement, kept in code so a
   publisher can re-run it rather than trusting this paragraph.

2. **The payload is one JSON string column, not a spread of typed columns.**
   `acts_india_v1` carries the publisher's own statute schema and the two
   judgment collections carry this project's (`openlaw.index.upsert
   .build_point`). A JSON column is lossless for both and cannot silently
   drop a field a future snapshot adds.

3. **`acts_india` is never published.** It is the publisher's irreplaceable
   snapshot holding their vectors; `acts_india_v1` is this project's
   citable-only derivative of it (see `scripts/build_sparse_vectors.py`) and
   is the one retrieval actually reads. `resolve_targets` and
   `build_published_collection_payload` both refuse the name outright.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from openlaw.corpus.high_courts import HIGH_COURTS
from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.index.qdrant_restore import CollectionSpec, build_collection_payload
from openlaw.index.sparse import SPARSE_VECTOR_NAME
from openlaw.index.upsert import point_id

DENSE_VECTOR_NAME = "dense"

#: The warning that must travel with the data, in every surface that hands
#: these vectors to someone: the dataset card, `scripts/fetch_corpus.py`'s
#: `--help` and docstring, and the README. Defined once here so the three
#: cannot drift apart or quietly lose the number that makes it urgent.
EMBEDDING_COMPATIBILITY_WARNING = """\
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
responsible for proving it produces the same vector space.\
"""


@dataclass(frozen=True)
class PublishedCollection:
    """A Qdrant collection published as a Hugging Face dataset.

    `shards` is the live collection's own `shard_number`, reproduced so a
    restored collection has the same geometry as the one the corpus was
    measured and validated against.
    """

    name: str
    shards: int


PUBLISHED_COLLECTIONS: dict[str, PublishedCollection] = {
    "acts_india_v1": PublishedCollection(name="acts_india_v1", shards=2),
    "sc_judgments_v1": PublishedCollection(name="sc_judgments_v1", shards=1),
    "hc_judgments_v1": PublishedCollection(name="hc_judgments_v1", shards=1),
}

HC_COLLECTION_NAME = "hc_judgments_v1"

#: The High Courts actually built and therefore actually publishable, in
#: build order. `openlaw.corpus.high_courts.HIGH_COURTS` registers 25 courts
#: because registration is what makes a court *queryable*; only these
#: finished ones are publishable. A court still building is left out:
#: exporting it would publish a partial shard set that looks like coverage
#: and is not.
PUBLISHED_COURT_SLUGS: tuple[str, ...] = (
    "punjab-and-haryana",
    "delhi",
    "bombay",
    "karnataka",
    "madras",
    "patna",
)

#: Measured point counts, read from the live collections rather than
#: estimated (`POST /collections/<name>/points/count` with `exact: true`,
#: filtered on `court_code` for the High Courts; that filter needs the
#: `court_code` payload index, without which the count scans every point and
#: times out -- see `openlaw.index.hc_upsert.COURT_CODE_INDEX`). Used for progress
#: percentages and for the honest per-court sizes the README and the dataset
#: card quote.
PUBLISHED_POINT_COUNTS: dict[str, int] = {
    "acts_india_v1": 961_136,
    "sc_judgments_v1": 371_247,
    "hc_judgments_v1/punjab-and-haryana": 1_845_344,
    "hc_judgments_v1/delhi": 1_053_138,
    "hc_judgments_v1/bombay": 2_788_379,
    "hc_judgments_v1/karnataka": 1_467_133,
    "hc_judgments_v1/madras": 2_610_454,
    "hc_judgments_v1/patna": 2_555_138,
}

_UNSAFE_PATH_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ExportTarget:
    """One publishable slice: a whole collection, or one court within one.

    `key` doubles as the dataset's directory prefix (`sc_judgments_v1`,
    `hc_judgments_v1/karnataka`), so a user who wants statutes plus the
    Supreme Court plus Karnataka downloads three prefixes and skips the
    2.8M Bombay judgments entirely.
    """

    key: str
    collection: str
    court_code: str | None

    @property
    def slug(self) -> str:
        """Return `key` flattened to one filesystem-safe path segment."""
        return _UNSAFE_PATH_CHARACTERS.sub("_", self.key)

    def shard_dir(self, root: Path) -> Path:
        """Return the directory holding this target's parquet shards."""
        return root.joinpath(*self.key.split("/"))

    def checkpoint_path(self, root: Path) -> Path:
        """Return this target's checkpoint file, distinct from every other's."""
        return root / f"corpus_{self.slug}_checkpoint.json"

    @property
    def expected_points(self) -> int:
        """Return the measured point count, or 0 when unmeasured."""
        return PUBLISHED_POINT_COUNTS.get(self.key, 0)


def _high_court_target(slug: str) -> ExportTarget:
    """Return the export target for one published High Court."""
    return ExportTarget(
        key=f"{HC_COLLECTION_NAME}/{slug}",
        collection=HC_COLLECTION_NAME,
        court_code=HIGH_COURTS[slug].vaquill_court_code,
    )


def _collection_refusal(name: str) -> str:
    """Return the error text for an unpublishable collection name."""
    known = ", ".join(PUBLISHED_COLLECTIONS)
    message = f"unknown collection {name!r}; publishable collections: {known}"
    if name == "acts_india":
        message += (
            " -- `acts_india` is the publisher's own snapshot and is never "
            "redistributed; `acts_india_v1` is the citable derivative "
            "retrieval actually reads"
        )
    return message


def _validate_collections(collections: Sequence[str] | None) -> tuple[str, ...]:
    """Return the requested collection names, refusing anything unpublished."""
    if not collections:
        return tuple(PUBLISHED_COLLECTIONS)
    for name in collections:
        if name not in PUBLISHED_COLLECTIONS:
            raise ValueError(_collection_refusal(name))
    return tuple(dict.fromkeys(collections))


def _validate_courts(courts: Sequence[str] | None) -> tuple[str, ...]:
    """Return the requested court slugs, refusing anything unbuilt."""
    if not courts:
        return PUBLISHED_COURT_SLUGS
    known = ", ".join(PUBLISHED_COURT_SLUGS)
    for slug in courts:
        if slug not in PUBLISHED_COURT_SLUGS:
            registered = " (registered but not built yet)" if slug in HIGH_COURTS else ""
            raise ValueError(
                f"no published corpus for court {slug!r}{registered}; "
                f"published courts: {known}"
            )
    wanted = set(courts)
    return tuple(slug for slug in PUBLISHED_COURT_SLUGS if slug in wanted)


def resolve_targets(
    collections: Sequence[str] | None, courts: Sequence[str] | None
) -> tuple[ExportTarget, ...]:
    """Return the export targets selected by `collections` and `courts`.

    Both default to everything. Naming any court implies the High Court
    collection and only it, so `--courts karnataka` is a complete request
    rather than one that silently also drags in statutes and the Supreme
    Court. Ordering is always the canonical one (statutes, Supreme Court,
    then courts in build order), not the order the caller happened to type.
    """
    if courts and not collections:
        collections = [HC_COLLECTION_NAME]
    selected = set(_validate_collections(collections))
    court_slugs = _validate_courts(courts)

    targets: list[ExportTarget] = []
    for name in PUBLISHED_COLLECTIONS:
        if name not in selected:
            continue
        if name == HC_COLLECTION_NAME:
            targets.extend(_high_court_target(slug) for slug in court_slugs)
        else:
            targets.append(ExportTarget(key=name, collection=name, court_code=None))
    return tuple(targets)


def court_code_filter(court_code: str | None) -> dict[str, Any] | None:
    """Return the Qdrant filter selecting one court's points, or None.

    Filters on the same `court_code` payload field that
    `openlaw.index.hc_upsert.delete_hc_court_points` and retrieval's
    jurisdiction gating use -- the vaquill spelling (`29_3`), as stored.
    """
    if court_code is None:
        return None
    return {"must": [{"key": "court_code", "match": {"value": court_code}}]}


def shard_name(index: int) -> str:
    """Return the parquet filename for shard `index`, zero-padded to sort."""
    return f"shard-{index:05d}.parquet"


def export_schema() -> pa.Schema:
    """Return the parquet schema every published shard is written with."""
    return pa.schema(
        [
            pa.field("chunk_id", pa.string(), nullable=False),
            pa.field(
                "dense",
                pa.list_(pa.float16(), EMBEDDING_DIMENSIONS),
                nullable=False,
            ),
            pa.field("sparse_indices", pa.list_(pa.uint32()), nullable=False),
            pa.field("sparse_values", pa.list_(pa.float32()), nullable=False),
            pa.field("payload", pa.string(), nullable=False),
        ]
    )


def point_to_row(point: Mapping[str, Any]) -> dict[str, Any]:
    """Return the parquet row for one scrolled Qdrant point.

    Refuses a point missing `chunk_id` or carrying the wrong dense width
    rather than publishing a row no restore can use: `chunk_id` is what the
    deterministic point ID is derived from (`openlaw.index.upsert.point_id`),
    so a row without one is unrestorable, and a width mismatch means the
    point was embedded by something other than this corpus's embedder.
    """
    payload = dict(point.get("payload") or {})
    chunk_id = payload.get("chunk_id")
    if not chunk_id:
        raise ValueError(
            f"point {point.get('id')!r} has no chunk_id payload field; "
            "it cannot be restored and is refused rather than published"
        )
    vectors = point.get("vector") or {}
    dense = vectors.get(DENSE_VECTOR_NAME) or []
    if len(dense) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"point {chunk_id!r} has a {len(dense)}-dimension dense vector; "
            f"this corpus is {EMBEDDING_DIMENSIONS}-dimension voyage-4-nano"
        )
    sparse = vectors.get(SPARSE_VECTOR_NAME) or {}
    return {
        "chunk_id": str(chunk_id),
        "dense": np.asarray(dense, dtype=np.float16),
        "sparse_indices": [int(index) for index in sparse.get("indices", [])],
        "sparse_values": [float(value) for value in sparse.get("values", [])],
        "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


def _dense_matrix(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """Return `rows`' dense vectors as one `(n, 1024)` float16 matrix."""
    if not rows:
        return np.zeros((0, EMBEDDING_DIMENSIONS), dtype=np.float16)
    return np.stack([np.asarray(row["dense"], dtype=np.float16) for row in rows])


def rows_to_table(rows: Sequence[Mapping[str, Any]]) -> pa.Table:
    """Return the parquet table for `rows`, under `export_schema`."""
    dense = _dense_matrix(rows)
    return pa.Table.from_arrays(
        [
            pa.array([row["chunk_id"] for row in rows], type=pa.string()),
            pa.FixedSizeListArray.from_arrays(
                pa.array(dense.reshape(-1), type=pa.float16()), EMBEDDING_DIMENSIONS
            ),
            pa.array([row["sparse_indices"] for row in rows], type=pa.list_(pa.uint32())),
            pa.array([row["sparse_values"] for row in rows], type=pa.list_(pa.float32())),
            pa.array([row["payload"] for row in rows], type=pa.string()),
        ],
        schema=export_schema(),
    )


def row_to_point(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Qdrant point to upsert for one published parquet row.

    The point ID is re-derived from `chunk_id` rather than published, so a
    restore produces byte-identical IDs to a local build and a re-run
    updates points instead of duplicating them. Every value is converted to
    a plain Python scalar: numpy types are not JSON-serialisable, and the
    Qdrant surface here is raw HTTP (`openlaw.index.qdrant_http`).
    """
    chunk_id = str(row["chunk_id"])
    dense = [float(value) for value in np.asarray(row["dense"], dtype=np.float32)]
    sparse = {
        "indices": [int(index) for index in row["sparse_indices"]],
        "values": [float(value) for value in row["sparse_values"]],
    }
    return {
        "id": point_id(chunk_id),
        "vector": {DENSE_VECTOR_NAME: dense, SPARSE_VECTOR_NAME: sparse},
        "payload": json.loads(row["payload"]),
    }


def float16_worst_cosine(vectors: np.ndarray) -> float:
    """Return the worst float32 -> float16 -> float32 cosine over `vectors`.

    This is the empirical check behind storing dense vectors as float16. It
    returns NaN for an empty sample rather than a perfect 1.0, so an export
    that sampled nothing cannot report a pass it never measured.
    """
    original = np.asarray(vectors, dtype=np.float32)
    if original.size == 0:
        return float("nan")
    # A component outside float16's range overflows to inf, which makes the
    # cosine NaN. That is a real answer -- "float16 destroys this vector
    # space" -- so the warnings are silenced while the NaN is kept, and
    # every caller must treat NaN as a failure, never as a pass.
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        round_trip = original.astype(np.float16).astype(np.float32)
        dot = np.einsum("ij,ij->i", original, round_trip)
        norms = np.linalg.norm(original, axis=1) * np.linalg.norm(round_trip, axis=1)
        cosines = np.divide(dot, norms, out=np.full_like(dot, np.nan), where=norms > 0)
    return float(np.min(cosines))


def build_published_collection_payload(collection: str) -> dict[str, Any]:
    """Return the Qdrant create-collection body for a published collection.

    Reuses `openlaw.index.qdrant_restore.build_collection_payload` for the
    dense geometry -- 1024 dimensions, cosine, on-disk, int8-quantised --
    rather than respelling it, then declares the sparse vector's `idf`
    modifier. That modifier is not cosmetic: stored sparse values carry only
    BM25's term-frequency component (see `openlaw.index.sparse`), so without
    it Qdrant never supplies the inverse document frequency and hybrid
    search scores wrongly, silently, with no error anywhere.
    """
    if collection not in PUBLISHED_COLLECTIONS:
        known = ", ".join(PUBLISHED_COLLECTIONS)
        raise ValueError(
            f"{collection!r} is not a published collection; publishable: {known}"
        )
    spec = CollectionSpec(
        name=collection, shards=PUBLISHED_COLLECTIONS[collection].shards, mirror=""
    )
    payload = build_collection_payload(spec)
    payload["sparse_vectors"] = {SPARSE_VECTOR_NAME: {"modifier": "idf"}}
    return payload


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    """Return the checkpoint at `path`, or None when it does not exist.

    A present-but-unparseable checkpoint raises rather than being treated as
    absent: silently restarting a 7M-point export from zero because a crash
    tore the file is the opposite of what a checkpoint is for.
    """
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_checkpoint(path: Path, state: Mapping[str, Any]) -> None:
    """Record `state` at `path` atomically, via a temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(dict(state), indent=2, sort_keys=True))
    temporary.replace(path)


# --- integrity: the git repository is the root of trust -----------------------

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HASH_CHUNK_BYTES = 1024 * 1024

#: Names that move. A revision spelled like one of these means "whatever is
#: there today", which is exactly what a squatter or a compromised account
#: controls. Compared case-insensitively.
FLOATING_REVISIONS = frozenset({"main", "master", "head", "latest", "dev"})

LOCK_FILE_NAME = "corpus.lock.json"


class IntegrityError(ValueError):
    """The downloaded corpus cannot be shown to be the one the repo pins.

    Raised, never logged: a corpus that fails verification can forge both the
    text presented as law and the government URL cited beside it, and the
    citation validator cannot see either. Callers must let it stop the run.
    """


@dataclass(frozen=True)
class CorpusLock:
    """What the git repository pins about the corpus.

    `manifests` maps a target key to the SHA-256 of that target's
    `manifest.json`; the manifest in turn carries one SHA-256 per shard, so
    the chain terminates in a file reviewed and versioned here rather than in
    anything the download supplies.
    """

    revision: str | None
    manifests: Mapping[str, str]


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of the file at `path`, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256_hex(value: object, label: str) -> str:
    """Return `value` if it is a lowercase 64-character hex digest, else raise."""
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise IntegrityError(
            f"{label}: expected a 64-character lowercase hex SHA-256, got {value!r}"
        )
    return value


def verify_sha256(path: Path, expected: object, label: str) -> None:
    """Raise `IntegrityError` unless the file at `path` hashes to `expected`."""
    wanted = require_sha256_hex(expected, label)
    actual = sha256_file(path)
    if actual != wanted:
        raise IntegrityError(
            f"{label}: SHA-256 mismatch.\n"
            f"      expected {wanted}\n"
            f"      actual   {actual}"
        )


def require_pinned_revision(revision: str | None) -> str:
    """Return `revision` if it names a fixed commit or tag, else raise."""
    if not revision or revision.lower() in FLOATING_REVISIONS:
        raise IntegrityError(
            f"revision {revision!r} is not pinned: a branch name means "
            "'whatever is there today'. Pass a commit hash or release tag "
            f"with --revision (or OPENLAW_CORPUS_REVISION, or `revision` in "
            f"{LOCK_FILE_NAME})."
        )
    return revision


def load_corpus_lock(path: Path) -> CorpusLock:
    """Return the lock file at `path`; an absent file pins nothing."""
    if not path.exists():
        return CorpusLock(revision=None, manifests={})
    raw = json.loads(path.read_text())
    manifests = dict(raw.get("manifests") or {})
    for key, value in manifests.items():
        require_sha256_hex(value, f"{path.name} manifest pin for {key}")
    return CorpusLock(revision=raw.get("revision"), manifests=manifests)
