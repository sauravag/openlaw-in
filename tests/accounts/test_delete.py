"""Tests for `openlaw.accounts.delete` against the live TEST Postgres.

Unique ids per test, own-row cleanup; the caller owns the commit, so tests
commit explicitly where production would.
"""

from __future__ import annotations

import base64
import uuid
from datetime import date
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openlaw.accounts import delete as delete_module
from openlaw.accounts.delete import DELETE_ACTION, DeletionResult, delete_account
from openlaw.config import Settings
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    DocumentExcerptRow,
    Message,
    QuotaUsage,
    User,
)
from openlaw.documents.store import store_document

pytestmark = pytest.mark.integration

_KEY = base64.b64encode(b"1" * 32).decode()
_TEXT = b"The landlord must refund the security deposit within thirty days."


def _settings() -> Settings:
    return Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        document_encryption_key=_KEY,
        auth_enabled=True,
    )


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def session(live_engine: Engine) -> Iterator[Session]:
    assert live_engine.url.database == "openlaw_test"
    Base.metadata.create_all(live_engine, checkfirst=True)
    session = Session(live_engine)
    session.info["users"] = []
    session.info["conversations"] = []
    yield session
    session.rollback()
    conv_ids = session.info["conversations"]
    doc_ids = [
        d.id
        for d in session.scalars(
            select(Document).where(Document.conversation_id.in_(conv_ids))
        )
    ]
    session.query(DocumentAccessLog).filter(
        DocumentAccessLog.document_id.in_(doc_ids)
    ).delete(synchronize_session=False)
    session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(
        synchronize_session=False
    )
    session.query(User).filter(User.id.in_(session.info["users"])).delete(
        synchronize_session=False
    )
    session.commit()
    session.close()


def _user(session: Session) -> str:
    user = User(email=f"{_uid()}@example.com")
    session.add(user)
    session.flush()
    session.info["users"].append(user.id)
    return user.id


def _material(
    session: Session, owner: Owner, documents: int = 1, turns: int = 1
) -> tuple[Conversation, list[Document]]:
    settings = _settings()
    conversation = create_conversation(session, owner, settings)
    session.info["conversations"].append(conversation.id)
    for index in range(turns):
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    turn_index=index,
                    role=role,
                    content="x",
                )
                for role in ("user", "assistant")
            ]
        )
    docs = [
        store_document(
            session, owner, conversation.id, f"n{i}.txt", "text/plain", _TEXT, settings
        )
        for i in range(documents)
    ]
    session.commit()
    return conversation, docs


def _count(session: Session, model: type, column, value) -> int:
    return session.scalar(select(func.count()).select_from(model).where(column == value))


def test_deletes_conversations_messages_documents_and_excerpts(session: Session) -> None:
    user_id = _user(session)
    owner = Owner(user_id=user_id, anon_token=None)
    conversation, docs = _material(session, owner, documents=2, turns=2)
    doc_ids = [d.id for d in docs]
    conversation_id = conversation.id
    assert session.scalar(
        select(func.count()).select_from(DocumentExcerptRow).where(
            DocumentExcerptRow.document_id.in_(doc_ids)
        )
    )

    result = delete_account(session, user_id)
    session.commit()

    assert (result.conversations_deleted, result.documents_deleted) == (1, 2)
    assert result.messages_deleted == 4
    assert _count(session, Conversation, Conversation.user_id, user_id) == 0
    assert _count(session, Document, Document.user_id, user_id) == 0
    assert _count(session, Message, Message.conversation_id, conversation_id) == 0
    assert (
        session.scalar(
            select(func.count()).select_from(DocumentExcerptRow).where(
                DocumentExcerptRow.document_id.in_(doc_ids)
            )
        )
        == 0
    )
    assert session.get(User, user_id) is None


def test_deletes_quota_rows_with_the_user(session: Session) -> None:
    user_id = _user(session)
    session.add(QuotaUsage(user_id=user_id, period_month=date(2026, 1, 1), questions_used=2))
    session.commit()

    delete_account(session, user_id)
    session.commit()

    assert _count(session, QuotaUsage, QuotaUsage.user_id, user_id) == 0


def test_leaves_another_users_and_anonymous_rows_untouched(session: Session) -> None:
    mine, other = _user(session), _user(session)
    _material(session, Owner(user_id=mine, anon_token=None))
    other_conv, other_docs = _material(session, Owner(user_id=other, anon_token=None))
    other_doc_id = other_docs[0].id
    session.add(QuotaUsage(user_id=other, period_month=date(2026, 1, 1), questions_used=3))
    session.commit()
    anon_conv, anon_docs = _material(session, Owner(user_id=None, anon_token=_uid()))

    delete_account(session, mine)
    session.commit()
    session.expire_all()

    for conv, docs in ((other_conv, other_docs), (anon_conv, anon_docs)):
        assert session.get(Conversation, conv.id) is not None
        assert session.get(Document, docs[0].id) is not None
        assert _count(session, Message, Message.conversation_id, conv.id) == 2
    assert session.get(User, other) is not None
    assert _count(session, QuotaUsage, QuotaUsage.user_id, other) == 1
    assert _count(session, DocumentExcerptRow, DocumentExcerptRow.document_id, other_doc_id) > 0


def test_keeps_the_access_log_and_reports_the_count(session: Session) -> None:
    user_id = _user(session)
    _, docs = _material(session, Owner(user_id=user_id, anon_token=None), documents=2)
    before = session.scalar(
        select(func.count()).select_from(DocumentAccessLog).where(
            DocumentAccessLog.document_id.in_([d.id for d in docs])
        )
    )

    result = delete_account(session, user_id)
    session.commit()

    after = session.scalar(
        select(func.count()).select_from(DocumentAccessLog).where(
            DocumentAccessLog.document_id.in_([d.id for d in docs])
        )
    )
    assert after == before + 2  # one new row per erased document
    assert result.audit_rows_retained == after


def test_writes_one_delete_audit_row_per_document_naming_the_account(
    session: Session,
) -> None:
    user_id = _user(session)
    _, docs = _material(session, Owner(user_id=user_id, anon_token=None), documents=2)
    doc_ids = sorted(d.id for d in docs)

    delete_account(session, user_id)
    session.commit()

    rows = list(
        session.scalars(
            select(DocumentAccessLog).where(
                DocumentAccessLog.document_id.in_(doc_ids),
                DocumentAccessLog.action == DELETE_ACTION,
            )
        )
    )
    assert DELETE_ACTION == "delete"
    assert sorted(r.document_id for r in rows) == doc_ids
    assert {(r.actor_kind, r.actor_ref) for r in rows} == {("user", user_id)}


def test_rerun_on_an_already_deleted_id_returns_zeros(session: Session) -> None:
    user_id = _user(session)
    _material(session, Owner(user_id=user_id, anon_token=None))
    delete_account(session, user_id)
    session.commit()

    again = delete_account(session, user_id)

    assert again == DeletionResult(0, 0, 0, 0)


def test_unknown_user_returns_zeros(session: Session) -> None:
    assert delete_account(session, _uid()) == DeletionResult(0, 0, 0, 0)


def test_empty_user_id_raises_before_any_sql(session: Session) -> None:
    with patch.object(session, "execute") as execute:
        with pytest.raises(ValueError):
            delete_account(session, "")
    execute.assert_not_called()


def test_a_failure_part_way_rolls_back_whole_and_leaves_nothing_ownerless(
    session: Session,
) -> None:
    user_id = _user(session)
    conversation, docs = _material(session, Owner(user_id=user_id, anon_token=None))
    log_before = session.scalar(
        select(func.count()).select_from(DocumentAccessLog).where(
            DocumentAccessLog.document_id == docs[0].id
        )
    )

    with patch.object(
        delete_module, "_delete_messages", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError):
            delete_account(session, user_id)
    session.rollback()
    session.expire_all()

    assert session.get(User, user_id) is not None
    assert session.get(Conversation, conversation.id).user_id == user_id
    assert session.get(Document, docs[0].id).user_id == user_id
    assert _count(session, Message, Message.conversation_id, conversation.id) == 2
    # The audit row staged before the failure rolled back with it.
    assert (
        session.scalar(
            select(func.count()).select_from(DocumentAccessLog).where(
                DocumentAccessLog.document_id == docs[0].id
            )
        )
        == log_before
    )



def test_locks_the_users_row_so_a_concurrent_insert_waits(
    session: Session, live_engine: Engine
) -> None:
    from sqlalchemy.exc import OperationalError

    user_id = _user(session)
    session.commit()

    delete_account(session, user_id)  # uncommitted: the lock is held

    with Session(live_engine) as other:
        with pytest.raises(OperationalError):
            other.execute(
                select(User.id).where(User.id == user_id).with_for_update(nowait=True)
            )
    session.rollback()
