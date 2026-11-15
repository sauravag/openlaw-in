"""`/auth/status`, the sign-in-unavailable refusal, and the startup refusal.

`/auth/status` must answer in BOTH modes: a Mode A backend answering
`auth_enabled: false` is precisely how a frontend built with sign-in detects
that it was built for the wrong backend.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.accounts.migrate import migrate_anonymous_material
from openlaw.api import auth_routes
from openlaw.api.app import ANON_COOKIE_NAME, create_app
from openlaw.api.auth import AuthError, decode_token
from openlaw.api.deps import _require_auth_configuration
from openlaw.config import Settings
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    User,
)
from openlaw.documents.store import store_document
from tests.api.conftest import (
    JWT_SECRET,
    NOTICE_TEXT,
    RecordingAnswerFn,
    api_settings,
    bearer,
    build_client,
)
import tests.api.conftest as _conftest


@pytest.fixture(autouse=True)
def _dispose_engines(monkeypatch: pytest.MonkeyPatch):
    """Dispose every sqlite engine `build_client` opens, so none leaks."""
    engines = []
    real_create = _conftest.create_engine

    def tracking(*args, **kwargs):
        engine = real_create(*args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(_conftest, "create_engine", tracking)
    yield
    for engine in engines:
        engine.dispose()


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


# --- POST /account/migrate (Plan 7 Task 10) ---------------------------------

ANON = "anon-token-for-migration"


def _seed_anonymous(tmp_path: Path, settings: Settings, token: str = ANON) -> None:
    """Put one anonymous conversation and document in the client's database."""
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            owner = Owner(user_id=None, anon_token=token)
            conversation = create_conversation(session, owner, settings)
            store_document(
                session, owner, conversation.id, "n.txt", "text/plain", NOTICE_TEXT, settings
            )
            session.commit()
    finally:
        engine.dispose()


def _owners(settings: Settings) -> list[tuple[str | None, str | None]]:
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            return [
                (c.user_id, c.anon_token) for c in session.query(Conversation).all()
            ] + [(d.user_id, d.anon_token) for d in session.query(Document).all()]
    finally:
        engine.dispose()


def _migrate(client, headers: dict[str, str] | None = None, cookie: str | None = ANON):
    if cookie is not None:
        client.cookies.set(ANON_COOKIE_NAME, cookie)
    return client.post("/account/migrate", headers=headers or {})


def test_migrate_is_404_in_mode_a(tmp_path: Path) -> None:
    response = _mode_a(tmp_path).post("/account/migrate")

    assert response.status_code == 404


def test_migrate_moves_anonymous_material_and_returns_counts_only(
    tmp_path: Path,
) -> None:
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    _seed_anonymous(tmp_path, settings)

    response = _migrate(client, bearer("user-1"))

    assert response.status_code == 200
    assert response.json() == {"conversations_moved": 1, "documents_moved": 1}
    assert set(_owners(settings)) == {("user-1", None)}


def test_migrate_repeat_call_returns_zeros_and_200(tmp_path: Path) -> None:
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    _seed_anonymous(tmp_path, settings)
    _migrate(client, bearer("user-1"))

    response = _migrate(client, bearer("user-1"))

    assert response.status_code == 200
    assert response.json() == {"conversations_moved": 0, "documents_moved": 0}


def test_migrate_never_moves_material_owned_by_another_account(tmp_path: Path) -> None:
    """A row already owned by someone else that still carries the caller's
    token must stay put: only the `user_id IS NULL` guard protects it."""
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            session.add(User(id="owner-9", email="nine@example.com"))
            session.add(Conversation(user_id="owner-9", anon_token=ANON))
            session.commit()
    finally:
        engine.dispose()

    response = _migrate(client, bearer("user-1", "one@example.com"))

    assert response.json() == {"conversations_moved": 0, "documents_moved": 0}
    assert _owners(settings) == [("owner-9", ANON)]


def test_migrate_without_an_anon_token_returns_zeros_without_calling_migration(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path, api_settings(tmp_path))
    state = SimpleNamespace(anon_token="")
    request = SimpleNamespace(
        state=state,
        app=client.app,
        headers={"authorization": bearer()["Authorization"]},
    )

    with patch.object(auth_routes, "migrate_anonymous_material") as migrate:
        result = auth_routes.migrate_account(request)

    assert result.model_dump() == {"conversations_moved": 0, "documents_moved": 0}
    migrate.assert_not_called()


def _expired_token() -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "user-1",
            "email": "person@example.com",
            "aud": "authenticated",
            "role": "authenticated",
            "exp": int(time.time()) - 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _token(**overrides: object) -> dict[str, str]:
    claims: dict[str, object] = {
        "sub": "user-1",
        "email": "person@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return {"Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"}


_BAD_BEARERS: dict[str, dict[str, str]] = {
    "missing": {},
    "wrong-scheme": {"Authorization": "Basic abc"},
    "garbage": {"Authorization": "Bearer not-a-jwt"},
    "bad-signature": {
        "Authorization": "Bearer "
        + jwt.encode({"sub": "u", "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256")
    },
    "expired": _token(exp=int(time.time()) - 60),
    "wrong-aud": _token(aud="anon"),
    "missing-sub": _token(sub=None),
    "wrong-role": _token(role="service_role"),
}


def _auth_error_details() -> set[str]:
    """Every AuthError.detail the verifier can produce for the bad bearers."""
    details = {"no bearer token"}
    for headers in _BAD_BEARERS.values():
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            decode_token(token, JWT_SECRET)
        except AuthError as error:
            details.add(error.detail)
    return details


@pytest.mark.parametrize("name", list(_BAD_BEARERS))
def test_migrate_refuses_an_unverified_caller_with_401_and_helplines(
    tmp_path: Path, name: str
) -> None:
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    _seed_anonymous(tmp_path, settings)

    response = _migrate(client, _BAD_BEARERS[name])

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    body = response.json()
    assert body["escalation_footer"] and body["helplines"]
    for detail in _auth_error_details():
        assert detail not in response.text
    assert set(_owners(settings)) == {(None, ANON)}


def test_migrate_refuses_an_email_collision_with_409_and_honest_copy(
    tmp_path: Path,
) -> None:
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    _seed_anonymous(tmp_path, settings)
    _migrate(client, bearer("user-1", "shared@example.com"))
    _seed_anonymous(tmp_path, settings, token="anon-2")

    response = _migrate(client, bearer("user-2", "shared@example.com"), cookie="anon-2")

    assert response.status_code == 409
    body = response.json()
    assert "already linked to another account" in body["detail"]
    assert "sign in again" not in body["detail"].lower()
    assert "still in this browser" in body["detail"]
    assert body["escalation_footer"] and body["helplines"]
    assert "registered to a different account" not in response.text
    assert (None, "anon-2") in _owners(settings)


def test_migrate_collision_copy_is_available_in_hindi() -> None:
    from openlaw.api.refusal import refusal_detail

    assert refusal_detail("account_email_conflict", "hi") != refusal_detail(
        "account_email_conflict", "en"
    )


def test_unavailable_stays_503() -> None:
    from openlaw.api.refusal import REFUSAL_STATUS

    assert REFUSAL_STATUS["auth_unavailable"] == 503
    assert REFUSAL_STATUS["sign_in_required"] == 401
    assert REFUSAL_STATUS["account_email_conflict"] == 409


def test_a_failure_after_the_moves_rolls_back_everything(tmp_path: Path) -> None:
    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings)
    _seed_anonymous(tmp_path, settings)

    def move_then_fail(*args, **kwargs):
        migrate_anonymous_material(*args, **kwargs)
        raise RuntimeError("boom")

    with patch.object(auth_routes, "migrate_anonymous_material", move_then_fail):
        response = _migrate(client, bearer("user-1"))

    assert response.status_code == 500
    assert set(_owners(settings)) == {(None, ANON)}
    # The user row was committed on its own, before the migration began.
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            assert session.get(User, "user-1") is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_migrate_through_the_endpoint_on_live_postgres(
    live_engine, tmp_path: Path
) -> None:
    """The brief's 'Done when', on the engine production runs on."""
    Base.metadata.create_all(live_engine, checkfirst=True)
    assert live_engine.url.database == "openlaw_test"
    token = f"anon-{time.time_ns()}"
    subject = f"00000000-0000-4000-8000-{time.time_ns() % 10**12:012d}"
    email = f"{subject}@example.com"
    settings = api_settings(tmp_path, supabase_url=SUPABASE_URL)

    @contextmanager
    def session_factory():
        session = Session(live_engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    conversation_id: str | None = None
    document_id: str | None = None
    try:
        with Session(live_engine) as seed:
            owner = Owner(user_id=None, anon_token=token)
            conversation = create_conversation(seed, owner, settings)
            conversation_id = conversation.id
            document = store_document(
                seed, owner, conversation.id, "n.txt", "text/plain", NOTICE_TEXT, settings
            )
            document_id = document.id
            seed.commit()

        client = TestClient(
            create_app(RecordingAnswerFn(), session_factory, settings),
            raise_server_exceptions=False,
        )
        response = _migrate(client, bearer(subject, email), cookie=token)

        assert response.status_code == 200
        assert response.json() == {"conversations_moved": 1, "documents_moved": 1}
        with Session(live_engine) as check:
            conv = check.get(Conversation, conversation_id)
            doc = check.get(Document, document_id)
            assert (conv.user_id, conv.anon_token, conv.expires_at) == (subject, None, None)
            assert (doc.user_id, doc.anon_token, doc.expires_at) == (subject, None, None)
            actions = [
                row.action
                for row in check.query(DocumentAccessLog).filter(
                    DocumentAccessLog.document_id == document_id
                )
            ]
            assert "migrate" in actions
    finally:
        with Session(live_engine) as cleanup:
            if document_id is not None:
                cleanup.query(DocumentAccessLog).filter(
                    DocumentAccessLog.document_id == document_id
                ).delete()
            if conversation_id is not None:
                cleanup.query(Conversation).filter(
                    Conversation.id == conversation_id
                ).delete()
            cleanup.query(User).filter(User.id == subject).delete()
            cleanup.commit()
