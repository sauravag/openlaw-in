"""Unit tests for `openlaw.index.qdrant_http` -- the shared retry/connection
helper that keeps a multi-hour index build from dying on a single dropped
Qdrant connection.

No real network or Qdrant is involved: `call_with_retry` is exercised
against small stub callables that raise the exact exception types a stale
pooled connection produces, with `sleep` stubbed out so the tests run
instantly regardless of the configured backoff.
"""

import httpx
import pytest

from openlaw.index.qdrant_http import build_index_client, call_with_retry


def _request() -> httpx.Request:
    return httpx.Request("PUT", "https://qdrant.test/collections/x/points")


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = _request()
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


class _Sleeps:
    """Records requested delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# --- The core contract: retry connection failures, never retry real responses


def test_succeeds_on_the_first_attempt_without_sleeping() -> None:
    sleeps = _Sleeps()

    result = call_with_retry(lambda: "ok", description="test", sleep=sleeps)

    assert result == "ok"
    assert sleeps.delays == []


def test_a_transport_error_then_success_completes_and_retries_exactly_once() -> None:
    """The scenario this fix exists for: one dead-socket write, then a fresh
    connection succeeds -- the caller must see a completed call, not a
    crashed build."""
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")
        return "batch upserted"

    sleeps = _Sleeps()
    result = call_with_retry(flaky, description="test", sleep=sleeps)

    assert result == "batch upserted"
    assert attempts["count"] == 2
    assert len(sleeps.delays) == 1


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: httpx.RemoteProtocolError("Server disconnected without sending a response"),
        lambda: httpx.ConnectError("connection refused"),
        lambda: httpx.ReadError("read failed"),
        lambda: httpx.ConnectTimeout("timed out connecting"),
        lambda: httpx.ReadTimeout("timed out reading"),
    ],
)
def test_retries_every_connection_level_error_family_member(make_error) -> None:
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise make_error()
        return "ok"

    result = call_with_retry(flaky, description="test", sleep=_Sleeps())

    assert result == "ok"
    assert attempts["count"] == 2


def test_a_4xx_response_raises_immediately_without_any_retry() -> None:
    """A malformed request must fail loudly on the first attempt, not loop."""
    attempts = {"count": 0}

    def bad_request() -> str:
        attempts["count"] += 1
        raise _http_status_error(400)

    sleeps = _Sleeps()
    with pytest.raises(httpx.HTTPStatusError):
        call_with_retry(bad_request, description="test", sleep=sleeps)

    assert attempts["count"] == 1
    assert sleeps.delays == []


def test_a_5xx_response_also_raises_immediately_without_retry() -> None:
    """HTTPStatusError is never retried, real 5xx included -- only
    connection-level failures are, per the task's contract."""
    attempts = {"count": 0}

    def server_error() -> str:
        attempts["count"] += 1
        raise _http_status_error(500)

    with pytest.raises(httpx.HTTPStatusError):
        call_with_retry(server_error, description="test", sleep=_Sleeps())

    assert attempts["count"] == 1


# --- Bounded retries: a persistent outage must surface, not loop forever ----


def test_persistent_connection_failure_raises_after_max_attempts() -> None:
    attempts = {"count": 0}

    def always_broken() -> str:
        attempts["count"] += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        call_with_retry(always_broken, description="test", max_attempts=3, sleep=_Sleeps())

    assert attempts["count"] == 3


def test_backoff_doubles_each_attempt_up_to_the_configured_cap() -> None:
    attempts = {"count": 0}

    def always_broken() -> str:
        attempts["count"] += 1
        raise httpx.ConnectError("connection refused")

    sleeps = _Sleeps()
    with pytest.raises(httpx.ConnectError):
        call_with_retry(
            always_broken,
            description="test",
            max_attempts=5,
            base_delay_seconds=1.0,
            max_delay_seconds=6.0,
            sleep=sleeps,
        )

    assert sleeps.delays == [1.0, 2.0, 4.0, 6.0]


def test_logs_a_warning_on_each_retry_and_an_error_on_final_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A persistent outage must be diagnosable from the log, not a silent
    retry loop."""

    def always_broken() -> str:
        raise httpx.ConnectError("connection refused")

    with caplog.at_level("WARNING", logger="openlaw.index.qdrant_http"):
        with pytest.raises(httpx.ConnectError):
            call_with_retry(
                always_broken, description="upsert batch", max_attempts=2, sleep=_Sleeps()
            )

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(warnings) == 1
    assert "upsert batch" in warnings[0].message
    assert len(errors) == 1
    assert "upsert batch" in errors[0].message


# --- Connection handling: avoid the dead socket in the first place ----------


def test_build_index_client_disables_keepalive_connection_reuse() -> None:
    """The idle-gap fix: a build script's requests are minutes apart, so a
    pooled keep-alive connection is pure liability -- every request should
    open a fresh one rather than risk reusing a socket the server (or an
    intermediary) already closed."""
    with build_index_client(timeout=30) as client:
        assert client._transport._pool._max_keepalive_connections == 0


def test_build_index_client_honours_the_requested_timeout() -> None:
    with build_index_client(timeout=42) as client:
        assert client.timeout.connect == 42
