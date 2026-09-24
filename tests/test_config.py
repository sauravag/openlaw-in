from pathlib import Path

from openlaw.config import Settings


def test_settings_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_QDRANT_URL", "http://localhost:6333")

    settings = Settings()

    assert settings.vaquill_root == Path("/tmp/vaquill")
    assert settings.kanoongpt_root == Path("/tmp/kanoon")
    assert settings.qdrant_url == "http://localhost:6333"


def test_corpus_version_defaults_to_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().corpus_version == "v2026.08"


def test_query_embedder_backend_defaults_to_local_nano(monkeypatch) -> None:
    """Binding decision (Plan 3 T1, measured): local nano, not the Voyage API."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().query_embedder_backend == "local_nano"


def test_nano_model_dir_defaults_to_none(monkeypatch) -> None:
    """Unset means fetch from the Hugging Face Hub on demand."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().nano_model_dir is None


def test_nano_model_dir_reads_a_configured_local_path(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_NANO_MODEL_DIR", "/models/voyage-4-nano")

    assert Settings().nano_model_dir == Path("/models/voyage-4-nano")


def test_graph_expansion_is_disabled_by_default(monkeypatch) -> None:
    """Binding decision (Plan 3 T3, fix round 2, measured): the statute-to-
    case-law bridge is too sparse and REFERENCES fans out through shared-Act
    supernodes, so every added chunk being citable makes ON the unsafe
    default -- see openlaw.retrieval.rerank's module docstring."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().graph_expansion_enabled is False


def test_graph_expansion_can_be_enabled_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_GRAPH_EXPANSION_ENABLED", "true")

    assert Settings().graph_expansion_enabled is True


def test_model_defaults_match_the_per_role_spec(monkeypatch) -> None:
    """Binding decision (Plan 3 T4): the generator needs frontier reasoning for
    grounded legal answers; translation and classification do not, and paying
    frontier prices for them is waste at the product's projected scale."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    settings = Settings()

    assert settings.model_generator == "claude-opus-5"
    assert settings.model_translator == "claude-haiku-4-5"
    assert settings.model_classifier == "claude-haiku-4-5"


def test_model_roles_can_be_overridden_independently(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "claude-sonnet-5")

    settings = Settings()

    assert settings.model_generator == "claude-sonnet-5"
    assert settings.model_translator == "claude-haiku-4-5"


def test_anthropic_api_key_defaults_to_none(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().anthropic_api_key is None


def test_anthropic_api_key_reads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_ANTHROPIC_API_KEY", "sk-ant-test")

    assert Settings().anthropic_api_key == "sk-ant-test"


def test_hallucination_log_path_has_a_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().hallucination_log_path == Path("data/hallucinations.jsonl")


def test_hallucination_log_path_is_configurable_via_environment(monkeypatch) -> None:
    """Plan 3 T7 fix round 1: the path must not be hard-coded at a call
    site, because the log can carry verbatim, sensitive user text and a
    deployment may need it on a restricted-access volume."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_HALLUCINATION_LOG_PATH", "/secure/hallucinations.jsonl")

    assert Settings().hallucination_log_path == Path("/secure/hallucinations.jsonl")
