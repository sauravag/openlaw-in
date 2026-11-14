"""`/auth/status`, the sign-in-unavailable refusal, and the startup refusal.

`/auth/status` must answer in BOTH modes: a Mode A backend answering
`auth_enabled: false` is precisely how a frontend built with sign-in detects
that it was built for the wrong backend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openlaw.api.deps import _require_auth_configuration
from openlaw.config import Settings
from tests.api.conftest import JWT_SECRET, api_settings, build_client

SUPABASE_URL = "https://abcdefgh.supabase.co"


def _mode_a(tmp_path: Path):
    return build_client(
        tmp_path,
        api_settings(
            tmp_path, auth_enabled=False, quota_enabled=False, supabase_jwt_secret=None
        ),
    )


def _mode_b(tmp_path: Path):
    return build_client(
        tmp_path, api_settings(tmp_path, supabase_url=SUPABASE_URL)
    )


def test_auth_status_reports_disabled_without_a_token_in_mode_a(tmp_path: Path) -> None:
    response = _mode_a(tmp_path).get("/auth/status")

    assert response.status_code == 200
    assert response.json() == {"auth_enabled": False, "supabase_url": None}


def test_auth_status_reports_enabled_and_url_in_mode_b(tmp_path: Path) -> None:
    response = _mode_b(tmp_path).get("/auth/status")

    assert response.status_code == 200
    assert response.json() == {"auth_enabled": True, "supabase_url": SUPABASE_URL}


def test_auth_status_never_serialises_the_jwt_secret(tmp_path: Path) -> None:
    response = _mode_b(tmp_path).get("/auth/status")

    assert JWT_SECRET not in response.text
    assert "secret" not in json.dumps(response.json()).lower()


@pytest.mark.parametrize("language", ["en", "hi"])
def test_auth_unavailable_refusal_carries_help_in_both_modes(
    tmp_path: Path, language: str
) -> None:
    for client in (_mode_a(tmp_path), _mode_b(tmp_path)):
        response = client.get("/auth/unavailable", params={"language": language})

        body = response.json()
        assert response.status_code == 503
        assert body["detail"]
        assert body["escalation_footer"]
        assert any("15100" in line["number"] for line in body["helplines"])


def test_auth_unavailable_is_localised(tmp_path: Path) -> None:
    client = _mode_a(tmp_path)

    english = client.get("/auth/unavailable", params={"language": "en"}).json()
    hindi = client.get("/auth/unavailable", params={"language": "hi"}).json()

    assert english["detail"] != hindi["detail"]


# --- startup coherence ----------------------------------------------------


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"vaquill_root": "/tmp/v", "kanoongpt_root": "/tmp/k"}
    return Settings(**{**defaults, **overrides})


def test_require_auth_configuration_ignores_mode_a() -> None:
    _require_auth_configuration(_settings(auth_enabled=False))


def test_require_auth_configuration_accepts_a_complete_mode_b() -> None:
    _require_auth_configuration(
        _settings(
            auth_enabled=True, supabase_url=SUPABASE_URL, supabase_jwt_secret=JWT_SECRET
        )
    )


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"supabase_url": None, "supabase_jwt_secret": JWT_SECRET}, "OPENLAW_SUPABASE_URL"),
        ({"supabase_url": "", "supabase_jwt_secret": JWT_SECRET}, "OPENLAW_SUPABASE_URL"),
        (
            {"supabase_url": SUPABASE_URL, "supabase_jwt_secret": None},
            "OPENLAW_SUPABASE_JWT_SECRET",
        ),
        (
            {"supabase_url": SUPABASE_URL, "supabase_jwt_secret": "short-secret-value"},
            "OPENLAW_SUPABASE_JWT_SECRET",
        ),
    ],
)
def test_require_auth_configuration_names_the_missing_variable_without_values(
    overrides: dict[str, object], named: str
) -> None:
    settings = _settings(auth_enabled=True, **overrides)

    with pytest.raises(RuntimeError, match=named) as raised:
        _require_auth_configuration(settings)

    assert "short-secret-value" not in str(raised.value)
    assert JWT_SECRET not in str(raised.value)


def test_require_auth_configuration_names_both_when_both_missing() -> None:
    with pytest.raises(RuntimeError) as raised:
        _require_auth_configuration(_settings(auth_enabled=True))

    assert "OPENLAW_SUPABASE_URL" in str(raised.value)
    assert "OPENLAW_SUPABASE_JWT_SECRET" in str(raised.value)
