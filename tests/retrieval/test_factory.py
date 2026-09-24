import sys
import types

import pytest

from openlaw.config import Settings
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.query import VoyageQueryEmbedder


def _settings(**overrides) -> Settings:
    return Settings(vaquill_root="/tmp/v", kanoongpt_root="/tmp/k", **overrides)


def test_builds_voyage_api_embedder_when_configured() -> None:
    embedder = build_query_embedder(
        _settings(query_embedder_backend="voyage_api", voyage_api_key="k")
    )

    assert isinstance(embedder, VoyageQueryEmbedder)


def test_voyage_api_backend_requires_an_api_key() -> None:
    """No key means no way to call the API -- fail fast, not with a confusing
    error from deep inside the voyageai client."""
    with pytest.raises(ValueError, match="OPENLAW_VOYAGE_API_KEY"):
        build_query_embedder(
            _settings(query_embedder_backend="voyage_api", voyage_api_key=None)
        )


def test_rejects_an_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown query embedder backend"):
        build_query_embedder(_settings(query_embedder_backend="something_else"))


def test_local_nano_is_the_default_backend_and_is_wired_correctly(monkeypatch) -> None:
    """Binding decision: local nano is the default. Swap in a stub so this test
    does not need the real model or its heavy dependencies installed."""
    created: dict = {}

    class FakeLocalNanoQueryEmbedder:
        def __init__(self, model_dir=None) -> None:
            created["model_dir"] = model_dir

    fake_module = types.SimpleNamespace(
        LocalNanoQueryEmbedder=FakeLocalNanoQueryEmbedder
    )
    monkeypatch.setitem(sys.modules, "openlaw.retrieval.local_nano", fake_module)

    settings = _settings(nano_model_dir="/models/voyage-4-nano")
    assert settings.query_embedder_backend == "local_nano"

    embedder = build_query_embedder(settings)

    assert isinstance(embedder, FakeLocalNanoQueryEmbedder)
    assert str(created["model_dir"]) == "/models/voyage-4-nano"
