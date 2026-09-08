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
