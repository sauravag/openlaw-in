from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.db.models import (
    Base,
    Conversation,
    HallucinationLog,
    Message,
    QuotaUsage,
    User,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_user_stores_only_an_email_identifier() -> None:
    """No names, no phone numbers — we hold nothing we do not need."""
    columns = {column.name for column in User.__table__.columns}
    assert columns == {"id", "email", "created_at"}


def test_user_email_is_unique() -> None:
    assert User.__table__.columns["email"].unique is True


def test_quota_usage_is_keyed_by_user_and_month() -> None:
    columns = {column.name for column in QuotaUsage.__table__.columns}
    assert {"user_id", "period_month", "questions_used"} <= columns


def test_message_stores_sources_as_structured_json() -> None:
    """Source cards are data, not rendered text."""
    assert "sources" in {column.name for column in Message.__table__.columns}


def test_hallucination_log_records_dropped_and_retrieved_chunk_ids() -> None:
    columns = {column.name for column in HallucinationLog.__table__.columns}
    assert {"question", "claim_text", "dropped_chunk_ids", "retrieved_chunk_ids"} <= (
        columns
    )


def test_persists_a_user_and_their_quota_row() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()

    session.add(
        QuotaUsage(user_id=user.id, period_month=date(2026, 9, 1), questions_used=1)
    )
    session.commit()

    stored = session.query(QuotaUsage).one()
    assert stored.questions_used == 1
    assert stored.period_month == date(2026, 9, 1)


def test_persists_a_conversation_with_messages() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()

    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content="The deposit must be refunded.",
            sources=[{"chunk_id": "a", "source_url": "https://x.gov.in/a"}],
        )
    )
    session.commit()

    assert session.query(Message).one().sources[0]["chunk_id"] == "a"


def test_deleting_a_conversation_deletes_its_messages() -> None:
    """Users can delete their history; nothing may be orphaned."""
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
    session.commit()

    session.delete(conversation)
    session.commit()

    assert session.query(Message).count() == 0
