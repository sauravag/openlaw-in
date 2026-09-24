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
