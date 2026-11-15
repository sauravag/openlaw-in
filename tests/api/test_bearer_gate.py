"""A bearer token is honoured only when `settings.auth_enabled` (final review L4).

A Mode A backend whose `.env` still carries Supabase variables must not act on
an `Authorization` header: Mode A has no accounts, so a verified token there
would quietly create a second identity space the UI knows nothing about.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.api.routes import _authenticated_decision, caller_owner
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import Base, User
from tests.api.conftest import api_settings, bearer, build_client


def _request(anon_token: str) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(anon_token=anon_token), headers={}, client=None
    )


def _real_factory(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(engine) as session:
            yield session
            session.commit()

    return factory


def _mode_a_settings(tmp_path: Path):
    # The Supabase secret is still configured: exactly the leftover-.env case.
    return api_settings(tmp_path, auth_enabled=False, quota_enabled=False)


def test_caller_owner_ignores_a_valid_bearer_in_mode_a(tmp_path: Path) -> None:
    settings = _mode_a_settings(tmp_path)
    assert settings.supabase_jwt_secret

    owner = caller_owner(
        _request("anon-1"),
        settings,
        _real_factory(settings.database_url),
        bearer("user-1")["Authorization"],
    )

    assert owner == Owner(user_id=None, anon_token="anon-1")


def test_caller_owner_honours_a_valid_bearer_in_mode_b(tmp_path: Path) -> None:
    settings = api_settings(tmp_path)

    owner = caller_owner(
        _request("anon-1"),
        settings,
        _real_factory(settings.database_url),
        bearer("user-1")["Authorization"],
    )

    assert owner == Owner(user_id="user-1", anon_token=None)


def test_metering_ignores_a_valid_bearer_in_mode_a(tmp_path: Path) -> None:
    settings = _mode_a_settings(tmp_path)

    decision = _authenticated_decision(
        _real_factory(settings.database_url),
        settings,
        bearer("user-1")["Authorization"],
    )

    assert decision is None


def _seed_user_conversation(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            session.add(User(id="user-1", email="person@example.com"))
            session.flush()
            owner = Owner(user_id="user-1", anon_token=None)
            conversation = create_conversation(session, owner, api_settings(Path("/tmp")))
            session.commit()
            return conversation.id
    finally:
        engine.dispose()


def test_a_mode_a_read_with_a_bearer_cannot_reach_a_user_owned_row(
    tmp_path: Path,
) -> None:
    settings = _mode_a_settings(tmp_path)
    client: TestClient = build_client(tmp_path, settings)
    conversation_id = _seed_user_conversation(settings.database_url)

    response = client.get(
        f"/conversations/{conversation_id}", headers=bearer("user-1")
    )

    assert response.status_code == 404
    assert response.json()["helplines"]
