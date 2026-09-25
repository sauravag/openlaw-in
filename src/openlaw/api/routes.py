"""HTTP endpoints.

Anonymous users get a small free allowance before signup so the first question
costs no friction -- the people this exists for will not create an account to
find out whether it helps (`Settings.anonymous_quota`).

**Jurisdiction is deliberately left caller-specified, not wired here.**
`AskRequest.state` only ever reaches `escalation_footer` (routing to the right
State Legal Services Authority) and is forwarded to `answer_fn` unchanged.
This module never calls `openlaw.retrieval.search.search` itself and never
constructs a `court_codes` filter -- that mapping (which Indian state maps to
which High Court's `court_code`) belongs to whatever builds the concrete
`answer_fn`/`Retriever` (Task 5's `build_answer_fn`/`QdrantGraphRetriever`),
since only that layer touches retrieval at all. The safe default --
`hc_judgments_v1` excluded unless a caller explicitly asks -- therefore holds
here by construction: there is no code path in this file that could silently
merge one state's High Court authority into another state's answer.

**Quota is checked before `answer_fn` runs, so a refusal never pays for
retrieval or generation.** `answer_fn` itself still runs its own full,
two-layer crisis check (regex, then classifier) ahead of retrieval and
generation for every request that IS allowed through -- see
`openlaw.language.pipeline.answer_in_language`'s module docstring.

**Every terminal response carries help, not just the quota refusal (Task 4
review, H1/M4).** `openlaw.api.refusal.crisis_extras` and `refusal_detail`
are the one shared helper every refusal path in this module (and the app-
level 500 handler in `app.py`) calls, so "a refusal must still route a
person in danger toward help, never leave them at a bare dead end" holds for
the rate limit and an unhandled error too, not only an exhausted allowance.
See that module's docstring for what it does and does not do (in particular,
why it still never spends the paid CLASSIFIER-role model call).

**Two different keys for two different controls (Task 4 review, C1/C1b).**
Short-burst rate limiting (`_check_rate_limit`) stays keyed by the caller's
IP (`_client_key`, trusted-proxy aware -- see `Settings.trusted_proxy_ips`):
it is an abuse/cost control, and a shared IP correctly sharing one bucket is
the point. The anonymous QUOTA (`_anonymous_decision`) is instead keyed by
an opaque per-client cookie token (`_anon_token`, set by `create_app`'s
middleware in `app.py`), with the IP used only as a last-resort fallback.
Indian mobile carriers run large-scale CGNAT -- one public IP can front an
entire region -- so keying the free allowance by IP would collapse it to one
shared bucket for everyone behind the same carrier, defeating the entire
point of a no-friction anonymous path for exactly the population it exists
for. A cleared cookie resets a user's own allowance; that is an accepted
trade-off, since the IP rate limit still bounds abuse regardless.

**A token that fails to verify falls back to the anonymous allowance rather
than refusing outright (Task 4 review, H1/M4 ruling).** A stale/expired
token is the ordinary state of a returning user on a shared or cybercafe
device -- refusing it left that user WORSE OFF than sending no token at all,
with no fallback to the allowance the code already had three lines away.
`AuthError` (and its `.detail`, which still never reaches a client) is now
caught inside `_authenticated_decision` and logged server-side only.

**Every turn's events are persisted at this boundary.**
`openlaw.answers.generate.persist_answer_events` is called once `answer_fn`
returns, on every non-refused turn -- `answer_in_language` deliberately
builds `HallucinationEvent`/`ClassifierFailureEvent` without writing them
(see that module's docstring), and this endpoint is the boundary that must
write them, or a sustained classifier outage or a hallucinated citation
becomes undiscoverable after the fact.
"""

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from openlaw.answers.generate import persist_answer_events
from openlaw.api.auth import AuthError, decode_token, extract_bearer, upsert_user
from openlaw.api.refusal import crisis_extras, refusal_detail
from openlaw.api.schemas import AskRequest, AskResponse, QuotaExceeded, to_response
from openlaw.config import Settings
from openlaw.db.quota import (
    QuotaDecision,
    check_quota,
    consume_quota,
    current_period,
    next_period_start,
)
from openlaw.index.upsert import SC_COLLECTION
from openlaw.retrieval.search import ACTS_INDIA_V1

#: Timeout for the /readyz datastore probes -- short, since this endpoint
#: exists to be polled frequently by an orchestrator and must never itself
#: become the slow thing blocking a deploy.
_READINESS_TIMEOUT_SECONDS = 3.0

#: Collections `/ask` searches on EVERY request by default (Task 7
#: requirement 2; see `openlaw.retrieval.search._collections_to_search`).
#: `hc_judgments_v1` is deliberately excluded here: it only joins a search
#: when a caller explicitly scopes a query to an onboarded High Court
#: jurisdiction, so an instance with no High Court data yet is still ready
#: to answer the ordinary Supreme-Court-and-statute question -- requiring it
#: here would report a self-host "not ready" for a corpus gap that never
#: blocks an actual answer.
_REQUIRED_CORPUS_COLLECTIONS: tuple[str, ...] = (ACTS_INDIA_V1, SC_COLLECTION)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Short-burst abuse protection, independent of the monthly/anonymous
#: allowance below. In-memory and per-process, which is the right scope for
#: a single-instance/self-hosted deployment; a multi-instance deployment
#: needs a shared store (Redis) instead, which is out of this task's scope.
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_MAX_REQUESTS = 30


#: One-time-per-process latch for `_warn_once_about_unconfigured_proxy_trust`
#: (Task 5 review, H3). Module-level rather than `request.app.state`: several
#: existing tests call `_client_key` directly with a bare `SimpleNamespace`
#: request that has no `.app`, and "once per process" is the semantics the
#: review actually asked for (`logger.error`, rate-limited to once, not once
#: per app instance). `tests/api/test_routes.py` resets this between tests
#: via an autouse fixture so the tests stay order-independent.
_proxy_trust_warning_logged = False


def _warn_once_about_unconfigured_proxy_trust(request: Request) -> None:
    """Log once, at ERROR, when a forwarded-for header arrives with no
    trusted proxy configured (Task 5 review, H3).

    `Settings.trusted_proxy_ips` left empty is ambiguous between "no proxy,
    correctly trusting nothing" and "there IS a proxy and nobody configured
    it" -- and the second one otherwise fails with no crash and no 500, just
    the fourth visitor of the day getting a normal-looking 429 while every
    caller behind the proxy shares one bucket. A request carrying
    `X-Forwarded-For` while the trusted set is empty is near-proof a proxy
    is in front of this service, so it is the one signal cheap enough to
    check on every request without becoming a per-request cost itself
    (`production_app`'s `_require_proxy_acknowledgement` closes the same
    hazard at startup; this catches the case where a proxy is added to an
    already-running deployment without anyone updating the setting).
    """
    global _proxy_trust_warning_logged
    if _proxy_trust_warning_logged or not request.headers.get("x-forwarded-for"):
        return
    _proxy_trust_warning_logged = True
    logger.error(
        "request arrived with X-Forwarded-For but OPENLAW_TRUSTED_PROXY_IPS "
        "is empty -- a reverse proxy appears to be in front of this service "
        "with no trust configured, which collapses per-IP rate limiting and "
        "the anonymous quota into one shared bucket for the whole "
        "deployment. Set OPENLAW_TRUSTED_PROXY_IPS to the proxy's address "
        "(see .env.example). Logged once per process."
    )


def _client_key(request: Request, settings: Settings) -> str:
    """Return the caller's IP for rate-limit accounting.

    Trusts `request.client.host` (the raw TCP peer) UNLESS that peer is
    listed in `settings.trusted_proxy_ips`, in which case the real client is
    derived by walking `X-Forwarded-For` from the right and returning the
    first hop that is NOT itself a trusted proxy. See `Settings
    .trusted_proxy_ips`'s docstring for the deployment consequence of
    leaving this unset behind a real reverse proxy, and for why
    unconditionally trusting the header instead would be worse, not better
    (it is attacker-supplied, and this service's abuse controls exist
    precisely to bound real money spent on a model provider).
    """
    peer = request.client.host if request.client else "unknown"
    trusted = settings.trusted_proxy_ips
    if not trusted:
        _warn_once_about_unconfigured_proxy_trust(request)
        return peer
    if peer not in trusted:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return peer


def _anon_token(request: Request, settings: Settings) -> str:
    """Return the opaque per-client token used to key the ANONYMOUS quota.

    Deliberately distinct from `_client_key` -- see the module docstring's
    "Two different keys" note. Set by `create_app`'s cookie middleware
    (`app.py`) on `request.state.anon_token` for every request; the IP is
    used only as a last-resort fallback if that state is somehow absent
    (e.g. a caller that bypasses the middleware, such as a test that builds
    a bare `APIRouter` rather than the full app).
    """
    token = getattr(request.state, "anon_token", None)
    return token or _client_key(request, settings)


def _prune_rate_limit_hits(hits: dict[str, list[float]], window_start: float) -> None:
    """Drop every key with no hits still inside the window.

    Runs on every check (Task 4 review, M1): an earlier version only
    trimmed the key being checked THIS call, so a key that stopped sending
    requests entirely was never removed and the dict grew without bound --
    worse, its docstring claimed pruning that did not happen. A full sweep
    here bounds the dict to "keys seen in the last window", from any caller,
    not just the current one.
    """
    for existing_key in list(hits):
        fresh = [hit for hit in hits[existing_key] if hit >= window_start]
        if fresh:
            hits[existing_key] = fresh
        else:
            del hits[existing_key]


def _raise_rate_limited(
    request: Request, question: str, state: str | None, language: str | None,
    settings: Settings, retry_after_seconds: int,
) -> None:
    """Raise the 429 for a tripped short-burst rate limit.

    Shares `QuotaExceeded`'s shape with the quota refusal below (Task 4
    review, H1): always carries `escalation_footer` and, via
    `crisis_extras`, `crisis_response` when warranted -- this refusal must
    not be a dead end either. `quota_reset_at` is None here (a burst limit
    has no calendar reset); `Retry-After` carries the actual wait instead.
    """
    footer, crisis, helplines = crisis_extras(question, state, language)
    body = QuotaExceeded(
        detail=refusal_detail("rate_limited", language),
        quota_remaining=0,
        signup_required=False,
        escalation_footer=footer,
        crisis_response=crisis,
        helplines=helplines,
        quota_reset_at=None,
        signup_url=settings.signup_url,
    )
    raise HTTPException(
        status_code=429,
        detail=body.model_dump(),
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _check_rate_limit(
    request: Request, key: str, question: str, state: str | None,
    language: str | None, settings: Settings,
) -> None:
    """Raise HTTP 429 when `key` has exceeded the short-burst request cap.

    A fixed window keyed by `key` and stored on `app.state`. This guards
    infrastructure and generation cost against bursts (a script, a retry
    loop, an automated scrape) -- a distinct concern from the monthly/
    anonymous allowance in `ask`, which is about fairness across a billing
    period, not request rate.
    """
    hits: dict[str, list[float]] = request.app.state.rate_limit_hits
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    _prune_rate_limit_hits(hits, window_start)

    recent = hits.get(key, [])
    if len(recent) >= _RATE_LIMIT_MAX_REQUESTS:
        retry_after = max(1, math.ceil(recent[0] + _RATE_LIMIT_WINDOW_SECONDS - now))
        _raise_rate_limited(request, question, state, language, settings, retry_after)
    hits.setdefault(key, []).append(now)


def _seconds_until(reset_date: date) -> int:
    """Return whole seconds from now (UTC) until midnight UTC on `reset_date`."""
    reset_at = datetime(reset_date.year, reset_date.month, reset_date.day, tzinfo=UTC)
    return max(1, int((reset_at - datetime.now(UTC)).total_seconds()))


def _raise_quota_exceeded(
    question: str, state: str | None, language: str | None,
    signup_required: bool, settings: Settings,
) -> None:
    """Raise the 429 for an exhausted monthly/anonymous allowance.

    Always carries `escalation_footer` and, via `crisis_extras`,
    `crisis_response` when warranted -- a quota refusal must still route a
    person in danger toward help, never leave them at a bare "limit
    reached" dead end. `quota_reset_at` and `Retry-After` both tell the
    caller when the allowance actually comes back (Task 4 review, M6);
    `signup_url` gives both refusal types a concrete next step.
    """
    footer, crisis, helplines = crisis_extras(question, state, language)
    reset_date = next_period_start()
    body = QuotaExceeded(
        detail=refusal_detail("quota_exceeded", language),
        quota_remaining=0,
        signup_required=signup_required,
        escalation_footer=footer,
        crisis_response=crisis,
        helplines=helplines,
        quota_reset_at=reset_date.isoformat(),
        signup_url=settings.signup_url,
    )
    raise HTTPException(
        status_code=429,
        detail=body.model_dump(),
        headers={"Retry-After": str(_seconds_until(reset_date))},
    )


def _authenticated_decision(
    session_factory: Callable, settings: Settings, authorization: str | None
) -> QuotaDecision | None:
    """Return the quota decision for a bearer-token request, or None.

    None means "proceed on the anonymous allowance instead" -- both for no
    token/no auth configured, AND (Task 4 review, H1/M4 ruling) for a token
    that fails verification; see the module docstring for why a refusal was
    the wrong response to a stale token. `AuthError.detail` never reaches a
    client either way (see `openlaw.api.auth`'s module docstring); now it
    never surfaces as a bare 401 either -- only to `logger.warning`.

    Opens exactly one session and does the whole authenticated turn on it:
    `upsert_user` may flush a pending insert/update, and `consume_quota`
    commits the session to release its row lock (see
    `openlaw.db.quota`'s module docstring) -- committing here also commits
    the upsert, so a brand-new user and their first quota increment land
    atomically together.
    """
    token = extract_bearer(authorization)
    if not token or not settings.supabase_jwt_secret:
        return None

    try:
        with session_factory() as session:
            identity = decode_token(token, settings.supabase_jwt_secret)
            user = upsert_user(session, identity)
            return consume_quota(
                session,
                user.id,
                limit=settings.monthly_quota,
                enabled=settings.quota_enabled,
            )
    except AuthError as error:
        logger.warning(
            "authentication failed, falling back to the anonymous allowance: %s",
            error.detail,
        )
        return None


def _prune_anonymous_counts(
    counts: dict[str, tuple[date, int]], period: date
) -> None:
    """Drop every entry from a past billing period.

    The anonymous allowance resets every calendar month, the same cadence
    `consume_quota` uses for the authenticated path (Task 4 review, M2: it
    previously never reset at all). An entry from a prior period is both
    stale (its count no longer applies -- a fresh request for that token
    this period starts at 0) and safe to forget, so one sweep fixes M2 and
    bounds the dict's growth (M1) together.
    """
    stale = [token for token, (stored_period, _) in counts.items() if stored_period != period]
    for token in stale:
        del counts[token]


def _anonymous_decision(request: Request, settings: Settings, token: str) -> QuotaDecision:
    """Return the quota decision for an unauthenticated request.

    Tracked in-memory, per process, on `app.state.anonymous_used`, keyed by
    the opaque per-client token from `_anon_token` (never the IP -- see the
    module docstring). Only increments when the question is actually
    allowed through and quota is enabled, so a refused or quota-disabled
    request never inflates the count.
    """
    period = current_period()
    counts: dict[str, tuple[date, int]] = request.app.state.anonymous_used
    _prune_anonymous_counts(counts, period)

    used = counts.get(token, (period, 0))[1]
    decision = check_quota(used, settings.anonymous_quota, settings.quota_enabled)
    if decision.allowed and settings.quota_enabled:
        counts[token] = (period, used + 1)
        return QuotaDecision(allowed=True, used=used + 1, limit=settings.anonymous_quota)
    return decision


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe -- process is up. Deliberately checks nothing else;
    see `/readyz` for whether it can actually answer a question."""
    return {"status": "ok"}


def _check_postgres(session_factory: Callable) -> str:
    """Return `"ok"` if a session can open and run a trivial query, else a
    short reason. Never raises -- a failed check is data for `/readyz`, not
    an exception that would itself 500 the readiness endpoint."""
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # pragma: no cover - depends on live environment
        logger.warning("readiness check: Postgres unreachable: %s", exc)
        return "unreachable"


_NOT_FOUND = 404


def _collection_points_count(
    client: httpx.Client, qdrant_url: str, collection: str
) -> int:
    """Return how many points `collection` holds, or `0` if it does not
    exist yet -- both read as "not ready to answer" to `_check_qdrant`, and
    a self-host between `docker compose up` and its first corpus build is
    exactly the "does not exist yet" case (see the README's corpus-build
    section).

    Only a 404 is treated as "not built yet" (Task 7 review, L2: an earlier
    version caught every `HTTPStatusError`, so a genuine server-side fault
    on Qdrant -- a 500, or a 403 from a misconfigured auth setup -- was
    reported as the calm, expected "empty" rather than the alarming
    "unreachable" it actually is). Any other failure (a non-404 status, a
    timeout, connection refused) is left to propagate to `_check_qdrant`'s
    own `except`, which reports `"unreachable"`.
    """
    response = client.get(f"{qdrant_url}/collections/{collection}")
    if response.status_code == _NOT_FOUND:
        return 0
    response.raise_for_status()
    return int(response.json()["result"]["points_count"])


def _check_qdrant(
    qdrant_url: str, client_factory: Callable[..., httpx.Client] = httpx.Client
) -> str:
    """Return `"ok"` only if Qdrant is reachable AND every collection `/ask`
    searches by default (`_REQUIRED_CORPUS_COLLECTIONS`) actually holds
    vectors; `"empty"` if Qdrant is reachable but the corpus is not there
    yet; `"unreachable"` for anything else. `client_factory` is injectable
    for tests -- never invoked against a live server here.

    Task 7 requirement 2: before this checked point counts, a freshly
    installed self-host with an empty (or entirely absent) corpus reported
    `/readyz` healthy -- the stack looked fine, then every real question
    500'd on an empty retrieval context. This is what makes `/readyz` prove
    the service can actually answer, not merely that Qdrant's process is up.
    """
    try:
        with client_factory(timeout=_READINESS_TIMEOUT_SECONDS) as client:
            response = client.get(f"{qdrant_url}/healthz")
            response.raise_for_status()
            empty = [
                collection
                for collection in _REQUIRED_CORPUS_COLLECTIONS
                if _collection_points_count(client, qdrant_url, collection) <= 0
            ]
        if empty:
            logger.warning(
                "readiness check: corpus collection(s) empty or missing: %s",
                ", ".join(empty),
            )
            return "empty"
        return "ok"
    except Exception as exc:  # pragma: no cover - depends on live environment
        logger.warning("readiness check: Qdrant unreachable: %s", exc)
        return "unreachable"


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    """Readiness probe: can this instance actually answer a question right
    now (Task 5 review, M1).

    Deliberately separate from `/health` (liveness): a process that is UP
    but cannot reach Postgres or Qdrant is not ready to serve traffic, and
    conflating the two means a deployment with no corpus, an unreachable
    Qdrant, or a down Postgres starts clean, reports healthy, and fails only
    on a real user's question (a generic 500) -- the exact "starts and only
    fails once a user asks a question" outcome this task's requirements
    forbid. Checks Postgres (a session round-trip) and Qdrant -- both its own
    `/healthz` AND that `_REQUIRED_CORPUS_COLLECTIONS` actually hold vectors
    (Task 7 requirement 2: a reachable-but-empty Qdrant is exactly the state
    a self-host is in immediately after `docker compose up`, before its
    first corpus build, and reporting THAT healthy is what let every real
    question 500 while the stack looked fine) -- never Neo4j or the
    Anthropic API, both of which `production_app()` already either validates
    or does not require for a
    degraded-but-functional (graph expansion off, or crisis classifier
    absorbing a failure) service.
    """
    settings: Settings = request.app.state.settings
    session_factory: Callable = request.app.state.session_factory
    postgres = _check_postgres(session_factory)
    qdrant = _check_qdrant(settings.qdrant_url)
    healthy = postgres == "ok" and qdrant == "ok"
    body = {
        "status": "ok" if healthy else "degraded",
        "postgres": postgres,
        "qdrant": qdrant,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@router.post("/ask", response_model=AskResponse)
def ask(
    request: Request,
    body: AskRequest,
    authorization: str | None = Header(default=None),
) -> AskResponse:
    """Answer a legal question, metering it against the caller's allowance.

    Order matters: the allowance is checked, and enforced, before `answer_fn`
    is ever called -- retrieval and generation cost real money and a refused
    request must not pay for them. `answer_fn` itself still runs its own
    full, two-layer crisis check (regex, then classifier) ahead of retrieval
    and generation for every request that IS allowed through.
    """
    settings = request.app.state.settings
    answer_fn: Callable = request.app.state.answer_fn
    session_factory: Callable = request.app.state.session_factory

    # Stashed so `app.py`'s generic 500 handler can still attach crisis
    # detection and the escalation footer to an unhandled-error response --
    # see `openlaw.api.refusal`'s module docstring.
    request.state.question = body.question
    request.state.state = body.state
    request.state.language = body.language

    ip_key = _client_key(request, settings)
    _check_rate_limit(request, ip_key, body.question, body.state, body.language, settings)

    decision = _authenticated_decision(session_factory, settings, authorization)
    signup_required = decision is None
    if decision is None:
        anon_token = _anon_token(request, settings)
        decision = _anonymous_decision(request, settings, anon_token)

    if not decision.allowed:
        _raise_quota_exceeded(
            body.question, body.state, body.language, signup_required, settings
        )

    localised = answer_fn(body.question, body.language, body.state)
    persist_answer_events(localised, settings)

    return to_response(localised, settings.corpus_version, decision.remaining)
