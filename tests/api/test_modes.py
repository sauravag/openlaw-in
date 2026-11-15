"""Plan 7 Task 15: the whole route surface, in both modes, side by side.

"A flag that is only exercised in one state is a flag that breaks in the
other." Each test here is parametrised over both modes inside the file, so it
runs in every pytest session regardless of `OPENLAW_TEST_MODE`; the session
mode additionally runs the rest of the suite in the other state.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from openlaw.config import Settings
from openlaw.db.models import Conversation, Document, User
from tests.api.conftest import (
    DUMMY_JWT_SECRET_64,
    DUMMY_SUPABASE_URL,
    api_settings,
    bearer,
    build_client,
    upload,
)

MODES = ("a", "b")


def _settings_for(mode: str, tmp_path: Path, **overrides: object) -> Settings:
    if mode == "a":
        return api_settings(
            tmp_path,
            auth_enabled=False,
            quota_enabled=False,
            supabase_url=None,
            supabase_jwt_secret=None,
            **overrides,
        )
    return api_settings(
        tmp_path,
        auth_enabled=True,
        supabase_url=DUMMY_SUPABASE_URL,
        supabase_jwt_secret=DUMMY_JWT_SECRET_64,
        **overrides,
    )


@pytest.fixture
def client_for(tmp_path: Path) -> Iterator:
    """Factory `mode -> TestClient`, disposing every engine it opened."""
    clients: list[TestClient] = []

    def make(mode: str, **overrides: object) -> TestClient:
        client = build_client(tmp_path, _settings_for(mode, tmp_path, **overrides))
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


# (method, path, exists in Mode A, exists in Mode B). A route that "does not
# exist" answers 404/405 from the router, never reaching any handler.
ROUTE_SURFACE = [
    ("GET", "/auth/status", True, True),
    ("POST", "/account/migrate", False, True),
    ("DELETE", "/account", False, True),
    ("POST", "/ask", True, True),
    ("POST", "/documents", True, True),
    ("DELETE", "/documents/does-not-exist", True, True),
    ("GET", "/conversations/does-not-exist", True, True),
    ("DELETE", "/conversations/does-not-exist", True, True),
]


MISSING_ID = "00000000-0000-4000-8000-000000000000"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("method", "path", "in_a", "in_b"), ROUTE_SURFACE)
def test_route_exists_only_in_the_modes_that_own_it(
    client_for, mode: str, method: str, path: str, in_a: bool, in_b: bool
) -> None:
    """A route that does not exist is a router-level 404/405 with the router's
    own body; one that exists is answered by a handler, whatever it says."""
    client = client_for(mode)
    url = path.replace("does-not-exist", MISSING_ID)

    response = client.request(method, url, headers=bearer())

    exists = in_a if mode == "a" else in_b
    router_miss = response.status_code in (404, 405) and response.json() == {
        "detail": "Not Found"
    }
    assert router_miss is (not exists), (method, path, response.status_code)


@pytest.mark.parametrize("mode", MODES)
def test_the_account_routes_answer_404_where_they_do_not_exist(
    client_for, mode: str
) -> None:
    client = client_for(mode)

    migrate = client.post("/account/migrate", headers=bearer())
    delete = client.delete("/account", headers=bearer())

    if mode == "a":
        assert (migrate.status_code, delete.status_code) == (404, 404)
    else:
        assert migrate.status_code != 404 and delete.status_code != 404


@pytest.mark.parametrize("mode", MODES)
def test_auth_status_answers_in_both_modes_and_says_which(
    client_for, mode: str
) -> None:
    body = client_for(mode).get("/auth/status").json()

    assert body["auth_enabled"] is (mode == "b")
    assert (body["supabase_url"] is not None) is (mode == "b")


@pytest.mark.parametrize("mode", MODES)
def test_the_shared_endpoints_exist_in_both_modes(client_for, mode: str) -> None:
    client = client_for(mode)

    asked = client.post("/ask", json={"question": "my landlord kept my deposit"})
    uploaded = upload(client)
    read_back = client.get(f"/conversations/{uploaded.json()['conversation_id']}")

    assert asked.status_code == 200
    assert uploaded.status_code == 201
    assert read_back.status_code == 200
    assert client.get(f"/conversations/{MISSING_ID}").status_code == 404


def test_quota_cannot_be_enabled_without_accounts() -> None:
    with pytest.raises(ValidationError, match="quota"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            vaquill_root="/tmp/v",
            kanoongpt_root="/tmp/k",
            auth_enabled=False,
            quota_enabled=True,
        )


def test_mode_a_checks_no_quota_however_small_the_allowance(
    client_for, tmp_path: Path
) -> None:
    client = client_for("a", anonymous_quota=0, monthly_quota=0)

    # Past the burst cap too: nothing in Mode A limits a caller.
    statuses = {
        client.post("/ask", json={"question": "q"}).status_code for _ in range(40)
    }

    assert statuses == {200}


def test_mode_a_ignores_a_bearer_token_and_creates_no_user(
    client_for, tmp_path: Path
) -> None:
    client = client_for("a")

    response = client.post("/ask", json={"question": "q"}, headers=bearer("stale"))

    assert response.status_code == 200
    engine = create_engine(f"sqlite:///{tmp_path / 'openlaw.sqlite'}")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
    engine.dispose()


def _expiries(tmp_path: Path) -> tuple[list[object], list[object]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'openlaw.sqlite'}")
    with Session(engine) as session:
        documents = list(session.scalars(select(Document.expires_at)))
        conversations = list(session.scalars(select(Conversation.expires_at)))
    engine.dispose()
    return documents, conversations


def test_mode_a_upload_writes_no_expiry(client_for, tmp_path: Path) -> None:
    client = client_for("a")

    body = upload(client).json()

    documents, conversations = _expiries(tmp_path)
    assert body["expires_at"] is None
    assert documents == [None] and conversations == [None]


def test_mode_b_anonymous_upload_expires(client_for, tmp_path: Path) -> None:
    client = client_for("b")

    body = upload(client).json()

    documents, conversations = _expiries(tmp_path)
    assert body["expires_at"] is not None
    assert documents[0] is not None and conversations[0] is not None


def test_mode_b_refusal_offers_signup(client_for) -> None:
    client = client_for("b", signup_url="https://openlaw.in/signup", anonymous_quota=0)

    refusal = client.post("/ask", json={"question": "q"})

    assert refusal.status_code == 429
    assert refusal.json()["signup_url"] == "https://openlaw.in/signup"


def test_no_mode_a_refusal_body_contains_signup_url(client_for) -> None:
    from fastapi import HTTPException

    from openlaw.api.routes import _raise_quota_exceeded, _raise_rate_limited

    settings = _settings_for("a", Path("."), signup_url="https://openlaw.in/signup")
    client = client_for("a", signup_url="https://openlaw.in/signup")
    bodies: list[dict[str, object]] = []
    for raiser in (
        lambda: _raise_rate_limited(None, "q", None, "en", settings, 5),  # type: ignore[arg-type]
        lambda: _raise_quota_exceeded("q", None, "en", True, settings),
    ):
        with pytest.raises(HTTPException) as excinfo:
            raiser()
        bodies.append(excinfo.value.detail)
    # ...and the refusals a Mode A server really produces over HTTP.
    bodies.append(client.post("/ask", json={}).json())
    bodies.append(client.post("/documents").json())

    for body in bodies:
        assert "signup_url" not in json.dumps(body) or body["signup_url"] is None
        assert body.get("signup_required") is not True


def test_the_session_ran_in_the_mode_it_says_it_did(build_mode: str) -> None:
    """The mode knob took effect: a bare `Settings()` reads it back."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
    )

    assert settings.auth_enabled is (build_mode == "b")
    assert settings.quota_enabled is (build_mode == "b")
