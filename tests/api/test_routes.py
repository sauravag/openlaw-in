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
from pathlib import Path

import jwt
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


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "database_url": "sqlite://",
        "quota_enabled": True,
        "anonymous_quota": 3,
        "monthly_quota": 2,
        "supabase_jwt_secret": JWT_SECRET,
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


# --- Requirement 4: auth failures never leak detail ------------------------


def test_invalid_bearer_token_returns_a_generic_401_without_leaking_detail() -> None:
    client = _client()
    response = client.post(
        "/ask",
        json={"question": "q"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    # The generic, fixed string only -- never PyJWT internals or claim content.
    assert response.json()["detail"] == "authentication failed"


def test_expired_bearer_token_returns_a_generic_401() -> None:
    client = _client()
    expired = _bearer_token(exp=int(time.time()) - 10)
    response = client.post(
        "/ask", json={"question": "q"}, headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication failed"


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


# --- unexpected failures never leak a traceback -----------------------------


def test_an_unhandled_error_in_answer_fn_returns_a_generic_500() -> None:
    def broken_answer_fn(question: str, language: str | None, state: str | None):
        raise RuntimeError("qdrant is down: connection refused at 10.0.0.5:6333")

    client = _client(answer_fn=broken_answer_fn)
    response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 500
    assert "qdrant" not in response.text
    assert "10.0.0.5" not in response.text
