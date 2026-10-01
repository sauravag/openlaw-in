"""Tests for the multi-turn conversation HTTP surface (Plan 6 Task 15).

Multi-turn existed in `openlaw.language.pipeline` from Task 9 and was
unreachable: `openlaw.api.deps` built a single-turn `answer_fn` and
`routes.ask` passed no history. These tests fix the reachable contract --
five turns over HTTP, each one seeing the turns before it, each one's
ownership enforced by the same owner-scoped query the data layer uses.

The rendering obligations are tested here too, because a response shape is
the only place they can be enforced: `document_claims` must be VISIBLY apart
from legal `claims` (not merely present in the same list), and
`unverified_summary` must never appear in any body, on any path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openlaw.answers.escalation import CrisisKind

from .conftest import (
    RecordingAnswerFn,
    api_settings,
    bearer,
    build_client,
    localised,
    upload,
    validated_answer,
)

_UNVERIFIED = "UNVERIFIED MODEL PROSE -- MUST NEVER RENDER"


def _ask(
    client: TestClient,
    question: str,
    headers: dict[str, str] | None = None,
    **extra: object,
) -> dict:
    response = client.post(
        "/ask", json={"question": question, **extra}, headers=headers
    )
    assert response.status_code == 200, response.json()
    return response.json()


# --- a conversation over HTTP ----------------------------------------------


def test_a_first_turn_mints_a_conversation_and_reports_turn_zero(
    client: TestClient,
) -> None:
    body = _ask(client, "my landlord kept my deposit")

    assert body["conversation_id"]
    assert body["turn_index"] == 0


def test_five_turns_over_http_each_see_the_turns_before_them(tmp_path: Path) -> None:
    answer_fn = RecordingAnswerFn()
    client = build_client(tmp_path, answer_fn=answer_fn)

    first = _ask(client, "turn one")
    conversation_id = first["conversation_id"]
    for index in range(1, 5):
        body = _ask(client, f"turn {index + 1}", conversation_id=conversation_id)
        assert body["conversation_id"] == conversation_id
        assert body["turn_index"] == index

    assert [len(call["history"]) for call in answer_fn.calls] == [0, 1, 2, 3, 4]
    assert answer_fn.calls[-1]["history"][0].question == "turn one"


def test_a_foreign_conversation_id_on_ask_is_a_404_carrying_help(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path)
    mine = _ask(client, "turn one", **{})
    client.cookies.clear()

    response = client.post(
        "/ask",
        json={"question": "what did I just ask?", "conversation_id": mine["conversation_id"]},
        headers=bearer("someone-else", "else@example.com"),
    )

    assert response.status_code == 404
    body = response.json()
    assert body["escalation_footer"] and body["helplines"]


def test_get_conversation_returns_the_turns_the_caller_owns(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    first = _ask(client, "my landlord kept my deposit")
    _ask(client, "what next?", conversation_id=first["conversation_id"])

    response = client.get(f"/conversations/{first['conversation_id']}")

    assert response.status_code == 200
    body = response.json()
    assert [turn["question"] for turn in body["turns"]] == [
        "my landlord kept my deposit",
        "what next?",
    ]
    assert [turn["turn_index"] for turn in body["turns"]] == [0, 1]
    assert _UNVERIFIED not in response.text


def test_get_a_foreign_conversation_is_a_404(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    mine = _ask(client, "turn one")

    response = client.get(
        f"/conversations/{mine['conversation_id']}",
        headers=bearer("someone-else", "else@example.com"),
    )

    assert response.status_code == 404
    assert response.json()["helplines"]


def test_get_conversation_lists_the_documents_still_attached(tmp_path: Path) -> None:
    """The reload path for per-document deletion (Plan 6 Task 18).

    An authenticated upload is retained until its owner deletes it, and this
    list is the only thing that makes the per-document control reachable after
    a refresh.
    """
    client = build_client(tmp_path)
    document = upload(client, filename="lease.txt", headers=bearer()).json()

    response = client.get(
        f"/conversations/{document['conversation_id']}", headers=bearer()
    )

    assert response.status_code == 200
    listed = response.json()["documents"]
    assert [entry["id"] for entry in listed] == [document["id"]]
    assert listed[0]["filename"] == "lease.txt"
    assert listed[0]["page_count"] == document["page_count"]


def test_a_listed_document_carries_no_content_and_no_url(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    document = upload(client, headers=bearer()).json()

    response = client.get(
        f"/conversations/{document['conversation_id']}", headers=bearer()
    )

    entry = response.json()["documents"][0]
    assert set(entry) == {
        "id",
        "filename",
        "page_count",
        "expires_at",
        "retention_notice",
    }
    # The uploaded text is in `tests/api/conftest.NOTICE_TEXT`; no fragment of
    # it may reach a listing, nor may anything that looks like a source link.
    assert "security deposit" not in response.text
    assert "source_url" not in response.text


def test_a_listed_document_can_then_be_deleted_individually(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    first = upload(client, filename="a.txt", headers=bearer()).json()
    conversation_id = first["conversation_id"]
    second = upload(
        client, filename="b.txt", conversation_id=conversation_id, headers=bearer()
    ).json()

    assert (
        client.delete(f"/documents/{first['id']}", headers=bearer()).status_code == 204
    )

    remaining = client.get(
        f"/conversations/{conversation_id}", headers=bearer()
    ).json()["documents"]
    assert [entry["id"] for entry in remaining] == [second["id"]]


def test_an_anonymous_listing_repeats_the_expiry_promise(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    document = upload(client).json()

    entry = client.get(
        f"/conversations/{document['conversation_id']}"
    ).json()["documents"][0]

    assert entry["expires_at"] is not None
    assert entry["retention_notice"] == document["retention_notice"]


def test_a_hindi_listing_renders_the_retention_notice_in_hindi(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    document = upload(client, headers=bearer()).json()

    entry = client.get(
        f"/conversations/{document['conversation_id']}?language=hi",
        headers=bearer(),
    ).json()["documents"][0]

    assert entry["retention_notice"] != document["retention_notice"]
    assert any("\u0900" <= char <= "\u097f" for char in entry["retention_notice"])


def test_a_listing_never_shows_another_identity_their_documents(tmp_path: Path) -> None:
    """A conversation one identity owns, holding a document another uploaded,
    cannot exist -- but the listing is owner-scoped independently anyway."""
    client = build_client(tmp_path)
    mine = upload(client, headers=bearer("user-1", "one@example.com")).json()

    response = client.get(
        f"/conversations/{mine['conversation_id']}",
        headers=bearer("user-2", "two@example.com"),
    )

    assert response.status_code == 404
    assert response.json()["helplines"]


def test_delete_a_conversation_removes_its_turns_and_documents(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    document = upload(client, headers=bearer()).json()
    conversation_id = document["conversation_id"]

    assert client.delete(f"/conversations/{conversation_id}", headers=bearer()).status_code == 204
    assert client.get(f"/conversations/{conversation_id}", headers=bearer()).status_code == 404
    assert client.delete(f"/documents/{document['id']}", headers=bearer()).status_code == 404


def test_delete_a_foreign_conversation_is_a_404(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    mine = _ask(client, "turn one")

    response = client.delete(
        f"/conversations/{mine['conversation_id']}",
        headers=bearer("someone-else", "else@example.com"),
    )

    assert response.status_code == 404
    assert response.json()["helplines"]


# --- documents in a live turn ----------------------------------------------


def test_an_attached_document_reaches_the_answer_function_as_context(
    tmp_path: Path,
) -> None:
    answer_fn = RecordingAnswerFn()
    client = build_client(tmp_path, answer_fn=answer_fn)
    document = upload(client, headers=bearer()).json()

    _ask(
        client,
        "must my landlord refund the deposit?",
        headers=bearer(),
        conversation_id=document["conversation_id"],
        document_ids=[document["id"]],
    )

    context = answer_fn.calls[-1]["document_context"]
    assert context is not None and context.excerpts, "no document excerpt reached the turn"


def test_a_document_derived_crisis_answers_normally_and_attaches_helplines(
    tmp_path: Path,
) -> None:
    """Task 10 ruling 1, now reachable: a clean question with a protection
    order attached gets a real legal answer AND the helpline card. The stored
    verdict must be threaded in on EVERY turn, not only the upload turn."""
    answer_fn = RecordingAnswerFn(
        result=localised(
            validated_answer(),
            **{"document_crisis": CrisisKind.DOMESTIC_VIOLENCE,
               "helplines": ()},
        )
    )
    client = build_client(tmp_path, answer_fn=answer_fn)
    order = b"FIR copy: the complainant states that her husband beats her daily."
    document = upload(client, content=order, headers=bearer()).json()

    body = _ask(
        client,
        "can I claim my security deposit back?",
        headers=bearer(),
        conversation_id=document["conversation_id"],
        document_ids=[document["id"]],
    )

    assert answer_fn.calls[-1]["document_crisis_kinds"] == ("domestic_violence",)
    assert body["is_answerable"] is True
    assert "deposit" in body["answer"]
    assert body["crisis_response"]


def test_a_stored_verdict_is_threaded_on_a_later_turn_with_no_new_upload(
    tmp_path: Path,
) -> None:
    answer_fn = RecordingAnswerFn()
    client = build_client(tmp_path, answer_fn=answer_fn)
    order = b"FIR copy: the complainant states that her husband beats her daily."
    document = upload(client, content=order, headers=bearer()).json()

    _ask(client, "first question", headers=bearer(),
         conversation_id=document["conversation_id"],
         document_ids=[document["id"]])
    _ask(client, "second question", headers=bearer(),
         conversation_id=document["conversation_id"],
         document_ids=[document["id"]])

    assert answer_fn.calls[-1]["document_crisis_kinds"] == ("domestic_violence",)


# --- rendering: document claims are visibly apart from legal claims --------


def test_document_claims_render_apart_from_legal_claims(tmp_path: Path) -> None:
    answer = validated_answer(
        claim_text="A deposit must be refunded within thirty days.",
        document_claim_text="Your notice is dated 1 March and gives fifteen days.",
    )
    client = build_client(tmp_path, answer_fn=RecordingAnswerFn(result=localised(answer)))

    body = _ask(client, "what does my notice say?")

    assert "thirty days" in body["answer"]
    assert "1 March" not in body["answer"], (
        "a document claim rendered inside `answer` would read as law"
    )
    assert "1 March" in body["document_answer"]
    assert "thirty days" not in body["document_answer"]
    assert body["document_citations"] == [
        {
            "excerpt_id": "doc:d1:p1:0",
            "document_id": "d1",
            "filename": "notice.txt",
            "page_number": 1,
        }
    ]


def test_a_document_citation_card_has_no_source_url(tmp_path: Path) -> None:
    answer = validated_answer(document_claim_text="Your notice gives fifteen days.")
    client = build_client(tmp_path, answer_fn=RecordingAnswerFn(result=localised(answer)))

    body = _ask(client, "what does my notice say?")

    assert "source_url" not in body["document_citations"][0]
    assert all("doc:" not in source["chunk_id"] for source in body["sources"])


def test_document_answer_is_absent_when_nothing_was_uploaded(
    client: TestClient,
) -> None:
    body = _ask(client, "my landlord kept my deposit")

    assert body["document_answer"] is None
    assert body["document_citations"] == []


def test_no_response_body_ever_contains_the_unverified_summary(tmp_path: Path) -> None:
    answer = validated_answer(document_claim_text="Your notice gives fifteen days.")
    client = build_client(tmp_path, answer_fn=RecordingAnswerFn(result=localised(answer)))

    response = client.post("/ask", json={"question": "what does my notice say?"})

    assert _UNVERIFIED not in response.text


def test_a_hindi_turn_labels_the_document_section_in_hindi(tmp_path: Path) -> None:
    answer = validated_answer(document_claim_text="आपका नोटिस पंद्रह दिन देता है।")
    client = build_client(
        tmp_path,
        answer_fn=RecordingAnswerFn(result=localised(answer, language="hi")),
    )

    body = _ask(client, "mera notice kya kehta hai?", language="hi")

    assert body["document_answer"] is not None
    heading = body["document_answer"].splitlines()[0]
    assert not heading.isascii(), f"document heading leaked English: {heading!r}"


# --- the /ask contract that must not regress -------------------------------


def test_quota_is_consumed_before_the_answer_function_runs(tmp_path: Path) -> None:
    settings = api_settings(tmp_path, anonymous_quota=0)
    answer_fn = RecordingAnswerFn()
    client = build_client(tmp_path, settings=settings, answer_fn=answer_fn)

    response = client.post("/ask", json={"question": "my landlord kept my deposit"})

    assert response.status_code == 429
    assert answer_fn.calls == []


# --- boundary validation ---------------------------------------------------


def test_duplicate_document_ids_are_rejected_at_the_boundary(
    client: TestClient,
) -> None:
    repeated = "0f1f2f3f-4f5f-6f7f-8f9f-aabbccddeeff"
    response = client.post(
        "/ask", json={"question": "hello?", "document_ids": [repeated, repeated]}
    )

    assert response.status_code == 422
    assert response.json()["helplines"]


def test_an_explicit_null_conversation_id_starts_a_new_conversation(
    client: TestClient,
) -> None:
    body = _ask(client, "my landlord kept my deposit", conversation_id=None)

    assert body["conversation_id"]
    assert body["turn_index"] == 0


def test_a_non_uuid_conversation_id_is_rejected_at_the_boundary(
    client: TestClient,
) -> None:
    """A conversation id reaches a database lookup and is echoed back in the
    response, so it is parsed as a UUID at the boundary like every other
    untrusted field -- never trusted downstream."""
    response = client.post(
        "/ask", json={"question": "hello?", "conversation_id": "../../etc/passwd"}
    )

    assert response.status_code == 422
    assert response.json()["helplines"]


def test_a_reloaded_transcript_keeps_document_claims_apart_from_legal_ones(
    tmp_path: Path,
) -> None:
    answer = validated_answer(
        claim_text="A deposit must be refunded within thirty days.",
        document_claim_text="Your notice is dated 1 March.",
    )
    client = build_client(tmp_path, answer_fn=RecordingAnswerFn(result=localised(answer)))
    document = upload(client, headers=bearer()).json()
    _ask(
        client,
        "what does my notice say?",
        headers=bearer(),
        conversation_id=document["conversation_id"],
        document_ids=[document["id"]],
    )

    turn = client.get(
        f"/conversations/{document['conversation_id']}", headers=bearer()
    ).json()["turns"][0]

    assert "thirty days" in turn["answer"]
    assert "1 March" not in turn["answer"]
    assert "1 March" in turn["document_answer"]
