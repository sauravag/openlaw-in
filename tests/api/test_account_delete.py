"""`DELETE /account` (Plan 7 Task 11): erase the caller's own account."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from openlaw.api import auth_routes
from openlaw.api.app import create_app
from openlaw.api.auth import AuthError
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
    NOTICE_TEXT,
    RecordingAnswerFn,
    api_settings,
    bearer,
    build_client,
)
from tests.api.test_auth_routes import SUPABASE_URL, _mode_a

KEY = "service-role-key-DO-NOT-LEAK"


def _seed(settings: Settings, user_id: str, email: str) -> list[str]:
    """One user with one conversation and one document; returns the doc ids."""
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            session.add(User(id=user_id, email=email))
            session.flush()
            owner = Owner(user_id=user_id, anon_token=None)
            conversation = create_conversation(session, owner, settings)
            document = store_document(
                session, owner, conversation.id, "n.txt", "text/plain", NOTICE_TEXT, settings
            )
            ids = [document.id]
            session.commit()
            return ids
    finally:
        engine.dispose()


def _rows(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            return {
                "users": session.scalar(select(func.count()).select_from(User)),
                "conversations": session.scalar(
                    select(func.count()).select_from(Conversation)
                ),
                "documents": session.scalar(select(func.count()).select_from(Document)),
                "audit": session.scalar(
                    select(func.count()).select_from(DocumentAccessLog)
                ),
            }
    finally:
        engine.dispose()


def _client(tmp_path: Path, **overrides: object) -> tuple[TestClient, Settings]:
    settings = api_settings(tmp_path, supabase_url=SUPABASE_URL, **overrides)
    return build_client(tmp_path, settings), settings


def test_delete_account_is_404_in_mode_a(tmp_path: Path) -> None:
    assert _mode_a(tmp_path).delete("/account").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic abc"}],
)
def test_requires_a_verified_bearer(tmp_path: Path, headers: dict[str, str]) -> None:
    client, settings = _client(tmp_path)
    _seed(settings, "user-1", "one@example.com")

    response = client.delete("/account", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    body = response.json()
    assert body["escalation_footer"] and body["helplines"]
    assert _rows(settings)["conversations"] == 1


def test_an_unverifiable_token_detail_is_not_echoed(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.delete("/account", headers={"Authorization": "Bearer junk.jwt.x"})
    assert "signature" not in response.text.lower()
    assert AuthError.__name__ not in response.text


def test_deletes_only_the_token_subjects_data_never_an_id_from_the_request(
    tmp_path: Path,
) -> None:
    client, settings = _client(tmp_path)
    _seed(settings, "user-1", "one@example.com")
    _seed(settings, "user-2", "two@example.com")

    response = client.request(
        "DELETE",
        "/account?user_id=user-2&id=user-2",
        headers=bearer("user-1", "one@example.com"),
        content='{"user_id": "user-2"}',
    )

    assert response.status_code == 200
    assert _rows(settings)["users"] == 1
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            assert session.get(User, "user-2") is not None
            assert session.get(User, "user-1") is None
            assert {c.user_id for c in session.query(Conversation)} == {"user-2"}
    finally:
        engine.dispose()


def test_returns_counts_and_honest_scope_when_no_service_key(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    doc_ids = _seed(settings, "user-1", "one@example.com")

    response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    body = response.json()
    assert response.status_code == 200
    assert body["conversations_deleted"] == 1
    assert body["documents_deleted"] == 1
    assert body["messages_deleted"] == 0
    assert body["audit_rows_retained"] >= 2  # the create row and the new delete row
    assert body["identity_removed"] is False
    assert doc_ids
    note = body["note_en"].lower()
    assert "access log" in note and "sign-in" in note and "remain" in note
    assert body["note_hi"].strip()
    assert body["note_hi"] != body["note_en"]


def test_second_call_succeeds_with_zeros(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    _seed(settings, "user-1", "one@example.com")
    headers = bearer("user-1", "one@example.com")
    client.delete("/account", headers=headers)

    response = client.delete("/account", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["conversations_deleted"], body["documents_deleted"]) == (0, 0)
    assert (body["messages_deleted"], body["audit_rows_retained"]) == (0, 0)


def test_identity_is_removed_after_the_local_commit_and_reported(tmp_path: Path) -> None:
    client, settings = _client(tmp_path, supabase_service_role_key=KEY)
    _seed(settings, "user-1", "one@example.com")
    seen: dict[str, object] = {}

    def fake_remove(passed_settings: Settings, user_id: str) -> bool:
        seen["user_id"] = user_id
        seen["rows_at_call"] = _rows(settings)  # must already be committed away
        return True

    with patch.object(auth_routes, "remove_supabase_identity", fake_remove):
        response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    body = response.json()
    assert seen["user_id"] == "user-1"
    assert seen["rows_at_call"]["users"] == 0 and seen["rows_at_call"]["conversations"] == 0
    assert body["identity_removed"] is True
    assert "remain" not in body["note_en"].lower()
    assert KEY not in response.text


def test_identity_failure_says_data_erased_but_identity_not_removed(tmp_path: Path) -> None:
    client, settings = _client(tmp_path, supabase_service_role_key=KEY)
    _seed(settings, "user-1", "one@example.com")

    with patch.object(auth_routes, "remove_supabase_identity", return_value=False):
        response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    body = response.json()
    assert response.status_code == 200
    assert body["identity_removed"] is False
    assert _rows(settings)["conversations"] == 0
    assert "erased" in body["note_en"].lower()
    assert "could not be removed" in body["note_en"].lower()
    assert "has also been removed" not in body["note_en"].lower()
    assert body["note_hi"].strip() and body["note_hi"] != body["note_en"]
    assert KEY not in response.text


def test_a_non_httpx_identity_error_still_returns_200_with_data_erased(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from openlaw.accounts import identity

    client, settings = _client(tmp_path, supabase_service_role_key=KEY)
    _seed(settings, "user-1", "one@example.com")
    boom = UnicodeEncodeError("ascii", KEY, 0, 1, "bad")

    with patch.object(identity.httpx, "Client", side_effect=boom):
        response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    body = response.json()
    assert response.status_code == 200
    assert body["identity_removed"] is False
    assert body["conversations_deleted"] == 1
    assert _rows(settings)["conversations"] == 0
    assert "could not be removed" in body["note_en"].lower()
    assert KEY not in response.text and KEY not in caplog.text


def test_delete_401_says_nothing_was_deleted_in_both_languages(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.delete("/account")
    from openlaw.api.refusal import refusal_detail

    hindi = refusal_detail("account_delete_sign_in_required", "hi")

    assert "nothing was deleted" in response.json()["detail"].lower()
    assert "moved" not in response.json()["detail"].lower()
    assert "कुछ भी नहीं हटाया गया" in hindi


def test_the_identity_call_is_skipped_when_the_local_erasure_fails(tmp_path: Path) -> None:
    client, settings = _client(tmp_path, supabase_service_role_key=KEY)
    _seed(settings, "user-1", "one@example.com")

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    with (
        patch.object(auth_routes, "delete_account", explode),
        patch.object(auth_routes, "remove_supabase_identity") as remove,
    ):
        response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    assert response.status_code == 500
    remove.assert_not_called()
    assert _rows(settings)["conversations"] == 1
    assert KEY not in response.text


def test_a_failure_after_the_deletes_rolls_the_whole_erasure_back(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    _seed(settings, "user-1", "one@example.com")
    real = auth_routes.delete_account

    def delete_then_fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("boom")

    with patch.object(auth_routes, "delete_account", delete_then_fail):
        response = client.delete("/account", headers=bearer("user-1", "one@example.com"))

    assert response.status_code == 500
    rows = _rows(settings)
    assert rows["conversations"] == 1 and rows["documents"] == 1 and rows["users"] == 1


@pytest.mark.integration
def test_delete_account_through_the_endpoint_on_live_postgres(
    live_engine, tmp_path: Path
) -> None:
    """The brief's 'Done when': rows gone, access log survives."""
    Base.metadata.create_all(live_engine, checkfirst=True)
    assert live_engine.url.database == "openlaw_test"
    subject = f"00000000-0000-4000-8000-{time.time_ns() % 10**12:012d}"
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

    document_id: str | None = None
    try:
        with Session(live_engine) as seed:
            seed.add(User(id=subject, email=f"{subject}@example.com"))
            seed.flush()
            owner = Owner(user_id=subject, anon_token=None)
            conversation = create_conversation(seed, owner, settings)
            document = store_document(
                seed, owner, conversation.id, "n.txt", "text/plain", NOTICE_TEXT, settings
            )
            document_id = document.id
            seed.commit()

        client = TestClient(
            create_app(RecordingAnswerFn(), session_factory, settings),
            raise_server_exceptions=False,
        )
        response = client.delete(
            "/account", headers=bearer(subject, f"{subject}@example.com")
        )

        assert response.status_code == 200
        body = response.json()
        assert (body["conversations_deleted"], body["documents_deleted"]) == (1, 1)
        with Session(live_engine) as check:
            assert check.get(User, subject) is None
            assert check.get(Document, document_id) is None
            actions = [
                row.action
                for row in check.query(DocumentAccessLog).filter(
                    DocumentAccessLog.document_id == document_id
                )
            ]
            assert "delete" in actions
            assert body["audit_rows_retained"] == len(actions)
    finally:
        with Session(live_engine) as cleanup:
            if document_id is not None:
                cleanup.query(DocumentAccessLog).filter(
                    DocumentAccessLog.document_id == document_id
                ).delete()
            cleanup.query(User).filter(User.id == subject).delete()
            cleanup.commit()
