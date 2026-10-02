"""Cross-account access, driven at the HTTP boundary (Plan 6 Task 18).

Two token subjects, every document and conversation endpoint, in both
directions. The property is uniform and deliberately blunt: **a resource you do
not own is indistinguishable from one that does not exist.**

Why 404 and never 403: a 403 confirms the id exists, which turns
`DELETE /documents/{id}` and `GET /conversations/{id}` into enumeration
oracles. Every assertion here therefore checks the status code AND that the
body still carries help -- a refusal is somebody's last screen even when it is
an attacker's.

The anonymous half matters as much as the authenticated half. Anonymous
ownership is a cookie, so "a second anonymous visitor" is modelled as a second
client with its own cookie jar, which is exactly what a different browser is.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.api.conftest import api_settings, bearer, build_client, upload

_ALICE = {"subject": "alice-subject", "email": "alice@example.com"}
_BOB = {"subject": "bob-subject", "email": "bob@example.com"}


def _alice() -> dict[str, str]:
    return bearer(_ALICE["subject"], _ALICE["email"])


def _bob() -> dict[str, str]:
    return bearer(_BOB["subject"], _BOB["email"])


def _ask(client: TestClient, headers: dict[str, str], **extra: object) -> dict:
    response = client.post(
        "/ask", json={"question": "my landlord kept my deposit", **extra}, headers=headers
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _assert_refused_as_absent(response) -> None:
    """A cross-owner access is a 404 that still delivers help."""
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["helplines"], "a refusal reached a person with no help on it"
    assert body["escalation_footer"]
    # A 403 would confirm existence; so would any mention of ownership.
    assert "forbidden" not in response.text.lower()


# --- documents ---------------------------------------------------------------


def test_bob_cannot_delete_alices_document(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    document = upload(client, headers=_alice()).json()

    _assert_refused_as_absent(
        client.delete(f"/documents/{document['id']}", headers=_bob())
    )
    # ...and Alice's document is still there afterwards.
    assert client.delete(f"/documents/{document['id']}", headers=_alice()).status_code == 204


def test_bob_cannot_read_alices_document_through_ask(tmp_path: Path) -> None:
    """The one path that returns document TEXT. `document_ids` naming a
    document Bob does not own must refuse the whole turn, never answer from the
    documents he does own while silently dropping the one he does not."""
    client = build_client(tmp_path)
    document = upload(client, headers=_alice()).json()
    bobs = upload(client, filename="bobs.txt", headers=_bob()).json()

    response = client.post(
        "/ask",
        json={
            "question": "what does my notice say?",
            "conversation_id": bobs["conversation_id"],
            "document_ids": [document["id"]],
        },
        headers=_bob(),
    )

    _assert_refused_as_absent(response)
    assert "security deposit" not in response.text


def test_bob_cannot_attach_alices_document_to_his_own_conversation(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path)
    alices = upload(client, headers=_alice()).json()
    bobs = _ask(client, _bob())

    _assert_refused_as_absent(
        client.post(
            "/ask",
            json={
                "question": "and this document?",
                "conversation_id": bobs["conversation_id"],
                "document_ids": [alices["id"]],
            },
            headers=_bob(),
        )
    )


def test_bob_cannot_upload_into_alices_conversation(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    alices = _ask(client, _alice())

    _assert_refused_as_absent(
        upload(client, conversation_id=alices["conversation_id"], headers=_bob())
    )


# --- conversations -----------------------------------------------------------


def test_bob_cannot_read_alices_conversation(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    alices = _ask(client, _alice())

    _assert_refused_as_absent(
        client.get(f"/conversations/{alices['conversation_id']}", headers=_bob())
    )


def test_bob_cannot_list_the_documents_in_alices_conversation(tmp_path: Path) -> None:
    """The listing added by this task is a new read surface, so it gets the
    same treatment as every other: a filename is personal data too."""
    client = build_client(tmp_path)
    alices = upload(client, filename="alices-fir.txt", headers=_alice()).json()

    response = client.get(
        f"/conversations/{alices['conversation_id']}", headers=_bob()
    )

    _assert_refused_as_absent(response)
    assert "alices-fir.txt" not in response.text


def test_bob_cannot_delete_alices_conversation(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    alices = _ask(client, _alice())

    _assert_refused_as_absent(
        client.delete(f"/conversations/{alices['conversation_id']}", headers=_bob())
    )
    assert (
        client.get(
            f"/conversations/{alices['conversation_id']}", headers=_alice()
        ).status_code
        == 200
    )


def test_bob_cannot_continue_alices_conversation(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    alices = _ask(client, _alice())

    _assert_refused_as_absent(
        client.post(
            "/ask",
            json={
                "question": "what did I just ask?",
                "conversation_id": alices["conversation_id"],
            },
            headers=_bob(),
        )
    )


# --- anonymous identities are separated the same way -------------------------


def test_a_second_anonymous_browser_cannot_read_the_first_ones_conversation(
    tmp_path: Path,
) -> None:
    """Two clients over one database: separate cookie jars, which is what two
    browsers actually are."""
    settings = api_settings(tmp_path)
    first = build_client(tmp_path, settings=settings)
    second = build_client(tmp_path, settings=settings)
    theirs = _ask(first, {})

    _assert_refused_as_absent(
        second.get(f"/conversations/{theirs['conversation_id']}")
    )


def test_a_second_anonymous_browser_cannot_delete_the_first_ones_document(
    tmp_path: Path,
) -> None:
    settings = api_settings(tmp_path)
    first = build_client(tmp_path, settings=settings)
    second = build_client(tmp_path, settings=settings)
    document = upload(first).json()

    _assert_refused_as_absent(second.delete(f"/documents/{document['id']}"))
    assert first.delete(f"/documents/{document['id']}").status_code == 204


def test_signing_in_does_not_inherit_an_anonymous_visitors_document(
    tmp_path: Path,
) -> None:
    """An anonymous upload is owned by a cookie, and a bearer token is a
    different identity -- not an upgrade path. Stated as a test because the
    opposite behaviour would look like a feature."""
    client = build_client(tmp_path)
    anonymous = upload(client).json()

    _assert_refused_as_absent(
        client.get(f"/conversations/{anonymous['conversation_id']}", headers=_alice())
    )
    _assert_refused_as_absent(
        client.delete(f"/documents/{anonymous['id']}", headers=_alice())
    )


# --- the denial is recorded --------------------------------------------------


def test_every_denied_document_access_leaves_an_audit_row(tmp_path: Path) -> None:
    """A denial is the request most likely to end in a rollback, which is
    exactly the row an audit trail exists to keep -- see
    `openlaw.api.turns.durable_audit`."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from openlaw.db.models import DocumentAccessLog

    settings = api_settings(tmp_path)
    client = build_client(tmp_path, settings=settings)
    document = upload(client, headers=_alice()).json()

    client.delete(f"/documents/{document['id']}", headers=_bob())

    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        denied = (
            session.query(DocumentAccessLog)
            .filter(DocumentAccessLog.action == "denied")
            .all()
        )
    assert denied, "a cross-owner denial left no audit trace"
    assert all(row.document_id == document["id"] for row in denied)
