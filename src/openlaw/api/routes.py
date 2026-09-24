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
retrieval or generation.** When the allowance is exhausted, this module still
runs the free, no-model-call crisis regex (`openlaw.answers.escalation.
detect_crisis`) on the raw question and always attaches the escalation
footer -- a quota refusal must still route a person in danger toward help,
never leave them at a bare "limit reached" dead end. The paid,
CLASSIFIER-role second layer is not spent on a request already being
refused; that full check runs inside `answer_fn` itself, for a request that
is actually allowed to proceed.

**Every turn's events are persisted at this boundary.**
`openlaw.answers.generate.persist_answer_events` is called once `answer_fn`
returns, on every non-refused turn -- `answer_in_language` deliberately
builds `HallucinationEvent`/`ClassifierFailureEvent` without writing them
(see that module's docstring), and this endpoint is the boundary that must
write them, or a sustained classifier outage or a hallucinated citation
becomes undiscoverable after the fact.
"""

import logging
import time
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from openlaw.answers.escalation import crisis_response, detect_crisis, escalation_footer
from openlaw.answers.generate import persist_answer_events
from openlaw.api.auth import AuthError, decode_token, extract_bearer, upsert_user
from openlaw.api.schemas import AskRequest, AskResponse, QuotaExceeded, to_response
from openlaw.config import Settings
from openlaw.db.quota import QuotaDecision, check_quota, consume_quota

logger = logging.getLogger(__name__)

router = APIRouter()

#: Short-burst abuse protection, independent of the monthly/anonymous
#: allowance below -- see the task report's "rate limiting" note. In-memory
#: and per-process, which is the right scope for a single-instance/
#: self-hosted deployment; a multi-instance deployment needs a shared store
#: (Redis) instead, which is out of this task's scope.
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_MAX_REQUESTS = 30
_RATE_LIMIT_DETAIL = "Too many requests. Please wait a moment and try again."


def _client_key(request: Request) -> str:
    """Return a coarse key for anonymous allowance and rate-limit accounting."""
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request, key: str) -> None:
    """Raise HTTP 429 when `key` has exceeded the short-burst request cap.

    A fixed window keyed by `key` and stored on `app.state`, pruned to the
    current window on every check so the store cannot grow unbounded. This
    guards infrastructure and generation cost against bursts (a script, a
    retry loop, an automated scrape) -- a distinct concern from the
    monthly/anonymous allowance in `ask`, which is about fairness across a
    billing period, not request rate.
    """
    hits: dict[str, list[float]] = request.app.state.rate_limit_hits
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    recent = [hit for hit in hits.get(key, []) if hit >= window_start]
    if len(recent) >= _RATE_LIMIT_MAX_REQUESTS:
        hits[key] = recent
        raise HTTPException(status_code=429, detail=_RATE_LIMIT_DETAIL)
    recent.append(now)
    hits[key] = recent


def _quota_exceeded_response(
    question: str, state: str | None, signup_required: bool
) -> QuotaExceeded:
    """Build the 429 body for an exhausted allowance.

    Always carries `escalation_footer`. Runs only the free regex crisis
    layer (`detect_crisis`) -- see the module docstring for why the
    CLASSIFIER-role layer is never spent here.
    """
    kind = detect_crisis(question)
    return QuotaExceeded(
        detail="Free allowance used for this period.",
        quota_remaining=0,
        signup_required=signup_required,
        escalation_footer=escalation_footer(state),
        crisis_response=crisis_response(kind) if kind is not None else None,
    )


def _authenticated_decision(
    session_factory: Callable, settings: Settings, authorization: str | None
) -> QuotaDecision | None:
    """Return the quota decision for a bearer-token request, or None.

    None means "no usable token" (missing, or auth is not configured) --
    the caller falls back to the anonymous path. Raises AuthError, which the
    caller turns into a 401 without exposing `.detail` (see
    `openlaw.api.auth`'s module docstring on why that field must never reach
    a client).

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

    with session_factory() as session:
        identity = decode_token(token, settings.supabase_jwt_secret)
        user = upsert_user(session, identity)
        return consume_quota(
            session,
            user.id,
            limit=settings.monthly_quota,
            enabled=settings.quota_enabled,
        )


def _anonymous_decision(request: Request, settings: Settings, key: str) -> QuotaDecision:
    """Return the quota decision for an unauthenticated request.

    Tracked in-memory, per process, on `app.state.anonymous_used` -- the same
    scope tradeoff as the rate limiter above, and adequate for the
    self-hosted/single-instance deployments this product targets today. Only
    increments when the question is actually allowed through and quota is
    enabled, so a refused or quota-disabled request never inflates the count.
    """
    used_by_key: dict[str, int] = request.app.state.anonymous_used
    used = used_by_key.get(key, 0)
    decision = check_quota(used, settings.anonymous_quota, settings.quota_enabled)
    if decision.allowed and settings.quota_enabled:
        used_by_key[key] = used + 1
        return QuotaDecision(allowed=True, used=used + 1, limit=settings.anonymous_quota)
    return decision


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


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
    and generation for every request that IS allowed through -- see
    `openlaw.language.pipeline.answer_in_language`'s module docstring.
    """
    settings = request.app.state.settings
    answer_fn: Callable = request.app.state.answer_fn
    session_factory: Callable = request.app.state.session_factory

    key = _client_key(request)
    _check_rate_limit(request, key)

    try:
        decision = _authenticated_decision(session_factory, settings, authorization)
    except AuthError as error:
        logger.warning("authentication failed: %s", error.detail)
        raise HTTPException(status_code=401, detail=str(error)) from error

    signup_required = decision is None
    if decision is None:
        decision = _anonymous_decision(request, settings, key)

    if not decision.allowed:
        refusal = _quota_exceeded_response(body.question, body.state, signup_required)
        raise HTTPException(status_code=429, detail=refusal.model_dump())

    localised = answer_fn(body.question, body.language, body.state)
    persist_answer_events(localised, settings)

    return to_response(localised, settings.corpus_version, decision.remaining)
