"""Database tables.

Deliberately minimal on personal data: a user is an email and an id. We never
store names, phone numbers, case numbers or Aadhaar — a legal-aid tool holding
that data becomes a target, and asking for it deters the people who most need to
use it.

Quota is tracked per user per calendar month, with `period_month` normalised to
the first day of the month so a period is a single comparable value.

**`Conversation` and `Message` stopped being placeholder tables in Plan 6
Task 2 (conversations-and-artifacts, this migration).** Their schema is now
the real one -- ownership (`user_id`/`anon_token`, with a check constraint
that forbids an ownerless row), retention (`expires_at`), and the validated
claim/language columns multi-turn, document-aware answering needs. **No
shipped code writes to them YET** -- `grep -rn` across `src/` and `scripts/`
still turns up nothing but this docstring and the table definitions
themselves -- the first writer lands in Task 14 ("Persistence, scoped to the
owning identity"). `Document`, `DocumentExcerptRow`, and
`DocumentAccessLog` (new in this migration) are the same story: schema
finalised now, writer arriving in a later Plan 6 task. None of this is
accidental debt; it is the schema Task 14 and friends are written against.

**`HallucinationLog` remains reserved for future use and is NOT written by
any shipped code path today** (final whole-plan review, M3 -- `grep -rn`
across `src/` and `scripts/` turns up only this docstring; `tests/db/` is the
only thing that constructs it, to prove the schema itself is sound), and
stays that way until a maintainer sets up the restricted database role its
own class docstring requires. It stays in the migration deliberately rather
than being dropped -- this is intentional forward schema design, not
accidental debt -- but see README's "Reserved, currently-unused tables"
section for why that distinction needs to be visible to an operator reading
the schema, not only to someone reading this comment.
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
    LargeBinary,
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
    """A thread of questions and answers.

    Owned by exactly one identity, never neither: `ck_conversations_has_owner`
    forbids a row with both `user_id` and `anon_token` NULL. Ownership binds
    to the same subject `openlaw.api.auth` already trusts -- `user_id` is a
    `users.id` (itself the Supabase token subject, never the email, per
    `auth.upsert_user`'s docstring) -- and `anon_token` is the opaque
    per-client cookie token (`openlaw.api.app`'s `ANON_COOKIE_NAME`) for a
    conversation started without signing in. Exactly one of the two is set
    for any given row; a signed-in conversation never carries an
    `anon_token`, and vice versa.

    `expires_at` is what lets `scripts/purge_retention.py` (Task 16) find
    expired anonymous conversations without a full scan -- indexed for
    exactly that query. An authenticated conversation leaves it NULL: per
    the plan, authenticated history persists until the user deletes it,
    while an anonymous conversation is session-scoped with a bounded TTL
    (`Settings.anonymous_conversation_ttl_hours`).
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR anon_token IS NOT NULL",
            name="ck_conversations_has_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    anon_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """One turn in a conversation, with its verified source cards.

    `content` holds only the user's verbatim question on `role="user"` rows
    and the rendered claim text on assistant rows -- never a mix of the two,
    and never the raw claim/citation structure, which lives in `claims` and
    `document_claims` instead. `claims` carries validated corpus citations
    (`[{"text": "...", "chunk_ids": ["IND_central_2385_s12_p0"]}]`);
    `document_claims` carries the same shape for excerpts quoted from a
    user-uploaded document (`openlaw.documents.DOCUMENT_EXCERPT_PREFIX`
    ids). Kept as two separate JSON columns, not one tagged list, because a
    later citation validator (Task 7) treats the two namespaces as disjoint
    and must never let one contaminate the other.
    """

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
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    document_claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Document(Base):
    """A user-uploaded document artifact, encrypted at rest.

    Owned the same way `Conversation` is (see its docstring): exactly one of
    `user_id`/`anon_token` is set, enforced by `ck_documents_has_owner`.
    `ciphertext`/`nonce` are AES-256-GCM output (`openlaw.documents.crypto`,
    Task 3) bound by AAD to this row's id -- `test_documents_never_store
    _plaintext` is the standing guarantee that no `Text`/`String` column
    here ever holds extracted document content instead.

    `expires_at` mirrors `Conversation.expires_at`: NULL for an
    authenticated upload (persists until the user deletes it), set to a
    bounded TTL for an anonymous one (`Settings.anonymous_upload_ttl_hours`),
    and indexed so `scripts/purge_retention.py` can find expired rows
    without a full scan.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR anon_token IS NOT NULL",
            name="ck_documents_has_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    anon_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crisis_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="documents")
    excerpts: Mapped[list["DocumentExcerptRow"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentExcerptRow(Base):
    """One encrypted excerpt of a `Document`, addressable by its `doc:` id.

    `excerpt_id` is the `openlaw.documents.DOCUMENT_EXCERPT_PREFIX`-prefixed
    id a `document_claims` entry (see `Message`) cites back to; the unique
    constraint on `(document_id, excerpt_id)` is what makes that id
    unambiguous within its own document. `ciphertext`/`nonce` are, like
    `Document`'s, AES-256-GCM output bound by AAD to this row's `excerpt_id`
    -- never the extracted text itself.
    """

    __tablename__ = "document_excerpts"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "excerpt_id", name="uq_document_excerpts_document_excerpt"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    excerpt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="excerpts")


class DocumentAccessLog(Base):
    """Append-only audit trail of who accessed which document, and how.

    `document_id` is deliberately NOT a foreign key: an audit record must
    outlive the document it describes (a user can delete a document; the
    fact that someone read or was denied access to it should not vanish
    with it), so there is nothing here for a `Document` deletion to cascade
    into. `actor_ref` is the same identity `Conversation.user_id`/
    `anon_token` carries -- the token subject for a signed-in actor, the
    anonymous cookie token otherwise -- named by `actor_kind`.
    """

    __tablename__ = "document_access_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


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
