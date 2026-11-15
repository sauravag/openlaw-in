"""`GET /conversations` (final review H1b): a signed-in owner's own list.

The listing is what lets a person recover a "kept" conversation on a new
device, or after a session lapse made the page forget its id. It carries ids,
times and a document count only: never a question, an answer or a filename.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openlaw.api.conversation_list import (
    CONVERSATION_LIST_LIMIT,
    CONVERSATION_LIST_MAX_LIMIT,
)
from tests.api.conftest import NOTICE_TEXT, api_settings, bearer, build_client, upload
from tests.api.test_auth_routes import SUPABASE_URL, _mode_a


def _mode_b(tmp_path: Path) -> TestClient:
    return build_client(tmp_path, api_settings(tmp_path, supabase_url=SUPABASE_URL))


def _ask(client: TestClient, headers: dict[str, str] | None = None) -> str:
    response = client.post("/ask", json={"question": "my deposit"}, headers=headers)
    assert response.status_code == 200
    return response.json()["conversation_id"]


def test_is_404_in_mode_a(tmp_path: Path) -> None:
    assert _mode_a(tmp_path).get("/conversations", headers=bearer()).status_code == 404


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic x"}]
)
def test_an_anonymous_caller_gets_the_sign_in_refusal_with_help(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    client = _mode_b(tmp_path)
    _ask(client)  # an anonymous conversation exists, and must not be listed

    response = client.get("/conversations?language=hi", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    body = response.json()
    assert body["escalation_footer"] and body["helplines"]
    assert any(line["number"] == "15100" for line in body["helplines"])
    assert "साइन इन" in body["detail"]


def test_lists_only_the_callers_own_conversations_with_metadata_only(
    tmp_path: Path,
) -> None:
    client = _mode_b(tmp_path)
    mine = bearer("user-1", "one@example.com")
    first = _ask(client, mine)
    second = upload(client, headers=mine).json()["conversation_id"]
    upload(client, headers=mine, conversation_id=second)
    _ask(client, bearer("user-2", "two@example.com"))
    _ask(client)  # anonymous

    response = client.get("/conversations", headers=mine)

    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body["conversations"]}
    assert set(by_id) == {first, second}
    assert by_id[first]["document_count"] == 0
    assert by_id[second]["document_count"] == 2
    for item in body["conversations"]:
        assert set(item) == {"id", "created_at", "updated_at", "document_count"}
    assert body["next_offset"] is None
    assert "my deposit" not in response.text
    assert NOTICE_TEXT.decode()[:20] not in response.text
    assert "notice.txt" not in response.text


def test_is_paginated_with_a_named_limit(tmp_path: Path) -> None:
    client = _mode_b(tmp_path)
    mine = bearer()
    ids = {_ask(client, mine) for _ in range(3)}

    first = client.get("/conversations?limit=2", headers=mine).json()
    second = client.get(
        f"/conversations?limit=2&offset={first['next_offset']}", headers=mine
    ).json()

    assert len(first["conversations"]) == 2 and first["next_offset"] == 2
    assert len(second["conversations"]) == 1 and second["next_offset"] is None
    seen = {item["id"] for item in first["conversations"] + second["conversations"]}
    assert seen == ids
    assert 1 <= CONVERSATION_LIST_LIMIT <= CONVERSATION_LIST_MAX_LIMIT


def test_a_limit_above_the_maximum_is_refused_with_help(tmp_path: Path) -> None:
    client = _mode_b(tmp_path)

    response = client.get(
        f"/conversations?limit={CONVERSATION_LIST_MAX_LIMIT + 1}", headers=bearer()
    )

    assert response.status_code == 422
    assert response.json()["helplines"]


def test_a_signed_in_caller_with_nothing_gets_an_empty_list(tmp_path: Path) -> None:
    response = _mode_b(tmp_path).get("/conversations", headers=bearer("new-user"))

    assert response.status_code == 200
    assert response.json() == {"conversations": [], "next_offset": None}
