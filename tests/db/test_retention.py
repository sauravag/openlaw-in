"""Tests for `openlaw.db.retention`: purge helpers for the hallucination-log
table, and (Plan 6 Task 16) for `documents`, `conversations`, and
`document_access_log`.

Mirrors `tests/answers/test_escalation.py`'s coverage of `purge_stale_events`
(the JSONL sibling): a row inside the retention window survives, a row
outside it is deleted, and the default matches the documented 90 days.

The Task 16 tests below exist to prove the one asymmetry the whole feature
depends on: a NULL `expires_at` (an authenticated upload or conversation)
survives every purge here, regardless of age, while an anonymous row past
its TTL does not -- see `openlaw.db.retention`'s module docstring for why
that split exists at all.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    HallucinationLog,
)
from openlaw.db.retention import (
    DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
    purge_expired_conversations,
    purge_expired_documents,
    purge_stale_document_audit,
    purge_stale_hallucination_log,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _event(days_old: int) -> HallucinationLog:
    return HallucinationLog(
        question="q",
        claim_text="c",
        dropped_chunk_ids=[],
        retrieved_chunk_ids=[],
        created_at=datetime.now(UTC) - timedelta(days=days_old),
    )


def _expired(hours_ago: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours_ago)


def _document(conversation_id: str, **overrides: object) -> Document:
    defaults: dict = {
        "conversation_id": conversation_id,
        "filename": "notice.pdf",
        "content_type": "application/pdf",
        "byte_size": 1024,
        "page_count": 1,
        "ciphertext": b"cipher",
        "nonce": b"nonce-bytes-",
        "key_id": "0" * 16,
    }
    defaults.update(overrides)
    return Document(**defaults)


def test_default_retention_window_is_90_days() -> None:
    """Must match the JSONL log's documented window -- see the module docstring."""
    assert DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS == 90


def test_purge_deletes_rows_older_than_the_window() -> None:
    session = _session()
    session.add(_event(days_old=91))
    session.commit()

    removed = purge_stale_hallucination_log(session, max_age_days=90)
    session.commit()

    assert removed == 1
    assert session.query(HallucinationLog).count() == 0


def test_purge_keeps_rows_within_the_window() -> None:
    session = _session()
    session.add(_event(days_old=1))
    session.commit()

    removed = purge_stale_hallucination_log(session, max_age_days=90)
    session.commit()

    assert removed == 0
    assert session.query(HallucinationLog).count() == 1


def test_purge_returns_zero_when_the_table_is_empty() -> None:
    session = _session()
    assert purge_stale_hallucination_log(session) == 0


# --- purge_expired_documents (Plan 6 Task 16) -------------------------------


def test_authenticated_document_is_never_purged_regardless_of_age() -> None:
    """The single most important assertion in this module: a signed-in
    user's eviction notice must not vanish on a timer."""
    session = _session()
    conversation = Conversation(user_id="user-1")
    session.add(conversation)
    session.flush()
    session.add(_document(conversation.id, user_id="user-1", expires_at=None))
    session.commit()

    removed = purge_expired_documents(session)
    session.commit()

    assert removed == 0
    assert session.query(Document).count() == 1


def test_expired_anonymous_document_is_purged_and_its_audit_rows_survive() -> None:
    session = _session()
    conversation = Conversation(anon_token="anon-1")
    session.add(conversation)
    session.flush()
    document = _document(conversation.id, anon_token="anon-1", expires_at=_expired())
    session.add(document)
    session.flush()
    session.add(
        DocumentAccessLog(
            document_id=document.id,
            actor_kind="anon",
            actor_ref="anon-1",
            action="create",
        )
    )
    session.commit()
    document_id = document.id

    removed = purge_expired_documents(session)
    session.commit()

    assert removed == 1
    assert session.query(Document).count() == 0
    survivors = session.query(DocumentAccessLog).filter_by(document_id=document_id)
    assert survivors.count() == 1


def test_authenticated_document_of_identical_age_is_not_purged_alongside_an_expired_anonymous_one() -> (  # noqa: E501
    None
):
    session = _session()
    anon_conversation = Conversation(anon_token="anon-1")
    auth_conversation = Conversation(user_id="user-1")
    session.add_all([anon_conversation, auth_conversation])
    session.flush()
    old_created_at = datetime.now(UTC) - timedelta(days=365)
    session.add(
        _document(
            anon_conversation.id,
            anon_token="anon-1",
            expires_at=_expired(),
            created_at=old_created_at,
        )
    )
    session.add(
        _document(
            auth_conversation.id,
            user_id="user-1",
            expires_at=None,
            created_at=old_created_at,
        )
    )
    session.commit()

    removed = purge_expired_documents(session)
    session.commit()

    assert removed == 1
    remaining = session.query(Document).one()
    assert remaining.user_id == "user-1"


def test_purging_documents_twice_in_a_row_is_idempotent() -> None:
    session = _session()
    conversation = Conversation(anon_token="anon-1")
    session.add(conversation)
    session.flush()
    session.add(_document(conversation.id, anon_token="anon-1", expires_at=_expired()))
    session.commit()

    first = purge_expired_documents(session)
    session.commit()
    second = purge_expired_documents(session)
    session.commit()

    assert first == 1
    assert second == 0


# --- purge_expired_conversations (Plan 6 Task 16) ---------------------------


def test_authenticated_conversation_is_never_purged() -> None:
    session = _session()
    session.add(Conversation(user_id="user-1", expires_at=None))
    session.commit()

    removed = purge_expired_conversations(session)
    session.commit()

    assert removed == 0
    assert session.query(Conversation).count() == 1


def test_expired_anonymous_conversation_purges_its_documents_too() -> None:
    session = _session()
    conversation = Conversation(anon_token="anon-1", expires_at=_expired())
    session.add(conversation)
    session.flush()
    session.add(
        _document(conversation.id, anon_token="anon-1", expires_at=None)
    )  # not itself expired yet -- the conversation's lifetime still bounds it
    session.commit()

    removed = purge_expired_conversations(session)
    session.commit()

    assert removed == 1
    assert session.query(Conversation).count() == 0
    assert session.query(Document).count() == 0


# --- purge_stale_document_audit (Plan 6 Task 16) ----------------------------


def test_audit_purge_honours_the_same_window_as_the_hallucination_log() -> None:
    """Asserted against the shared constant so the two windows cannot drift."""
    session = _session()
    session.add(
        DocumentAccessLog(
            document_id="doc-1",
            actor_kind="anon",
            actor_ref="anon-1",
            action="create",
            created_at=datetime.now(UTC)
            - timedelta(days=DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS + 1),
        )
    )
    session.commit()

    removed = purge_stale_document_audit(session)
    session.commit()

    assert removed == 1
    assert session.query(DocumentAccessLog).count() == 0


def test_audit_purge_keeps_rows_within_the_window() -> None:
    session = _session()
    session.add(
        DocumentAccessLog(
            document_id="doc-1",
            actor_kind="anon",
            actor_ref="anon-1",
            action="create",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    session.commit()

    removed = purge_stale_document_audit(session)
    session.commit()

    assert removed == 0
    assert session.query(DocumentAccessLog).count() == 1
