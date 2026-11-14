"""Trust-chain tests for `scripts/fetch_corpus.py`.

The corpus an operator downloads decides what a citizen reads as law AND
which government URL is shown beside it, and the citation validator cannot
see either. These tests pin the chain: repo id and revision are explicit,
the manifest's hash is pinned from git, every shard is checked against the
manifest, and any failure stops the run before Qdrant is written.

Hermetic: HTTP and Qdrant are `httpx.MockTransport`, downloads are a local
dict. No network.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from openlaw.index.publish import (
    ExportTarget,
    IntegrityError,
    point_to_row,
    rows_to_table,
    sha256_file,
)
from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.index.sparse import SPARSE_VECTOR_NAME
from openlaw.index.upsert import point_id

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_corpus = _load("fetch_corpus")

QDRANT = "http://qdrant.test:6333"
SC = ExportTarget(key="sc_judgments_v1", collection="sc_judgments_v1", court_code=None)
REVISION = "a" * 40


def _point(index: int) -> dict[str, Any]:
    chunk_id = f"chunk-{index}"
    return {
        "id": point_id(chunk_id),
        "vector": {
            "dense": [0.01] * EMBEDDING_DIMENSIONS,
            SPARSE_VECTOR_NAME: {"indices": [index], "values": [1.0]},
        },
        "payload": {"chunk_id": chunk_id, "text": f"provision {index}"},
    }


class _Repo:
    """A fake dataset repo: written to disk, served by path, with call log."""

    def __init__(self, root: Path, shard_rows: tuple[int, ...] = (3, 2)) -> None:
        self.root = root
        self.downloads: list[str] = []
        shards = []
        for number, rows in enumerate(shard_rows):
            name = f"shard-{number:05d}.parquet"
            path = root / "repo" / SC.key / name
            path.parent.mkdir(parents=True, exist_ok=True)
            table = rows_to_table([point_to_row(_point(i)) for i in range(rows)])
            pq.write_table(table, path)
            shards.append(
                {"name": name, "rows": rows, "bytes": path.stat().st_size,
                 "sha256": sha256_file(path)}
            )
        self.manifest = {
            "target": SC.key, "collection": SC.collection, "court_code": None,
            "dense_dtype": "float16", "points": sum(shard_rows), "shards": shards,
        }
        self.write_manifest()

    @property
    def manifest_path(self) -> Path:
        return self.root / "repo" / SC.key / "manifest.json"

    def write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2))

    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()

    def download(self, path: str) -> Path:
        self.downloads.append(path)
        return self.root / "repo" / path


class _Qdrant:
    def __init__(self) -> None:
        self.writes = 0
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/points"):
            self.writes += len(json.loads(request.content)["points"])
        return httpx.Response(200, json={"result": {"status": "completed"}})


def _run(repo: _Repo, qdrant: _Qdrant, tmp_path: Path, pins: dict[str, str]) -> None:
    options = fetch_corpus.FetchOptions(
        checkpoint_dir=tmp_path / "ckpt", batch_size=2, keep_downloads=True
    )
    fetch_corpus.fetch_all(
        qdrant.client, QDRANT, (SC,), repo.download, options, "v2026.08", pins
    )


# --- required configuration ---------------------------------------------------


def test_there_is_no_default_repo_id() -> None:
    assert not hasattr(fetch_corpus, "DEFAULT_REPO_ID")


def test_a_missing_repo_id_is_refused_naming_flag_and_env(monkeypatch) -> None:
    monkeypatch.delenv("OPENLAW_CORPUS_REPO_ID", raising=False)
    args = fetch_corpus.build_parser().parse_args([])
    with pytest.raises(IntegrityError, match="--repo-id") as excinfo:
        fetch_corpus.resolve_trust(args.repo_id, args.revision, fetch_corpus.load_corpus_lock(Path("none")))
    assert "OPENLAW_CORPUS_REPO_ID" in str(excinfo.value)


def test_the_repo_id_can_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_CORPUS_REPO_ID", "me/mine")
    monkeypatch.setenv("OPENLAW_CORPUS_REVISION", REVISION)
    args = fetch_corpus.build_parser().parse_args([])
    trust = fetch_corpus.resolve_trust(args.repo_id, args.revision, fetch_corpus.load_corpus_lock(Path("none")))
    assert (trust.repo_id, trust.revision) == ("me/mine", REVISION)


def test_an_unconfigured_revision_is_refused(monkeypatch) -> None:
    monkeypatch.delenv("OPENLAW_CORPUS_REVISION", raising=False)
    args = fetch_corpus.build_parser().parse_args(["--repo-id", "me/mine"])
    with pytest.raises(IntegrityError, match="--revision"):
        fetch_corpus.resolve_trust(args.repo_id, args.revision, fetch_corpus.load_corpus_lock(Path("none")))


def test_a_branch_name_is_not_a_pin(monkeypatch) -> None:
    args = fetch_corpus.build_parser().parse_args(
        ["--repo-id", "me/mine", "--revision", "main"]
    )
    with pytest.raises(IntegrityError, match="revision"):
        fetch_corpus.resolve_trust(args.repo_id, args.revision, fetch_corpus.load_corpus_lock(Path("none")))


def test_the_revision_can_be_pinned_by_the_lock_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENLAW_CORPUS_REVISION", raising=False)
    lock_path = tmp_path / "corpus.lock.json"
    lock_path.write_text(json.dumps({"revision": "v2026.08", "manifests": {}}))
    args = fetch_corpus.build_parser().parse_args(["--repo-id", "me/mine"])
    trust = fetch_corpus.resolve_trust(args.repo_id, args.revision, fetch_corpus.load_corpus_lock(lock_path))
    assert trust.revision == "v2026.08"


def test_main_refuses_before_touching_qdrant_or_the_network(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENLAW_CORPUS_REPO_ID", raising=False)
    monkeypatch.setattr(
        fetch_corpus, "build_index_client", lambda **_: pytest.fail("Qdrant touched")
    )
    monkeypatch.setattr(
        fetch_corpus, "build_downloader", lambda *_: pytest.fail("download built")
    )
    code = fetch_corpus.main(["--collections", "sc_judgments_v1"])
    assert code != 0
    assert "OPENLAW_CORPUS_REPO_ID" in capsys.readouterr().out


# --- the chain: git pin -> manifest -> shards ---------------------------------


def test_a_clean_round_trip_still_works(tmp_path: Path) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    _run(repo, qdrant, tmp_path, {SC.key: repo.manifest_sha256()})
    assert qdrant.writes == 5


def test_a_tampered_shard_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    pins = {SC.key: repo.manifest_sha256()}
    last = repo.root / "repo" / SC.key / "shard-00001.parquet"
    last.write_bytes(last.read_bytes() + b"forged")

    with pytest.raises(IntegrityError, match="shard-00001.parquet") as excinfo:
        _run(repo, qdrant, tmp_path, pins)

    assert qdrant.writes == 0, "no point may reach Qdrant, not even from shard 0"
    assert "do not" in str(excinfo.value).lower() or "delete" in str(excinfo.value).lower()


def test_a_manifest_that_does_not_match_the_git_pin_is_refused(tmp_path: Path) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    pins = {SC.key: repo.manifest_sha256()}
    # An attacker re-hashes every shard and rewrites the manifest to match.
    repo.manifest["shards"][0]["sha256"] = "f" * 64
    repo.write_manifest()

    with pytest.raises(IntegrityError, match="manifest"):
        _run(repo, qdrant, tmp_path, pins)
    assert qdrant.writes == 0
    assert repo.downloads == [f"{SC.key}/manifest.json"], "no shard fetched"


def test_a_target_with_no_manifest_pin_is_refused(tmp_path: Path) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    with pytest.raises(IntegrityError, match="corpus.lock.json"):
        _run(repo, qdrant, tmp_path, {})
    assert qdrant.writes == 0


def test_a_shard_with_no_manifest_hash_is_refused(tmp_path: Path) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    del repo.manifest["shards"][1]["sha256"]
    repo.write_manifest()
    with pytest.raises(IntegrityError, match="shard-00001.parquet"):
        _run(repo, qdrant, tmp_path, {SC.key: repo.manifest_sha256()})
    assert qdrant.writes == 0


def test_a_shard_present_in_the_download_but_not_the_manifest_is_refused(
    tmp_path: Path,
) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    planted = repo.root / "repo" / SC.key / "shard-00099.parquet"
    planted.write_bytes(b"planted")
    with pytest.raises(IntegrityError, match="shard-00099.parquet"):
        _run(repo, qdrant, tmp_path, {SC.key: repo.manifest_sha256()})
    assert qdrant.writes == 0


def test_a_shard_name_that_escapes_the_target_directory_is_refused(
    tmp_path: Path,
) -> None:
    repo, qdrant = _Repo(tmp_path), _Qdrant()
    repo.manifest["shards"][0]["name"] = "../other/shard-00000.parquet"
    repo.write_manifest()
    with pytest.raises(IntegrityError, match="name"):
        _run(repo, qdrant, tmp_path, {SC.key: repo.manifest_sha256()})
    assert qdrant.writes == 0


def test_the_committed_lock_file_parses_and_pins_nothing_unverified() -> None:
    lock = fetch_corpus.load_corpus_lock(_ROOT / "corpus.lock.json")
    for pin in lock.manifests.values():
        assert len(pin) == 64
