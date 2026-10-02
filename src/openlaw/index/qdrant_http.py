"""Resilient HTTP access to Qdrant for long-running index builds.

A multi-hour build (e.g. `scripts/build_hc_index.py` over ~1.85M chunks)
spends minutes per batch embedding before it ever talks to Qdrant again. A
pooled keep-alive connection left idle that long is exactly the kind of
connection a server, load balancer, or NAT device closes without telling the
client -- the next write then lands on a dead socket and surfaces as
`httpx.RemoteProtocolError` ("Server disconnected without sending a
response"), a `ConnectError`, a `ReadError`, or a timeout. Qdrant itself can
be perfectly healthy the whole time; only the connection has gone stale. See
`build_index_client` and `call_with_retry` below for the two complementary
halves of the fix: avoid the dead socket where possible, and recover cleanly
when it happens anyway.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Five attempts with doubling backoff (1s, 2s, 4s, 8s, capped at 30s) covers a
# Qdrant restart or a brief network blip without turning a persistent outage
# into a silent infinite loop -- the last attempt's failure always propagates.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 30.0


def build_index_client(timeout: float = 120) -> httpx.Client:
    """Return an `httpx.Client` configured for a long-running index build.

    Sets `max_keepalive_connections=0`, which disables connection reuse
    entirely: every request opens a fresh TCP connection rather than trying
    to reuse one that has been sitting idle in the pool. httpx already
    retires idle connections after `keepalive_expiry` (5s by default), but
    that is a client-side timer racing against whatever closed the socket
    first -- disabling reuse outright removes the race, and the cost (one
    extra local TCP handshake per request) is immaterial next to the minutes
    a build spends embedding between requests.

    This is deliberately NOT the right default for the query-serving hot
    path, where many requests arrive in quick succession and keep-alive
    reuse matters for latency -- it is specific to build scripts whose gaps
    between Qdrant calls are measured in minutes, not milliseconds.
    """
    return httpx.Client(timeout=timeout, limits=httpx.Limits(max_keepalive_connections=0))


def call_with_retry(
    func: Callable[[], T],
    *,
    description: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `func`, retrying only on transient connection failures.

    Retries `httpx.TransportError` -- `ConnectError`, `ReadError`,
    `RemoteProtocolError`, every `TimeoutException` subclass, and so on --
    with exponential backoff, up to `max_attempts` total tries, reconnecting
    fresh on each retry rather than reusing whatever connection just failed.

    `httpx.HTTPStatusError` (a real 4xx/5xx response, raised by a caller's
    own `response.raise_for_status()` inside `func`) is a sibling of
    `TransportError`, not a subclass, so it is never caught here: a
    malformed request or a genuine server error must fail loudly on the
    first attempt, not loop.

    `description` names the call being retried so a persistent outage is
    diagnosable from the log rather than a silent, indistinguishable retry
    loop; `sleep` is injectable so tests can exercise the retry path without
    real delay.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except httpx.TransportError as exc:
            if attempt >= max_attempts:
                logger.error(
                    "[Qdrant] %s failed after %d attempt(s), giving up: %s",
                    description, attempt, exc,
                )
                raise
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            logger.warning(
                "[Qdrant] %s: transient connection error (%s), retrying in "
                "%.1fs (attempt %d/%d)",
                description, exc, delay, attempt, max_attempts,
            )
            sleep(delay)
