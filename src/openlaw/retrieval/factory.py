"""Wires up the configured `QueryEmbedder`.

Binding decision (measured, see `openlaw.retrieval.query`): local voyage-4-nano is
the default, since it shares voyage-4's embedding space closely enough to query
the corpus directly, at no per-query cost and no vendor dependency. The Voyage API
stays available as a configurable alternative for anyone without local compute
headroom.
"""

from openlaw.config import Settings
from openlaw.retrieval.query import QueryEmbedder, VoyageQueryEmbedder

LOCAL_NANO_BACKEND = "local_nano"
VOYAGE_API_BACKEND = "voyage_api"
LOCAL_NANO_INSTALL_COMMAND = 'pip install -e ".[local-nano]"'


def build_query_embedder(settings: Settings) -> QueryEmbedder:
    """Return the `QueryEmbedder` configured by `settings.query_embedder_backend`."""
    if settings.query_embedder_backend == VOYAGE_API_BACKEND:
        return _build_voyage_api_embedder(settings)
    if settings.query_embedder_backend == LOCAL_NANO_BACKEND:
        return _build_local_nano_embedder(settings)
    raise ValueError(
        f"unknown query embedder backend: {settings.query_embedder_backend!r} "
        f"(expected {LOCAL_NANO_BACKEND!r} or {VOYAGE_API_BACKEND!r})"
    )


def _build_voyage_api_embedder(settings: Settings) -> VoyageQueryEmbedder:
    if not settings.voyage_api_key:
        raise ValueError(
            "OPENLAW_VOYAGE_API_KEY is required when "
            f"OPENLAW_QUERY_EMBEDDER_BACKEND={VOYAGE_API_BACKEND!r}"
        )
    return VoyageQueryEmbedder(settings.voyage_api_key)


def _build_local_nano_embedder(settings: Settings) -> QueryEmbedder:
    # torch/transformers/sentence-transformers live in the optional "local-nano"
    # extra (spec §12.7: a voyage_api-only install, or CI, must not pay their
    # ~2GB install cost). Both the import and the model load below can raise
    # ImportError if that extra was never installed -- caught and re-raised with
    # the exact fix, not hidden.
    try:
        from openlaw.retrieval.local_nano import LocalNanoQueryEmbedder

        return LocalNanoQueryEmbedder(model_dir=settings.nano_model_dir)
    except ImportError as exc:
        raise ImportError(
            "The 'local_nano' query embedder backend needs its optional ML "
            "dependencies (torch, transformers, sentence-transformers), which "
            f"are not installed. Install them with:\n\n    {LOCAL_NANO_INSTALL_COMMAND}\n\n"
            "Or set OPENLAW_QUERY_EMBEDDER_BACKEND=voyage_api (with "
            "OPENLAW_VOYAGE_API_KEY) to use the Voyage API instead, with no "
            "local model."
        ) from exc
