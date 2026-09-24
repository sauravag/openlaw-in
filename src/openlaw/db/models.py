"""Database tables.

Deliberately minimal on personal data: a user is an email and an id. We never
store names, phone numbers, case numbers or Aadhaar — a legal-aid tool holding
that data becomes a target, and asking for it deters the people who most need to
use it.

Quota is tracked per user per calendar month, with `period_month` normalised to
the first day of the month so a period is a single comparable value.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _new_id() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for every table."""


class User(Base):
    """A signed-up person. Email is the only identifier stored."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QuotaUsage(Base):
    """Questions used by one user in one calendar month."""

    __tablename__ = "quota_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "period_month"),
        # A negative counter is a quota that can be bypassed; costs nothing
        # to forbid at the database layer. Incrementing is the caller's
        # concern (an atomic SQL increment, not read-modify-write) -- this
        # constraint only rules out the counter ever going negative.
        CheckConstraint(
            "questions_used >= 0", name="ck_quota_usage_questions_used_nonnegative"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_month: Mapped[date] = mapped_column(Date, nullable=False)
    questions_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Conversation(Base):
    """A thread of questions and answers."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """One turn in a conversation, with its verified source cards."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class HallucinationLog(Base):
    """Citations the validator refused to render — a safety metric and eval data.

    This is a second copy of the same class of verbatim, potentially sensitive
    text (a rejected claim's question and quoted text) that
    `openlaw.answers.escalation`'s JSONL hallucination log already holds. That
    sibling earns the right to keep the text verbatim by pairing it with two
    real controls: owner-only (`0600`) file permissions and a 90-day
    retention window (`DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS`,
    `purge_stale_events`). This table must carry the same weight, not a
    lesser version of it, because a database's blast radius (any query, any
    backup, any read replica) is wider than a single file's:

    - Retention: `openlaw.db.retention.purge_stale_hallucination_log` deletes
      rows older than `DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS` (default 90,
      matching the JSONL log) and is intended to run periodically, the same
      way `purge_stale_events` does for its file.
    - Access: **nothing may write to this table in production until it is
      reachable only through a restricted database role** (not the general
      application credential) analogous to the JSONL log's `0600`
      permissions. A maintainer wiring a writer to this table for the first
      time must set that role up first — this is a hard precondition, not a
      follow-up.
    """

    __tablename__ = "hallucination_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    dropped_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
