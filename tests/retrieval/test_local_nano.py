import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.retrieval._nano_compat import NanoCompatError
from openlaw.retrieval.local_nano import (
    DEFAULT_DOCUMENT_WORKING_DIR,
    DEFAULT_WORKING_DIR,
    MPS_FALLBACK_ENV_VAR,
    NANO_MODEL_ID,
    LocalNanoDocumentEmbedder,
    LocalNanoQueryEmbedder,
    _download_model_snapshot,
    _encode_documents,
    _encode_query,
    _select_device,
    _truncate_and_renormalise,
)


class FakeModel:
    """Stands in for a loaded SentenceTransformer, recording how it was called."""

    def __init__(self, vector: np.ndarray | None = None) -> None:
        self.calls: list[str] = []
        self._vector = vector if vector is not None else np.ones((1, 2048)) * 2.0

    def encode_query(self, texts: list[str], show_progress_bar: bool = False):
        self.calls.append("query")
        return self._vector

    def encode_document(self, texts: list[str], show_progress_bar: bool = False):
        self.calls.append("document")
        return self._vector


# --- pure helpers ---------------------------------------------------------


def test_truncate_and_renormalise_shrinks_dims_and_restores_unit_norm() -> None:
    """Matryoshka truncation breaks unit norm; cosine scoring against the
    normalised, stored voyage-4 vectors is wrong unless we renormalise."""
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(4, 2048))

    result = _truncate_and_renormalise(vectors, 1024)

    assert result.shape == (4, 1024)
    assert np.allclose(np.linalg.norm(result, axis=1), 1.0, atol=1e-6)


def test_encode_query_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        _encode_query(FakeModel(), "   ", EMBEDDING_DIMENSIONS)


def test_encode_query_uses_the_query_method_not_the_document_one() -> None:
    """Nano applies different prompts to each side -- using the wrong one
    measurably costs accuracy (0.9886 vs 0.9825 cosine, measured)."""
    model = FakeModel()

    _encode_query(model, "what is my notice period", EMBEDDING_DIMENSIONS)

    assert model.calls == ["query"]


def test_encode_query_returns_a_unit_vector_of_the_corpus_dimensionality() -> None:
    vector = _encode_query(FakeModel(), "landlord deposit", EMBEDDING_DIMENSIONS)

    assert len(vector) == EMBEDDING_DIMENSIONS
    assert abs(sum(v * v for v in vector) ** 0.5 - 1.0) < 1e-6


def test_encode_documents_uses_the_document_method_not_the_query_one() -> None:
    """Nano applies different prompts to each side -- using the query prompt
    on document text would measurably cost accuracy, mirroring the query
    side's 0.9886 vs 0.9825 cosine measurement."""
    model = FakeModel()

    _encode_documents(model, ["the writ petition is allowed"], EMBEDDING_DIMENSIONS)

    assert model.calls == ["document"]


def test_encode_documents_returns_one_unit_vector_per_text() -> None:
    vectors = _encode_documents(
        FakeModel(vector=np.ones((2, 2048)) * 3.0), ["a", "b"], EMBEDDING_DIMENSIONS
    )

    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIMENSIONS
        assert abs(sum(v * v for v in vector) ** 0.5 - 1.0) < 1e-6


# --- device selection -------------------------------------------------------


def _fake_torch(cuda_available: bool, mps_available: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda_available),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: mps_available)
        ),
    )


def test_select_device_prefers_cuda_over_mps(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True, True))
    assert _select_device() == "cuda"


def test_select_device_falls_back_to_mps_then_cpu(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, True))
    assert _select_device() == "mps"

    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))
    assert _select_device() == "cpu"


def test_select_device_never_sets_the_mps_fallback_env_var(monkeypatch) -> None:
    """NEVER set PYTORCH_ENABLE_MPS_FALLBACK=1 -- measured 26x slower (2.0 vs 52
    chunks/s) because it silently relocates unsupported ops to CPU."""
    monkeypatch.delenv(MPS_FALLBACK_ENV_VAR, raising=False)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))

    _select_device()

    assert MPS_FALLBACK_ENV_VAR not in os.environ


def test_select_device_warns_if_mps_fallback_already_enabled(monkeypatch, caplog) -> None:
    monkeypatch.setenv(MPS_FALLBACK_ENV_VAR, "1")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))

    with caplog.at_level("WARNING"):
        _select_device()

    assert any(MPS_FALLBACK_ENV_VAR in record.message for record in caplog.records)


# --- fetching weights --------------------------------------------------------


def test_download_model_snapshot_disables_xet_and_uses_the_nano_model_id(
    monkeypatch, tmp_path: Path
) -> None:
    """HF Hub's Xet backend hangs indefinitely on a normal connection; plain
    HTTPS (HF_HUB_DISABLE_XET=1) works."""
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    captured: dict[str, str] = {}

    def _fake_snapshot_download(repo_id: str) -> str:
        captured["repo_id"] = repo_id
        return str(tmp_path)

    fake_hub = types.SimpleNamespace(snapshot_download=_fake_snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    result = _download_model_snapshot()

    assert captured["repo_id"] == NANO_MODEL_ID
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert result == tmp_path


# --- the embedder itself ------------------------------------------------


def _install_fake_model_stack(
    monkeypatch, captured: dict, fail: bool = False
) -> type:
    class FakeSentenceTransformer:
        instances = 0

        def __init__(self, path: str, trust_remote_code: bool, device: str) -> None:
            if fail:
                raise TypeError(
                    "create_causal_mask() got an unexpected keyword argument "
                    "'cache_position'"
                )
            FakeSentenceTransformer.instances += 1
            captured["path"] = path
            captured["device"] = device
            self.max_seq_length = None

        def encode_query(self, texts: list[str], show_progress_bar: bool = False):
            return np.ones((1, 2048))

        def encode_document(self, texts: list[str], show_progress_bar: bool = False):
            return np.ones((len(texts), 2048))

    FakeSentenceTransformer.instances = 0
    fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))
    return FakeSentenceTransformer


def test_local_nano_uses_an_explicit_model_dir_without_downloading(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)

    def _fail_if_called() -> Path:
        raise AssertionError("must not download when model_dir is given")

    monkeypatch.setattr(
        "openlaw.retrieval.local_nano._download_model_snapshot", _fail_if_called
    )

    source = tmp_path / "model"
    source.mkdir()
    LocalNanoQueryEmbedder(model_dir=source, working_dir=tmp_path / "working")

    assert captured["path"] == str(tmp_path / "working")


def test_local_nano_downloads_from_hf_hub_when_no_model_dir_given(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)

    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    called = {"done": False}

    def _fake_download() -> Path:
        called["done"] = True
        return downloaded

    monkeypatch.setattr(
        "openlaw.retrieval.local_nano._download_model_snapshot", _fake_download
    )

    LocalNanoQueryEmbedder(working_dir=tmp_path / "working")

    assert called["done"] is True


def test_local_nano_loads_the_model_exactly_once(monkeypatch, tmp_path: Path) -> None:
    """This runs on the live request path -- reloading per call is not an option."""
    captured: dict = {}
    fake_cls = _install_fake_model_stack(monkeypatch, captured)
    source = tmp_path / "model"
    source.mkdir()

    embedder = LocalNanoQueryEmbedder(
        model_dir=source, working_dir=tmp_path / "working"
    )
    embedder.embed_query("one")
    embedder.embed_query("two")

    assert fake_cls.instances == 1


def test_local_nano_embed_query_returns_the_corpus_dimensionality(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)
    source = tmp_path / "model"
    source.mkdir()

    embedder = LocalNanoQueryEmbedder(
        model_dir=source, working_dir=tmp_path / "working"
    )
    vector = embedder.embed_query("what is my notice period")

    assert len(vector) == EMBEDDING_DIMENSIONS


def test_local_nano_raises_a_clear_error_when_model_loading_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """If transformers moves again and breaks the vendored patch too, this must
    fail loudly rather than silently returning garbage embeddings."""
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured, fail=True)
    source = tmp_path / "model"
    source.mkdir()

    with pytest.raises(NanoCompatError):
        LocalNanoQueryEmbedder(model_dir=source, working_dir=tmp_path / "working")


# --- the document embedder ------------------------------------------------
#
# HC (High Court) judgment chunks are embedded with this instead of the
# Voyage API's voyage-4-lite, so that the default LOCAL nano query embedder
# above and the stored document vectors use the SAME model -- measured: nano
# queries against voyage-4-lite HC documents scored only 15.0% top-1 / 31.7%
# top-10 (median rank 61 of 300), because nano and lite's independent
# approximation errors against full voyage-4 do not cancel against each
# other, even though each aligns well to voyage-4 alone on its own.


def test_local_nano_document_embedder_uses_a_separate_default_working_dir() -> None:
    """Query and document embedders must not share a default working dir --
    `prepare_local_model_dir` rebuilds it from scratch on every construction,
    so two embedders pointed at the same directory would race to delete and
    repopulate it out from under each other."""
    assert DEFAULT_DOCUMENT_WORKING_DIR != DEFAULT_WORKING_DIR


def test_local_nano_document_embedder_uses_an_explicit_model_dir_without_downloading(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)

    def _fail_if_called() -> Path:
        raise AssertionError("must not download when model_dir is given")

    monkeypatch.setattr(
        "openlaw.retrieval.local_nano._download_model_snapshot", _fail_if_called
    )

    source = tmp_path / "model"
    source.mkdir()
    LocalNanoDocumentEmbedder(model_dir=source, working_dir=tmp_path / "working")

    assert captured["path"] == str(tmp_path / "working")


def test_local_nano_document_embedder_loads_the_model_exactly_once(
    monkeypatch, tmp_path: Path
) -> None:
    """Indexing streams many batches -- reloading the model per batch is not
    an option."""
    captured: dict = {}
    fake_cls = _install_fake_model_stack(monkeypatch, captured)
    source = tmp_path / "model"
    source.mkdir()

    embedder = LocalNanoDocumentEmbedder(
        model_dir=source, working_dir=tmp_path / "working"
    )
    embedder.embed_documents(["one"])
    embedder.embed_documents(["two", "three"])

    assert fake_cls.instances == 1


def test_local_nano_document_embedder_returns_one_vector_per_text(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)
    source = tmp_path / "model"
    source.mkdir()

    embedder = LocalNanoDocumentEmbedder(
        model_dir=source, working_dir=tmp_path / "working"
    )
    vectors = embedder.embed_documents(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(vector) == EMBEDDING_DIMENSIONS for vector in vectors)


def test_local_nano_document_embedder_returns_empty_list_for_empty_input(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured)
    source = tmp_path / "model"
    source.mkdir()

    embedder = LocalNanoDocumentEmbedder(
        model_dir=source, working_dir=tmp_path / "working"
    )

    assert embedder.embed_documents([]) == []


def test_local_nano_document_embedder_raises_a_clear_error_when_model_loading_fails(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _install_fake_model_stack(monkeypatch, captured, fail=True)
    source = tmp_path / "model"
    source.mkdir()

    with pytest.raises(NanoCompatError):
        LocalNanoDocumentEmbedder(model_dir=source, working_dir=tmp_path / "working")
