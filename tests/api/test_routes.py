"""Tests for the `/ask` and `/health` HTTP surface.

No live Supabase project, Anthropic key, or vector store is configured
anywhere here -- `answer_fn` is always a stub, and authenticated tests mint
their own throwaway-secret JWTs exactly the way `tests/api/test_auth.py`
does.

**Note on `ValidatedAnswer`:** it has no `summary` field -- only
`unverified_summary` (never safe to render, see
`openlaw.answers.validate`'s module docstring), `claims`, and `sources`. Test
fixtures below build claims whose `.text` is the string a test wants to see
rendered in `AskResponse.answer`, since that is the only content
`openlaw.api.schemas.to_response` is allowed to compose an answer from.
"""

import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from openlaw.answers.crisis_classifier import ClassifierFailureEvent
from openlaw.answers.escalation import CrisisKind, HallucinationEvent
from openlaw.answers.schema import Claim
from openlaw.answers.validate import Source, ValidatedAnswer
from openlaw.api.app import create_app
from openlaw.config import Settings
from openlaw.db.models import Base
from openlaw.language.pipeline import LocalisedAnswer

JWT_SECRET = "test-secret-at-least-32-bytes-long!!"


@pytest.fixture(autouse=True)
def _reset_proxy_trust_warning_latch() -> None:
    """`openlaw.api.routes._proxy_trust_warning_logged` is a module-level,
    once-per-process latch (Task 5 review, H3) -- reset it before every test
    in this file so a warning logged by one test cannot silently suppress
    the assertion in another, regardless of test order."""
    import openlaw.api.routes as routes_module

    routes_module._proxy_trust_warning_logged = False


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "database_url": "sqlite://",
        "quota_enabled": True,
        "anonymous_quota": 3,
        "monthly_quota": 2,
        "supabase_jwt_secret": JWT_SECRET,
        # TestClient talks plain HTTP ("http://testserver"); a `Secure`
        # cookie (the production default -- see `Settings.cookie_secure`)
        # would never round-trip back to the client, silently breaking
        # every anonymous-quota test that relies on the cookie surviving
        # between requests on the same client. Mirrors the documented local
        # HTTP dev case, not a weakening of the production default.
        "cookie_secure": False,
    }
    return Settings(**{**defaults, **overrides})


def _validated_answer(
    claim_text: str = "The deposit must be refunded.",
    chunk_id: str = "a",
    source_url: str = "https://www.indiacode.nic.in/handle/123456789/1",
    title: str | None = "Model Tenancy Act",
    unverified_summary: str | None = "unverified model prose -- must never render",
) -> ValidatedAnswer:
    return ValidatedAnswer(
        unverified_summary=unverified_summary,
        claims=(Claim(text=claim_text, chunk_ids=(chunk_id,)),),
        sources=(Source(chunk_id=chunk_id, source_url=source_url, title=title),),
        is_answerable=True,
    )


_UNANSWERABLE = ValidatedAnswer(unverified_summary=None, is_answerable=False)


def _localised(
    crisis: CrisisKind | None = None,
    answer: ValidatedAnswer | None = None,
    events: tuple[HallucinationEvent, ...] = (),
    classifier_failures: tuple[ClassifierFailureEvent, ...] = (),
) -> LocalisedAnswer:
    resolved_answer = answer or (_UNANSWERABLE if crisis else _validated_answer())
    return LocalisedAnswer(
        answer=resolved_answer,
        english_answer=resolved_answer,
        language="en",
        english_query="my landlord kept my deposit",
        crisis=crisis,
        footer="This is legal information, not legal advice. NALSA: 15100",
        events=events,
        classifier_failures=classifier_failures,
    )


def _client(
    localised: LocalisedAnswer | None = None,
    settings: Settings | None = None,
    answer_fn=None,
) -> TestClient:
    # `check_same_thread=False` + `StaticPool` keeps one shared connection to
    # the same in-memory database across threads -- FastAPI's sync route
    # handlers run in anyio's worker threadpool, a different thread than the
    # one that called `Base.metadata.create_all` below. Without this, each
    # request can land on a *different* connection and therefore a *blank*
    # in-memory database ("no such table: users").
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    def default_answer_fn(
        question: str, language: str | None, state: str | None
    ) -> LocalisedAnswer:
        return localised or _localised()

    # `raise_server_exceptions=False` matches what a real deployment's caller
    # actually sees: the app's own `Exception` handler renders a response
    # rather than the exception propagating out of the test transport.
    return TestClient(
        create_app(
            answer_fn or default_answer_fn, session_factory, settings or _settings()
        ),
        raise_server_exceptions=False,
    )


def _bearer_token(**claim_overrides: object) -> str:
    claims = {
        "sub": "user-1",
        "email": "person@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


# --- health -------------------------------------------------------------


def test_health_endpoint_reports_ok() -> None:
    assert _client().get("/health").json()["status"] == "ok"


# --- Task 5 review, M1: readiness must be checked separately from liveness


def test_readyz_reports_ok_when_both_datastores_are_reachable(monkeypatch) -> None:
    """Postgres is genuinely checked (the test client's real sqlite
    session); Qdrant is monkeypatched -- this suite never makes a real
    network call to a live Qdrant."""
    from openlaw.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_check_qdrant", lambda qdrant_url: "ok")

    response = _client().get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "postgres": "ok", "qdrant": "ok"}


def test_readyz_reports_degraded_and_503_when_qdrant_is_unreachable(monkeypatch) -> None:
    from openlaw.api import routes as routes_module

    monkeypatch.setattr(
        routes_module, "_check_qdrant", lambda qdrant_url: "unreachable"
    )

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "postgres": "ok",
        "qdrant": "unreachable",
    }


def test_readyz_reports_degraded_and_503_when_postgres_is_unreachable(
    monkeypatch,
) -> None:
    from openlaw.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_check_qdrant", lambda qdrant_url: "ok")

    def _broken_session_factory():
        raise RuntimeError("no such table")

    client = _client(answer_fn=None)
    # Swap in a session factory that always fails -- exercises the "cannot
    # even open a session" branch, not just "opened but the query failed".
    client.app.state.session_factory = _broken_session_factory

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "postgres": "unreachable",
        "qdrant": "ok",
    }


def _fake_qdrant_client(
    points_by_collection: dict[str, int], error_status: dict[str, int] | None = None
):
    """Build a fake `httpx.Client`-shaped factory for `_check_qdrant`.

    Answers `/healthz` unconditionally, and `/collections/<name>` with a
    `points_count` looked up in `points_by_collection` -- a collection not
    present in that dict responds 404, matching real Qdrant's behaviour for
    a collection that was never created. `error_status` overrides the
    status code for a named collection (e.g. 500) -- Task 7 review, L2.
    """
    import httpx

    error_status = error_status or {}

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("GET", "http://q:6333/x")
                raise httpx.HTTPStatusError(
                    "error", request=request, response=httpx.Response(
                        self.status_code, request=request
                    )
                )

        def json(self) -> dict:
            return self._payload

    class _FakeClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, url: str) -> _FakeResponse:
            if url.endswith("/healthz"):
                return _FakeResponse(200)
            collection = url.rsplit("/", 1)[-1]
            if collection in error_status:
                return _FakeResponse(error_status[collection])
            if collection not in points_by_collection:
                return _FakeResponse(404)
            payload = {"result": {"points_count": points_by_collection[collection]}}
            return _FakeResponse(200, payload)

    return _FakeClient


def test_check_qdrant_reports_ok_when_corpus_collections_are_populated() -> None:
    """Unit-level check of `_check_qdrant` itself, with an injected fake
    client -- proves the function's own logic without any network access."""
    from openlaw.api.routes import _check_qdrant

    client_factory = _fake_qdrant_client(
        {"acts_india_v1": 961_136, "sc_judgments_v1": 371_247}
    )

    assert _check_qdrant("http://q:6333", client_factory=client_factory) == "ok"


def test_check_qdrant_reports_empty_when_a_corpus_collection_has_zero_points() -> None:
    """Task 7 requirement 2: an empty corpus must not report readiness --
    the exact "stack up, then every question 500s" failure mode this check
    exists to prevent."""
    from openlaw.api.routes import _check_qdrant

    client_factory = _fake_qdrant_client(
        {"acts_india_v1": 0, "sc_judgments_v1": 371_247}
    )

    assert _check_qdrant("http://q:6333", client_factory=client_factory) == "empty"


def test_check_qdrant_reports_empty_when_a_corpus_collection_does_not_exist() -> None:
    """A collection that was never created (a self-host before its first
    corpus build) reads the same as "empty", not "unreachable" -- Qdrant
    itself is fine, the corpus just is not there yet."""
    from openlaw.api.routes import _check_qdrant

    client_factory = _fake_qdrant_client({"acts_india_v1": 961_136})

    assert _check_qdrant("http://q:6333", client_factory=client_factory) == "empty"


def test_check_qdrant_reports_unreachable_on_a_non_404_collection_error() -> None:
    """Task 7 review, L2: a genuine server-side fault (500) on a collection
    lookup must read as "unreachable", not the calm "empty" -- only a 404
    (the collection was never created) means "corpus not built yet"."""
    from openlaw.api.routes import _check_qdrant

    client_factory = _fake_qdrant_client(
        {"sc_judgments_v1": 371_247}, error_status={"acts_india_v1": 500}
    )

    assert _check_qdrant("http://q:6333", client_factory=client_factory) == "unreachable"


def test_check_qdrant_reports_unreachable_on_any_failure() -> None:
    from openlaw.api.routes import _check_qdrant

    def _boom(timeout: float):
        raise ConnectionError("refused")

    assert _check_qdrant("http://q:6333", client_factory=_boom) == "unreachable"


# --- basic /ask shape -----------------------------------------------------


def test_ask_returns_the_answer_composed_from_validated_claims() -> None:
    response = _client().post("/ask", json={"question": "my landlord kept my deposit"})
    assert response.status_code == 200
    assert response.json()["answer"] == "The deposit must be refunded."


def test_ask_never_renders_unverified_summary() -> None:
    """Regression: a model can hide a hallucination in `summary` while its
    claims stay well-cited. The rendered answer must never contain it."""
    body = _client().post("/ask", json={"question": "q"}).json()
    assert "unverified model prose" not in body["answer"]


def test_ask_returns_source_cards_with_government_urls() -> None:
    body = _client().post("/ask", json={"question": "q"}).json()
    assert body["sources"][0]["source_url"].startswith("https://")


def test_ask_returns_the_corpus_version() -> None:
    """Every answer is time-bound -- the reader must see which snapshot it is."""
    assert _client().post("/ask", json={"question": "q"}).json()["corpus_version"] == (
        "v2026.08"
    )


def test_ask_always_returns_the_escalation_footer() -> None:
    assert "15100" in _client().post("/ask", json={"question": "q"}).json()["footer"]


def test_crisis_response_is_returned_as_its_own_field() -> None:
    """The interface renders it above the legal content, never below."""
    client = _client(_localised(crisis=CrisisKind.DOMESTIC_VIOLENCE))
    body = client.post("/ask", json={"question": "my husband beats me"}).json()

    assert body["crisis_response"] is not None
    assert "181" in body["crisis_response"] or "1091" in body["crisis_response"]


def test_no_crisis_response_for_an_ordinary_question() -> None:
    assert _client().post("/ask", json={"question": "q"}).json()["crisis_response"] is (
        None
    )


# --- validation -------------------------------------------------------------


def test_rejects_a_blank_question() -> None:
    assert _client().post("/ask", json={"question": "   "}).status_code == 422


def test_rejects_an_overlong_question() -> None:
    assert _client().post("/ask", json={"question": "x" * 2001}).status_code == 422


# --- anonymous quota -------------------------------------------------------


def test_anonymous_quota_is_reported() -> None:
    assert _client().post("/ask", json={"question": "q"}).json()["quota_remaining"] == 2


def test_anonymous_user_is_blocked_after_the_free_allowance() -> None:
    client = _client()
    for _ in range(3):
        client.post("/ask", json={"question": "q"})
    blocked = client.post("/ask", json={"question": "q"})

    assert blocked.status_code == 429
    assert blocked.json()["signup_required"] is True


def test_quota_disabled_never_blocks() -> None:
    """Self-hosted deployments run without a cap."""
    client = _client(settings=_settings(quota_enabled=False))
    for _ in range(10):
        response = client.post("/ask", json={"question": "q"})
    assert response.status_code == 200


# --- Requirement 1: a quota refusal must still deliver help ---------------


def test_quota_refusal_still_carries_the_escalation_footer() -> None:
    client = _client()
    for _ in range(3):
        client.post("/ask", json={"question": "q"})
    blocked = client.post("/ask", json={"question": "q"})

    assert blocked.status_code == 429
    assert "15100" in blocked.json()["escalation_footer"]


def test_quota_refusal_surfaces_crisis_help_for_a_dangerous_question() -> None:
    """Crisis detection is free (no model call) and must still run even when
    the user has no quota left -- a refusal is never a bare dead end."""
    client = _client()
    for _ in range(3):
        client.post("/ask", json={"question": "an ordinary question"})
    blocked = client.post("/ask", json={"question": "my husband beats me"})

    assert blocked.status_code == 429
    assert blocked.json()["crisis_response"] is not None
    assert "181" in blocked.json()["crisis_response"]


def test_quota_refusal_has_no_crisis_response_for_an_ordinary_question() -> None:
    client = _client()
    for _ in range(3):
        client.post("/ask", json={"question": "q"})
    blocked = client.post("/ask", json={"question": "q"})

    assert blocked.json()["crisis_response"] is None


# --- Requirement 4 + review ruling H1/M4: auth failures never leak detail,
# and never leave the caller worse off than sending no token at all ---------


def test_invalid_bearer_token_falls_back_to_the_anonymous_allowance() -> None:
    """A malformed token used to return a bare 401. The review's H1/M4 ruling:
    fall back to the anonymous path instead -- a stale token is the ordinary
    state of a returning user on a shared device, and refusing outright left
    them worse off than sending no token at all."""
    client = _client(settings=_settings(anonymous_quota=3))
    response = client.post(
        "/ask",
        json={"question": "q"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 200
    # 2, not 1 (monthly_quota - 1) -- proves the anonymous allowance was
    # used, not a (nonexistent, for this bad token) authenticated identity.
    assert response.json()["quota_remaining"] == 2


def test_expired_bearer_token_falls_back_to_the_anonymous_allowance() -> None:
    client = _client(settings=_settings(anonymous_quota=3))
    expired = _bearer_token(exp=int(time.time()) - 10)
    response = client.post(
        "/ask", json={"question": "q"}, headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 200
    assert response.json()["quota_remaining"] == 2


# --- Requirement 6: the authenticated path meters its own (larger) quota ---


def test_authenticated_user_is_metered_against_the_monthly_quota() -> None:
    client = _client(settings=_settings(monthly_quota=2, anonymous_quota=1))
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/ask", json={"question": "q"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["quota_remaining"] == 1

    second = client.post("/ask", json={"question": "q"}, headers=headers)
    assert second.status_code == 200
    assert second.json()["quota_remaining"] == 0

    third = client.post("/ask", json={"question": "q"}, headers=headers)
    assert third.status_code == 429
    assert third.json()["signup_required"] is False


def test_authenticated_user_is_not_double_counted_against_anonymous_quota() -> None:
    """A signed-in user with a smaller anonymous_quota than monthly_quota must
    still get the larger, authenticated allowance -- not be blocked by the
    anonymous path just because both share this process."""
    client = _client(settings=_settings(monthly_quota=5, anonymous_quota=1))
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(3):
        response = client.post("/ask", json={"question": "q"}, headers=headers)
        assert response.status_code == 200


# --- Requirement 7: events are persisted at the boundary --------------------


def test_hallucination_and_classifier_failure_events_are_persisted(
    tmp_path: Path,
) -> None:
    hallucination_log = tmp_path / "hallucinations.jsonl"
    classifier_log = tmp_path / "classifier_failures.jsonl"
    settings = _settings(
        hallucination_log_path=hallucination_log,
        classifier_failure_log_path=classifier_log,
    )
    event = HallucinationEvent(
        question="q",
        dropped_chunk_ids=("bogus",),
        retrieved_chunk_ids=("a",),
        claim_text="an unverifiable claim",
        timestamp="2026-09-24T00:00:00+00:00",
    )
    failure = ClassifierFailureEvent(
        question="q", error="model timeout", timestamp="2026-09-24T00:00:00+00:00"
    )
    localised = _localised(events=(event,), classifier_failures=(failure,))

    client = _client(localised, settings=settings)
    response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 200
    assert hallucination_log.exists()
    assert "an unverifiable claim" in hallucination_log.read_text()
    assert classifier_log.exists()
    assert "model timeout" in classifier_log.read_text()


def test_no_events_written_for_an_ordinary_answerable_turn(tmp_path: Path) -> None:
    settings = _settings(
        hallucination_log_path=tmp_path / "hallucinations.jsonl",
        classifier_failure_log_path=tmp_path / "classifier_failures.jsonl",
    )
    client = _client(settings=settings)
    client.post("/ask", json={"question": "q"})

    assert not (tmp_path / "hallucinations.jsonl").exists()
    assert not (tmp_path / "classifier_failures.jsonl").exists()


# --- answer_fn receives what the caller sent --------------------------------


def test_answer_fn_is_called_with_the_question_language_and_state() -> None:
    seen: dict[str, object] = {}

    def answer_fn(question: str, language: str | None, state: str | None):
        seen["question"] = question
        seen["language"] = language
        seen["state"] = state
        return _localised()

    client = _client(answer_fn=answer_fn)
    client.post(
        "/ask",
        json={"question": "my rent was raised", "language": "hi", "state": "Delhi"},
    )

    assert seen == {"question": "my rent was raised", "language": "hi", "state": "Delhi"}


# --- short-burst rate limiting ----------------------------------------------


def test_rate_limit_trips_after_the_burst_cap_and_recovers_outside_the_window() -> None:
    from openlaw.api import routes as routes_module

    client = _client(settings=_settings(quota_enabled=False))
    # Fill the window exactly to the cap directly, rather than firing 30 real
    # requests -- same code path, without slowing the suite down.
    now = routes_module.time.monotonic()
    client.app.state.rate_limit_hits["testclient"] = [
        now
    ] * routes_module._RATE_LIMIT_MAX_REQUESTS

    limited = client.post("/ask", json={"question": "q"})
    assert limited.status_code == 429
    assert "Too many requests" in limited.json()["detail"]

    # Outside the window, the same key is allowed again.
    client.app.state.rate_limit_hits["testclient"] = [
        now - routes_module._RATE_LIMIT_WINDOW_SECONDS - 1
    ] * routes_module._RATE_LIMIT_MAX_REQUESTS
    recovered = client.post("/ask", json={"question": "q"})
    assert recovered.status_code == 200


# --- Review finding H2: state/language are allowlisted, not free text ------


def test_rejects_a_state_that_is_not_a_real_jurisdiction() -> None:
    """The reviewer's own proof string: unvalidated `state` let a
    <script> tag and a phishing URL land inside `escalation_footer`, the one
    sentence that tells a citizen who to call for help."""
    proof = "<script>alert(1)</script> Send money to hxxp://evil"
    response = _client().post("/ask", json={"question": "q", "state": proof})
    assert response.status_code == 422


def test_rejects_an_unsupported_language_code() -> None:
    # Short enough to pass the length check, so this actually exercises the
    # allowlist validator rather than the length constraint ahead of it.
    response = _client().post("/ask", json={"question": "q", "language": "fr"})
    assert response.status_code == 422


def test_rejects_an_overlong_language_code() -> None:
    response = _client().post("/ask", json={"question": "q", "language": "xx-not-real"})
    assert response.status_code == 422


def test_accepts_a_real_state_and_a_supported_language() -> None:
    response = _client().post(
        "/ask", json={"question": "q", "state": "Delhi", "language": "hi"}
    )
    assert response.status_code == 200


# --- Review finding H3: a crisis turn is never marked unanswerable ---------


def test_unanswerable_non_crisis_turn_gets_the_fixed_no_verifiable_answer_message() -> (
    None
):
    """The legitimate "we searched and found nothing citable" case -- not a
    crisis, just nothing survived validation. Distinct from H3, which is
    about the crisis case specifically."""
    client = _client(_localised(answer=_UNANSWERABLE))
    body = client.post("/ask", json={"question": "q"}).json()

    assert body["is_answerable"] is False
    assert "could not find a verifiable answer" in body["answer"].lower()


def test_crisis_turn_is_not_marked_unanswerable() -> None:
    client = _client(_localised(crisis=CrisisKind.DOMESTIC_VIOLENCE))
    body = client.post("/ask", json={"question": "my husband beats me"}).json()

    assert body["is_answerable"] is True
    assert "could not find a verifiable answer" not in body["answer"].lower()
    assert "181" in body["answer"] or "1091" in body["answer"]


# --- Review finding C1/C1b: rate limiting is IP-keyed, anonymous quota is
# token-keyed, and the two must not collapse into one shared bucket --------


def _shared_app(settings: Settings | None = None):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    def answer_fn(question: str, language: str | None, state: str | None):
        return _localised()

    return create_app(answer_fn, session_factory, settings or _settings())


def test_two_anonymous_callers_behind_the_same_ip_get_separate_quota_buckets() -> None:
    """Regression for C1/C1b/M5: two distinct browsers behind one shared IP
    (the common case behind CGNAT, or two TestClient instances that both
    report the same "testclient" peer) must not share one allowance."""
    app = _shared_app(settings=_settings(anonymous_quota=1))
    client_a = TestClient(app, raise_server_exceptions=False)
    client_b = TestClient(app, raise_server_exceptions=False)

    assert client_a.post("/ask", json={"question": "q"}).status_code == 200
    assert client_a.post("/ask", json={"question": "q"}).status_code == 429
    # A different caller, same IP, still has their own allowance.
    assert client_b.post("/ask", json={"question": "q"}).status_code == 200


def test_client_key_uses_the_peer_address_when_no_proxy_is_trusted() -> None:
    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"), headers={"x-forwarded-for": "1.2.3.4"}
    )
    assert _client_key(request, _settings(trusted_proxy_ips=())) == "203.0.113.9"


def test_client_key_uses_the_rightmost_untrusted_forwarded_for_hop() -> None:
    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="172.18.0.1"),
        headers={"x-forwarded-for": "203.0.113.5, 172.18.0.1"},
    )
    settings = _settings(trusted_proxy_ips=("172.18.0.1",))
    assert _client_key(request, settings) == "203.0.113.5"


def test_client_key_trusts_the_peer_directly_when_it_is_not_a_trusted_proxy() -> None:
    """`trusted_proxy_ips` is configured, but this particular caller's peer
    is not in it -- a direct connection that bypassed the proxy entirely
    (or a caller with no proxy in the path at all). The raw peer is used
    as-is, with no `X-Forwarded-For` walk, since only a trusted peer's
    forwarded header is meaningful."""
    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers={"x-forwarded-for": "1.2.3.4"},
    )
    settings = _settings(trusted_proxy_ips=("172.18.0.1",))
    assert _client_key(request, settings) == "203.0.113.9"


def test_client_key_falls_back_to_the_peer_when_every_hop_is_trusted() -> None:
    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="172.18.0.1"),
        headers={"x-forwarded-for": "172.18.0.1"},
    )
    settings = _settings(trusted_proxy_ips=("172.18.0.1",))
    assert _client_key(request, settings) == "172.18.0.1"


# --- Task 5 review, H3: an unconfigured proxy must be logged, not silent ---


def test_client_key_warns_once_when_forwarded_header_arrives_untrusted(caplog) -> None:
    import logging

    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers={"x-forwarded-for": "1.2.3.4"},
    )
    settings = _settings(trusted_proxy_ips=())

    with caplog.at_level(logging.ERROR, logger="openlaw.api.routes"):
        _client_key(request, settings)

    assert "OPENLAW_TRUSTED_PROXY_IPS" in caplog.text


def test_client_key_only_warns_once_per_process(caplog) -> None:
    import logging

    from openlaw.api.routes import _client_key

    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers={"x-forwarded-for": "1.2.3.4"},
    )
    settings = _settings(trusted_proxy_ips=())

    with caplog.at_level(logging.ERROR, logger="openlaw.api.routes"):
        _client_key(request, settings)
        _client_key(request, settings)
        _client_key(request, settings)

    assert len(caplog.records) == 1


def test_client_key_does_not_warn_when_no_forwarded_header_is_present(caplog) -> None:
    import logging

    from openlaw.api.routes import _client_key

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"), headers={})
    settings = _settings(trusted_proxy_ips=())

    with caplog.at_level(logging.ERROR, logger="openlaw.api.routes"):
        _client_key(request, settings)

    assert caplog.records == []


# --- Review finding M3: the refusal path's crisis check must not be blind
# to Devanagari and other Indic scripts --------------------------------------


def test_quota_refusal_gives_a_safety_note_for_devanagari_the_regex_cannot_match() -> (
    None
):
    """The regex layer (`detect_crisis`) only matches English/Hinglish
    Latin-script phrasing -- it cannot fire on Devanagari at all. The
    refusal path must still surface something rather than silence."""
    client = _client(settings=_settings(anonymous_quota=0))
    blocked = client.post("/ask", json={"question": "मेरे पति मुझे पीटते हैं"})

    assert blocked.status_code == 429
    assert blocked.json()["crisis_response"] is not None
    assert "112" in blocked.json()["crisis_response"]


def test_quota_refusal_crisis_response_is_none_for_an_ordinary_latin_question() -> None:
    client = _client(settings=_settings(anonymous_quota=0))
    blocked = client.post("/ask", json={"question": "an ordinary question"})
    assert blocked.json()["crisis_response"] is None


# --- Review findings M6 + "refusal text is English-only" -------------------


def test_quota_refusal_carries_reset_date_signup_url_and_retry_after() -> None:
    client = _client(
        settings=_settings(anonymous_quota=0, signup_url="https://openlaw.in/signup")
    )
    blocked = client.post("/ask", json={"question": "q"})

    assert blocked.status_code == 429
    body = blocked.json()
    assert body["quota_reset_at"] is not None
    assert body["signup_url"] == "https://openlaw.in/signup"
    assert "Retry-After" in blocked.headers


def test_rate_limit_refusal_carries_retry_after_and_signup_url() -> None:
    from openlaw.api import routes as routes_module

    client = _client(
        settings=_settings(quota_enabled=False, signup_url="https://openlaw.in/signup")
    )
    now = routes_module.time.monotonic()
    client.app.state.rate_limit_hits["testclient"] = (
        [now] * routes_module._RATE_LIMIT_MAX_REQUESTS
    )

    limited = client.post("/ask", json={"question": "q"})

    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    body = limited.json()
    assert body["signup_url"] == "https://openlaw.in/signup"
    assert body["quota_reset_at"] is None


def test_quota_refusal_detail_defaults_to_english() -> None:
    client = _client(settings=_settings(anonymous_quota=0))
    blocked = client.post("/ask", json={"question": "q"})
    assert blocked.json()["detail"] == "Free allowance used for this period."


def test_quota_refusal_detail_is_localised_to_hindi() -> None:
    client = _client(settings=_settings(anonymous_quota=0))
    blocked = client.post("/ask", json={"question": "q", "language": "hi"})
    assert blocked.json()["detail"] != "Free allowance used for this period."
    assert "अवधि" in blocked.json()["detail"]


def test_500_response_also_carries_the_escalation_footer() -> None:
    """Review finding M4: an outage is the failure most likely to be
    sustained ("try again" alone is misleading), so it must not be a dead
    end either."""

    def broken_answer_fn(question: str, language: str | None, state: str | None):
        raise RuntimeError("boom")

    client = _client(answer_fn=broken_answer_fn)
    response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 500
    assert "15100" in response.json()["escalation_footer"]


# --- Review finding M1: both in-process dicts are actually pruned ----------


def test_prune_rate_limit_hits_removes_keys_with_no_recent_activity() -> None:
    from openlaw.api.routes import _prune_rate_limit_hits

    hits = {"stale": [0.0, 1.0], "fresh": [100.0]}
    _prune_rate_limit_hits(hits, window_start=50.0)
    assert hits == {"fresh": [100.0]}


def test_prune_anonymous_counts_drops_entries_from_a_past_period() -> None:
    from openlaw.api.routes import _prune_anonymous_counts

    counts = {
        "old-token": (date(2026, 8, 1), 3),
        "current-token": (date(2026, 9, 1), 1),
    }
    _prune_anonymous_counts(counts, period=date(2026, 9, 1))
    assert counts == {"current-token": (date(2026, 9, 1), 1)}


# --- Review finding M2: the anonymous allowance actually resets ------------


def test_anonymous_quota_resets_for_a_new_billing_period() -> None:
    client = _client(settings=_settings(anonymous_quota=1))
    first = client.post("/ask", json={"question": "q"})
    assert first.status_code == 200

    token = client.cookies.get("openlaw_anon_id")
    assert token is not None
    # Poison the stored count as if this token had already blown far past
    # the limit -- but in a stale (long-past) billing period.
    client.app.state.anonymous_used[token] = (date(2020, 1, 1), 999)

    recovered = client.post("/ask", json={"question": "q"})
    assert recovered.status_code == 200


# --- unexpected failures never leak a traceback -----------------------------


def test_an_unhandled_error_in_answer_fn_returns_a_generic_500() -> None:
    def broken_answer_fn(question: str, language: str | None, state: str | None):
        raise RuntimeError("qdrant is down: connection refused at 10.0.0.5:6333")

    client = _client(answer_fn=broken_answer_fn)
    response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 500
    assert "qdrant" not in response.text
    assert "10.0.0.5" not in response.text


# --- Task 5: the anonymous cookie's `Secure` flag is a deployment setting --


def test_anonymous_cookie_carries_secure_when_the_deployment_requires_https() -> None:
    client = _client(settings=_settings(anonymous_quota=3, cookie_secure=True))
    response = client.post("/ask", json={"question": "q"})

    assert "secure" in response.headers["set-cookie"].lower()


def test_anonymous_cookie_omits_secure_for_plain_http_local_development() -> None:
    client = _client(settings=_settings(anonymous_quota=3, cookie_secure=False))
    response = client.post("/ask", json={"question": "q"})

    assert "secure" not in response.headers["set-cookie"].lower()
