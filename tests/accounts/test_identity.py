"""Tests for the GoTrue admin identity removal (httpx mocked, no network)."""

from __future__ import annotations

import logging

import httpx
import pytest

from openlaw.accounts import identity
from openlaw.accounts.identity import remove_supabase_identity
from openlaw.config import Settings

KEY = "service-role-key-DO-NOT-LEAK"
URL = "https://abcdefgh.supabase.co"
SUB = "11111111-2222-4333-8444-555555555555"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "supabase_url": URL,
        "supabase_service_role_key": KEY,
    }
    return Settings(**{**base, **overrides})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deletes_the_identity_with_the_service_key_and_reports_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    assert remove_supabase_identity(_settings(), SUB, client=_client(handler)) is True
    request = seen[0]
    assert request.method == "DELETE"
    assert str(request.url) == f"{URL}/auth/v1/admin/users/{SUB}"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["apikey"] == KEY


def test_already_gone_identity_counts_as_removed() -> None:
    client = _client(lambda r: httpx.Response(404))
    assert remove_supabase_identity(_settings(), SUB, client=client) is True


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_error_status_is_a_failure(status: int) -> None:
    client = _client(lambda r: httpx.Response(status, text=KEY))
    assert remove_supabase_identity(_settings(), SUB, client=client) is False


def test_network_error_is_a_failure_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    assert remove_supabase_identity(_settings(), SUB, client=_client(handler)) is False


def test_unset_or_blank_key_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    for key in (None, ""):
        settings = _settings(supabase_service_role_key=key)
        assert remove_supabase_identity(settings, SUB, client=_client(handler)) is False


def test_missing_supabase_url_is_a_failure_without_a_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    settings = _settings(supabase_url=None)
    assert remove_supabase_identity(settings, SUB, client=_client(handler)) is False


def test_subject_is_percent_encoded_into_the_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    remove_supabase_identity(_settings(), "a/../b", client=_client(handler))

    assert seen[0].url.raw_path == b"/auth/v1/admin/users/a%2F..%2Fb"


def test_the_key_never_reaches_the_logs_or_settings_repr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach with {KEY}", request=request)

    with caplog.at_level(logging.DEBUG):
        remove_supabase_identity(_settings(), SUB, client=_client(boom))
        remove_supabase_identity(
            _settings(), SUB, client=_client(lambda r: httpx.Response(500, text=KEY))
        )

    assert KEY not in caplog.text
    assert KEY not in repr(_settings())


def test_uses_a_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Fake:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def delete(self, *args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(200)

    monkeypatch.setattr(identity.httpx, "Client", Fake)

    assert remove_supabase_identity(_settings(), SUB) is True
    assert captured["timeout"] == identity.ADMIN_TIMEOUT_SECONDS
    assert 0 < identity.ADMIN_TIMEOUT_SECONDS <= 15


@pytest.mark.parametrize(
    "error", [UnicodeEncodeError("ascii", "\u201c", 0, 1, "bad"), ValueError("x"), httpx.InvalidURL("u")]
)
def test_a_non_httpx_error_is_a_failure_not_an_exception(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    assert remove_supabase_identity(_settings(), SUB, client=_client(handler)) is False
