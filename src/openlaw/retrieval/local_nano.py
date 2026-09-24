"""Local voyage-4-nano embedders -- query (the default `QueryEmbedder`) and
document (the HC judgment-chunk `Embedder`).

`LocalNanoQueryEmbedder` runs on the live request path for every question, so
its model is loaded once (at construction time) and reused for every
`embed_query` call. `LocalNanoDocumentEmbedder` is its document-side sibling,
used by `scripts/build_hc_index.py` in place of the Voyage API's
voyage-4-lite -- so the same model embeds both sides of High Court retrieval.
Measured: nano queries against voyage-4-lite HC documents scored only 15.0%
top-1 / 31.7% top-10 (median rank 61 of 300); nano and lite's independent
approximation errors against full voyage-4 do not cancel against each other,
even though each aligns well to voyage-4 alone.

Both embedders share model loading, device selection, and the truncate/
renormalise step below -- only the prompt side (`encode_query` vs
`encode_document`) and the default working directory differ.

Three things that WILL silently break this if skipped -- see the module the
constant/function is named after for the measured numbers behind each:

  1. Dimensions: nano's native output is 2048-d; the corpus is 1024-d. Matryoshka
     truncation to the first 1024 dims breaks unit norm, so `_truncate_and_renormalise`
     renormalises before the vector is ever compared against the stored (also
     normalised) voyage-4 document vectors.
  2. Prompts: nano applies distinct query/document prompts, only reachable through
     sentence-transformers' `encode_query()`/`encode_document()` -- never the plain
     `.encode()`. `_encode_query` always calls `encode_query`.
  3. Remote code compatibility: see `openlaw.retrieval._nano_compat`.

Two more, purely operational:

  - NEVER set `PYTORCH_ENABLE_MPS_FALLBACK=1`. It silently relocates unsupported
    ops to CPU and measured 26x slower (2.0 vs 52 chunks/s) than native MPS.
    `_select_device` never sets it, and warns if something else already has.
  - Hugging Face's Xet download backend hangs indefinitely on a normal connection;
    `_download_model_snapshot` sets `HF_HUB_DISABLE_XET=1` before fetching weights.
"""

import logging
import os
from pathlib import Path

import numpy as np

from openlaw.index.embed import EMBEDDING_DIMENSIONS
from openlaw.retrieval._nano_compat import NanoCompatError, prepare_local_model_dir

logger = logging.getLogger(__name__)

NANO_MODEL_ID = "voyageai/voyage-4-nano"
MAX_QUERY_SEQ_LENGTH = 8192  # native 32768; no user question comes close
# HC judgment chunks measure ~908 tokens/chunk on average (p99 ~1,055 within a
# 128-row batch, see openlaw.index.embed) -- 8192 stays comfortably clear of
# that with the same headroom already proven safe for the query side.
MAX_DOCUMENT_SEQ_LENGTH = 8192
MPS_FALLBACK_ENV_VAR = "PYTORCH_ENABLE_MPS_FALLBACK"
XET_DISABLE_ENV_VAR = "HF_HUB_DISABLE_XET"
DEFAULT_WORKING_DIR = Path.home() / ".cache" / "openlaw" / "voyage-4-nano"
# Separate from the query embedder's working dir -- prepare_local_model_dir
# rebuilds its working dir from scratch on every construction, so query and
# document embedders sharing one would race to delete/repopulate it.
DEFAULT_DOCUMENT_WORKING_DIR = Path.home() / ".cache" / "openlaw" / "voyage-4-nano-document"


def _truncate_and_renormalise(vectors: np.ndarray, dimensions: int) -> np.ndarray:
    """Truncate to `dimensions` and restore unit norm for cosine scoring."""
    truncated = vectors[:, :dimensions]
    norms = np.linalg.norm(truncated, axis=1, keepdims=True)
    return truncated / norms


def _encode_query(model, text: str, dimensions: int) -> list[float]:
    """Embed one query with nano's query prompt, truncated and renormalised."""
    if not text or not text.strip():
        raise ValueError("cannot embed an empty query")
    raw = np.asarray(model.encode_query([text], show_progress_bar=False))
    return _truncate_and_renormalise(raw, dimensions)[0].tolist()


def _encode_documents(model, texts: list[str], dimensions: int) -> list[list[float]]:
    """Embed `texts` with nano's document prompt, truncated and renormalised.

    Uses `encode_document`, not the plain `.encode()` -- nano applies a
    distinct prompt per side, only reachable through sentence-transformers'
    dedicated `encode_query()`/`encode_document()` methods.
    """
    raw = np.asarray(model.encode_document(texts, show_progress_bar=False))
    return _truncate_and_renormalise(raw, dimensions).tolist()


def _warn_if_mps_fallback_enabled() -> None:
    """Warn (never silently accept) if PYTORCH_ENABLE_MPS_FALLBACK=1 is already set."""
    if os.environ.get(MPS_FALLBACK_ENV_VAR) == "1":
        logger.warning(
            "%s=1 is set in the environment; this silently relocates unsupported "
            "ops to CPU and measured 26x slower (2.0 vs 52 chunks/s) than native "
            "MPS. Unset it for query-embedding latency.",
            MPS_FALLBACK_ENV_VAR,
        )


def _select_device() -> str:
    """Prefer CUDA, then Apple's MPS, then CPU -- never force a silent fallback."""
    import torch

    _warn_if_mps_fallback_enabled()
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _download_model_snapshot() -> Path:
    """Fetch voyage-4-nano's weights from the Hugging Face Hub.

    HF Hub's Xet backend hangs indefinitely on a normal connection; disabling it
    falls back to plain HTTPS, which works.
    """
    os.environ.setdefault(XET_DISABLE_ENV_VAR, "1")
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(NANO_MODEL_ID))


def _load_model(model_dir: Path | None, working_dir: Path, max_seq_length: int):
    """Load voyage-4-nano, always through our patched copy of its remote code.

    Shared by both `LocalNanoQueryEmbedder` and `LocalNanoDocumentEmbedder` --
    `max_seq_length` is the only thing that differs between the two calls.
    """
    source_dir = model_dir if model_dir is not None else _download_model_snapshot()
    prepared_dir = prepare_local_model_dir(source_dir, working_dir)
    device = _select_device()

    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(
            str(prepared_dir), trust_remote_code=True, device=device
        )
    except Exception as exc:  # noqa: BLE001 - re-raised loudly as NanoCompatError
        raise NanoCompatError(
            "Failed to load voyage-4-nano with the installed transformers/"
            "sentence-transformers versions. Its remote code has needed hand "
            "patching before (see openlaw.retrieval._nano_compat) -- if a library "
            "upgrade moved the goalposts again, that patch needs updating."
        ) from exc

    model.max_seq_length = max_seq_length
    return model


class LocalNanoQueryEmbedder:
    """The default `QueryEmbedder`: voyage-4-nano run locally, no API call.

    Shares voyage-4's embedding space closely enough to query its document
    vectors directly (measured: 0.951 mean cosine for the same text, 85% top-10
    retrieval either way), so this removes the API key, per-query cost, and
    vendor dependency from the serving path.

    Loads the model once at construction time. Pass `model_dir` to use an
    already-downloaded copy (e.g. baked into a container image); otherwise the
    weights are fetched from the Hugging Face Hub on first use.
    """

    def __init__(
        self,
        model_dir: Path | None = None,
        dimensions: int = EMBEDDING_DIMENSIONS,
        working_dir: Path | None = None,
    ) -> None:
        self._model = _load_model(
            model_dir, working_dir or DEFAULT_WORKING_DIR, MAX_QUERY_SEQ_LENGTH
        )
        self._dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        """Return the query vector for `text`, at the corpus dimensionality."""
        return _encode_query(self._model, text, self._dimensions)


class LocalNanoDocumentEmbedder:
    """An `Embedder` (see `openlaw.index.embed`): voyage-4-nano run locally
    for documents -- the document-side counterpart to `LocalNanoQueryEmbedder`.

    `scripts/build_hc_index.py` uses this in place of the Voyage API's
    voyage-4-lite, so both sides of High Court retrieval -- the default local
    nano query embedder and the stored document vectors -- use the SAME
    model. See the module docstring for why that matters: nano and
    voyage-4-lite each align well to full voyage-4 individually, but their
    independent approximation errors do not cancel against each other.

    Loads the model once at construction time, reused for every
    `embed_documents` call -- indexing streams many batches, so reloading per
    batch is not an option. Pass `model_dir` to use an already-downloaded
    copy; otherwise the weights are fetched from the Hugging Face Hub on
    first use.
    """

    def __init__(
        self,
        model_dir: Path | None = None,
        dimensions: int = EMBEDDING_DIMENSIONS,
        working_dir: Path | None = None,
    ) -> None:
        self._model = _load_model(
            model_dir,
            working_dir or DEFAULT_DOCUMENT_WORKING_DIR,
            MAX_DOCUMENT_SEQ_LENGTH,
        )
        self._dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one document vector per text, in the same order.

        Returns `[]` for an empty `texts` list rather than calling into the
        model with nothing to embed.
        """
        if not texts:
            return []
        return _encode_documents(self._model, texts, self._dimensions)
