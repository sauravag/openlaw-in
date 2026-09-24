"""Tests for the fix-round-1 hardening: a non-negative quota counter, a
constrained `Message.role`, and indexes on the foreign-key columns that back
the two hottest read paths (conversation history, quota lookups).
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openlaw.db.models import Base, Conversation, Message, QuotaUsage, User


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_quota_usage_rejects_a_negative_counter() -> None:
    """A negative counter is a quota that can be bypassed."""
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()

    session.add(
        QuotaUsage(user_id=user.id, period_month=date(2026, 9, 1), questions_used=-1)
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_message_rejects_an_unrecognised_role() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(
        Message(
            conversation_id=conversation.id, role="system", content="q", sources=[]
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_message_accepts_user_and_assistant_roles() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(
        Message(conversation_id=conversation.id, role="user", content="q", sources=[])
    )
    session.add(
        Message(
            conversation_id=conversation.id, role="assistant", content="a", sources=[]
        )
    )
    session.commit()

    assert session.query(Message).count() == 2


def test_foreign_key_columns_are_indexed() -> None:
    """Postgres does not auto-index foreign keys; these three back the
    hottest read paths (conversation history, quota lookups) and must not
    full-scan as data grows."""
    indexed_columns = {
        (table.name, column)
        for table in Base.metadata.tables.values()
        for index in table.indexes
        for column in (col.name for col in index.columns)
    }
    assert ("quota_usage", "user_id") in indexed_columns
    assert ("conversations", "user_id") in indexed_columns
    assert ("messages", "conversation_id") in indexed_columns
