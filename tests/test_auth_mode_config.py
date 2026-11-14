"""Plan 7 Task 1: the build-mode flag and the quota rule that hangs off it."""

import pytest
from pydantic import ValidationError

from openlaw.config import Settings


@pytest.fixture(autouse=True)
def _roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    for name in ("OPENLAW_AUTH_ENABLED", "OPENLAW_QUOTA_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def _bare() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_bare_settings_is_the_open_source_mode() -> None:
    settings = _bare()
    assert settings.auth_enabled is False
    assert settings.quota_enabled is False


def test_env_var_turns_auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAW_AUTH_ENABLED", "true")
    assert _bare().auth_enabled is True


def test_auth_on_defaults_quota_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAW_AUTH_ENABLED", "true")
    assert _bare().quota_enabled is True


def test_auth_on_allows_quota_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAW_AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENLAW_QUOTA_ENABLED", "false")
    settings = _bare()
    assert settings.auth_enabled is True
    assert settings.quota_enabled is False


def test_auth_off_with_quota_off_is_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAW_QUOTA_ENABLED", "false")
    assert _bare().quota_enabled is False


def test_quota_without_auth_is_rejected_naming_both_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENLAW_QUOTA_ENABLED", "true")
    with pytest.raises(ValidationError) as excinfo:
        _bare()
    message = str(excinfo.value)
    assert "OPENLAW_QUOTA_ENABLED" in message
    assert "OPENLAW_AUTH_ENABLED" in message
    assert "OPENLAW_QUOTA_ENABLED=false" in message
    assert "OPENLAW_AUTH_ENABLED=true" in message


def test_quota_without_auth_is_rejected_via_constructor() -> None:
    with pytest.raises(ValidationError):
        Settings(quota_enabled=True, auth_enabled=False, _env_file=None)  # type: ignore[call-arg]
