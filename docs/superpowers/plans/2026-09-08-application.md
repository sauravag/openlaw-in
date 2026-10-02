# OpenLaw.in Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person signs up with email or Google, asks a legal question in their own language, and gets a verified, cited answer — free, within a monthly quota, on a stack anyone can self-host.

**Architecture:** A FastAPI service composing the pipelines from Plans 1–4, with Postgres for users, quota, conversations and the hallucination log. Auth is Supabase (email and Google OAuth), so no password ever reaches this service. A Next.js front end renders answers as plain-language text with source cards linking to government PDFs. Everything ships in one Docker Compose file, because a legal-aid body with one part-time technical volunteer has to be able to run it.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2 + Alembic, Postgres 17, Supabase auth, Next.js, Docker Compose, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-09-08-openlaw-in-design.md` (§9, §10, §12)

**Depends on:** all four prior plans — corpus-foundation (Qdrant), knowledge-graph (`expand_chunks`), retrieval-and-answers (`search`, `ModelRegistry`, `AnthropicAdapter`), multilingual (`answer_in_language`)

## Global Constraints

- **Anonymous users get 3 questions before signup; signed-in users get 30 per month.** Self-hosted deployments disable quota with one flag.
- **Metering ships in v1.** Cheap to build now, painful to retrofit.
- **Never store or solicit identifying details** — no names, case numbers, or Aadhaar. Conversations are user-deletable.
- **Every answer response carries** its sources, the corpus version, the escalation footer, and — when detected — the crisis response.
- **Crisis responses render above the legal content, never below it.** A helpline printed under a statutory explanation has already failed the person reading it.
- **A language selector is required in the interface.** Marathi shares Devanagari with Hindi and Assamese shares its script with Bengali, so detection alone misclassifies them (multilingual plan, Task 4 self-review).
- **One `docker compose up` starts the whole stack.** A tested success criterion, not an aspiration.
- **Python style:** type hints everywhere, `pathlib.Path`, f-strings, functions under 50 lines, files under 800 lines.
- **TDD mandatory**, minimum 80% coverage. API tests use FastAPI's `TestClient` with stubbed pipelines — no live model or vector calls.

---

## File Structure

```
src/openlaw/db/
├── models.py        # SQLAlchemy tables
├── session.py       # engine and session lifecycle
└── quota.py         # metering (pure policy + persistence)

src/openlaw/api/
├── auth.py          # Supabase JWT verification
├── schemas.py       # request/response models
├── routes.py        # endpoints
├── app.py           # application factory
└── deps.py          # production wiring

migrations/          # Alembic
web/                 # Next.js front end
tests/db/, tests/api/
```

---

## Task 1: Database schema

**Files:**
- Create: `src/openlaw/db/__init__.py`, `src/openlaw/db/models.py`, `src/openlaw/db/session.py`
- Create: `tests/db/__init__.py`, `tests/db/test_models.py`
- Modify: `pyproject.toml`, `src/openlaw/config.py`, `.env.example`

**Interfaces:**
- Consumes: `openlaw.config.Settings`
- Produces:
  - `Settings.database_url`, `quota_enabled`, `monthly_quota`, `anonymous_quota`, `supabase_url`, `supabase_jwt_secret`
  - `Base` — declarative base
  - `User`, `QuotaUsage`, `Conversation`, `Message`, `HallucinationLog`
  - `engine_from_settings(settings) -> Engine`
  - `session_scope(engine) -> Iterator[Session]` — context manager

- [ ] **Step 1: Add dependencies and settings**

Add to `pyproject.toml` dependencies: `"fastapi>=0.115"`, `"uvicorn[standard]>=0.32"`, `"sqlalchemy>=2.0"`, `"alembic>=1.14"`, `"psycopg[binary]>=3.2"`, `"pyjwt[crypto]>=2.9"`.

In `src/openlaw/config.py`, add to `Settings`:

```python
    database_url: str = "postgresql+psycopg://openlaw:openlaw@localhost:5432/openlaw"
    quota_enabled: bool = True
    monthly_quota: int = 30
    anonymous_quota: int = 3
    supabase_url: str | None = None
    supabase_jwt_secret: str | None = None
```

Append to `.env.example`:

```bash
OPENLAW_DATABASE_URL=postgresql+psycopg://openlaw:openlaw@localhost:5432/openlaw

# Quota. Self-hosters set OPENLAW_QUOTA_ENABLED=false for unlimited use.
OPENLAW_QUOTA_ENABLED=true
OPENLAW_MONTHLY_QUOTA=30
OPENLAW_ANONYMOUS_QUOTA=3

# Supabase auth (email + Google)
OPENLAW_SUPABASE_URL=
OPENLAW_SUPABASE_JWT_SECRET=
```

- [ ] **Step 2: Write the failing test**

Create `tests/db/test_models.py`:

```python
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
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/db/test_models.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.db'`

- [ ] **Step 4: Implement `src/openlaw/db/models.py`**

```python
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
    __table_args__ = (UniqueConstraint("user_id", "period_month"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_month: Mapped[date] = mapped_column(Date, nullable=False)
    questions_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Conversation(Base):
    """A thread of questions and answers."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """One turn in a conversation, with its verified source cards."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class HallucinationLog(Base):
    """Citations the validator refused to render — a safety metric and eval data."""

    __tablename__ = "hallucination_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    dropped_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 5: Implement `src/openlaw/db/session.py`**

```python
"""Database engine and session lifecycle."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from openlaw.config import Settings


def engine_from_settings(settings: Settings) -> Engine:
    """Return a SQLAlchemy engine built from `settings`."""
    return create_engine(settings.database_url, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on failure."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

Also create empty `src/openlaw/db/__init__.py` and `tests/db/__init__.py`.

- [ ] **Step 6: Run it to verify it passes**

Run: `pip install -e ".[dev]" && pytest tests/db/test_models.py -v --no-cov`
Expected: PASS (8 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example src/openlaw/config.py src/openlaw/db tests/db
git commit -m "feat: database schema for users, quota, conversations and hallucinations"
```

---

## Task 2: Quota metering

**Files:**
- Create: `src/openlaw/db/quota.py`
- Create: `tests/db/test_quota.py`

**Interfaces:**
- Consumes: `QuotaUsage` (Task 1)
- Produces:
  - `current_period(today: date | None = None) -> date`
  - `QuotaDecision` — frozen dataclass: `allowed: bool`, `used: int`, `limit: int`; property `remaining: int`
  - `check_quota(used: int, limit: int, enabled: bool) -> QuotaDecision` — pure
  - `consume_quota(session, user_id, limit, enabled, today=None) -> QuotaDecision`

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_quota.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.db.models import Base, QuotaUsage, User
from openlaw.db.quota import check_quota, consume_quota, current_period


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _user(session: Session) -> User:
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    return user


def test_current_period_is_the_first_of_the_month() -> None:
    assert current_period(date(2026, 9, 8)) == date(2026, 9, 1)


def test_allows_a_user_below_the_limit() -> None:
    decision = check_quota(used=5, limit=30, enabled=True)
    assert decision.allowed is True
    assert decision.remaining == 25


def test_blocks_a_user_at_the_limit() -> None:
    decision = check_quota(used=30, limit=30, enabled=True)
    assert decision.allowed is False
    assert decision.remaining == 0


def test_blocks_a_user_over_the_limit() -> None:
    assert check_quota(used=31, limit=30, enabled=True).allowed is False


def test_remaining_never_goes_negative() -> None:
    assert check_quota(used=45, limit=30, enabled=True).remaining == 0


def test_disabled_quota_always_allows() -> None:
    """Self-hosted legal-aid deployments run without a cap."""
    assert check_quota(used=10_000, limit=30, enabled=False).allowed is True


def test_first_question_creates_a_usage_row() -> None:
    session = _session()
    user = _user(session)

    decision = consume_quota(
        session, user.id, limit=30, enabled=True, today=date(2026, 9, 8)
    )
    assert decision.allowed is True
    assert decision.used == 1


def test_repeated_questions_increment_the_same_row() -> None:
    session = _session()
    user = _user(session)

    for _ in range(3):
        decision = consume_quota(
            session, user.id, limit=30, enabled=True, today=date(2026, 9, 8)
        )
    assert decision.used == 3


def test_a_new_month_starts_a_fresh_allowance() -> None:
    session = _session()
    user = _user(session)

    consume_quota(session, user.id, limit=30, enabled=True, today=date(2026, 9, 8))
    october = consume_quota(
        session, user.id, limit=30, enabled=True, today=date(2026, 10, 1)
    )
    assert october.used == 1


def test_consuming_beyond_the_limit_is_refused_without_incrementing() -> None:
    session = _session()
    user = _user(session)

    for _ in range(2):
        consume_quota(session, user.id, limit=2, enabled=True, today=date(2026, 9, 8))
    blocked = consume_quota(
        session, user.id, limit=2, enabled=True, today=date(2026, 9, 8)
    )

    assert blocked.allowed is False
    assert blocked.used == 2


def test_disabled_quota_does_not_write_usage_rows() -> None:
    session = _session()
    user = _user(session)

    consume_quota(session, user.id, limit=30, enabled=False, today=date(2026, 9, 8))
    assert session.query(QuotaUsage).count() == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/db/test_quota.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.db.quota'`

- [ ] **Step 3: Implement `src/openlaw/db/quota.py`**

```python
"""Monthly quota metering.

Thirty questions a month is deliberately generous enough that one real legal
problem can be worked through end to end, and bounded enough that costs stay
predictable. Self-hosted deployments — legal-aid authorities, NGOs, law-school
clinics — disable it entirely with one configuration flag.

`check_quota` is pure, so the policy is testable without a database.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from openlaw.db.models import QuotaUsage


@dataclass(frozen=True)
class QuotaDecision:
    """Whether a question may proceed, and what remains."""

    allowed: bool
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        """Questions left this period, never negative."""
        return max(self.limit - self.used, 0)


def current_period(today: date | None = None) -> date:
    """Return the first day of `today`'s month."""
    reference = today or date.today()
    return date(reference.year, reference.month, 1)


def check_quota(used: int, limit: int, enabled: bool) -> QuotaDecision:
    """Return the quota decision for a user who has used `used` questions."""
    if not enabled:
        return QuotaDecision(allowed=True, used=used, limit=limit)
    return QuotaDecision(allowed=used < limit, used=used, limit=limit)


def consume_quota(
    session: Session,
    user_id: str,
    limit: int,
    enabled: bool,
    today: date | None = None,
) -> QuotaDecision:
    """Consume one question from `user_id`'s allowance for the current month.

    Returns a refusal without incrementing when the allowance is exhausted, and
    writes nothing at all when quota is disabled.
    """
    if not enabled:
        return QuotaDecision(allowed=True, used=0, limit=limit)

    period = current_period(today)
    usage = (
        session.query(QuotaUsage)
        .filter(QuotaUsage.user_id == user_id, QuotaUsage.period_month == period)
        .one_or_none()
    )
    if usage is None:
        usage = QuotaUsage(user_id=user_id, period_month=period, questions_used=0)
        session.add(usage)
        session.flush()

    decision = check_quota(usage.questions_used, limit, enabled)
    if not decision.allowed:
        return decision

    usage.questions_used += 1
    session.flush()
    return QuotaDecision(allowed=True, used=usage.questions_used, limit=limit)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/db/test_quota.py -v --no-cov`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/db/quota.py tests/db/test_quota.py
git commit -m "feat: monthly quota metering with a self-host disable flag"
```

---

## Task 3: Authentication

**Files:**
- Create: `src/openlaw/api/__init__.py`, `src/openlaw/api/auth.py`
- Create: `tests/api/__init__.py`, `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `User` (Task 1)
- Produces:
  - `AuthError` — exception
  - `AuthenticatedUser` — frozen model: `user_id: str`, `email: str`
  - `extract_bearer(header: str | None) -> str | None`
  - `decode_token(token: str, secret: str) -> AuthenticatedUser`
  - `upsert_user(session, identity: AuthenticatedUser) -> User`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_auth.py`:

```python
import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.api.auth import (
    AuthenticatedUser,
    AuthError,
    decode_token,
    extract_bearer,
    upsert_user,
)
from openlaw.db.models import Base, User

SECRET = "test-secret"


def _token(payload: dict) -> str:
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_extracts_a_bearer_token() -> None:
    assert extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"


def test_bearer_extraction_is_case_insensitive() -> None:
    assert extract_bearer("bearer abc") == "abc"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer"])
def test_returns_none_for_a_non_bearer_header(header: str | None) -> None:
    assert extract_bearer(header) is None


def test_decodes_a_valid_supabase_token() -> None:
    identity = decode_token(
        _token({"sub": "user-123", "email": "person@example.com"}), SECRET
    )
    assert identity.user_id == "user-123"
    assert identity.email == "person@example.com"


def test_rejects_a_token_signed_with_the_wrong_secret() -> None:
    token = jwt.encode({"sub": "u", "email": "e@x.com"}, "other", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_rejects_a_malformed_token() -> None:
    with pytest.raises(AuthError):
        decode_token("not-a-token", SECRET)


def test_rejects_a_token_without_a_subject() -> None:
    with pytest.raises(AuthError):
        decode_token(_token({"email": "person@example.com"}), SECRET)


def test_rejects_a_token_without_an_email() -> None:
    with pytest.raises(AuthError):
        decode_token(_token({"sub": "user-123"}), SECRET)


def test_upsert_creates_a_user_on_first_sign_in() -> None:
    session = _session()
    user = upsert_user(
        session, AuthenticatedUser(user_id="user-123", email="person@example.com")
    )

    assert user.email == "person@example.com"
    assert session.query(User).count() == 1


def test_upsert_is_idempotent_across_sign_ins() -> None:
    session = _session()
    identity = AuthenticatedUser(user_id="user-123", email="person@example.com")

    first = upsert_user(session, identity)
    second = upsert_user(session, identity)

    assert first.id == second.id
    assert session.query(User).count() == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/api/test_auth.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.api'`

- [ ] **Step 3: Implement `src/openlaw/api/auth.py`**

```python
"""Supabase JWT verification.

Supabase handles email and Google OAuth, so no password ever reaches this
service. We verify the token it issues and record only the subject and email.
An unverifiable token is treated as anonymous, never as trusted.
"""

import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from openlaw.db.models import User

_BEARER_PREFIX = "bearer "


class AuthError(Exception):
    """Raised when a token is missing, malformed, or fails verification."""


class AuthenticatedUser(BaseModel):
    """The identity carried by a verified token."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    email: str


def extract_bearer(header: str | None) -> str | None:
    """Return the token from an `Authorization` header, or None."""
    if not header or not header.lower().startswith(_BEARER_PREFIX):
        return None
    return header[len(_BEARER_PREFIX) :].strip() or None


def decode_token(token: str, secret: str) -> AuthenticatedUser:
    """Verify `token` and return the identity it carries.

    Raises AuthError on any failure.
    """
    try:
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], options={"verify_aud": False}
        )
    except jwt.PyJWTError as error:
        raise AuthError(f"token verification failed: {error}") from error

    subject = payload.get("sub")
    email = payload.get("email")
    if not subject or not email:
        raise AuthError("token is missing a subject or email claim")

    return AuthenticatedUser(user_id=str(subject), email=str(email))


def upsert_user(session: Session, identity: AuthenticatedUser) -> User:
    """Return the stored user for `identity`, creating it on first sign-in."""
    user = session.query(User).filter(User.email == identity.email).one_or_none()
    if user is not None:
        return user

    user = User(id=identity.user_id, email=identity.email)
    session.add(user)
    session.flush()
    return user
```

Also create empty `src/openlaw/api/__init__.py` and `tests/api/__init__.py`.

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/api/test_auth.py -v --no-cov`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/api tests/api
git commit -m "feat: Supabase JWT verification and user upsert"
```

---

## Task 4: API schemas, routes, and the application factory

**Files:**
- Create: `src/openlaw/api/schemas.py`, `src/openlaw/api/routes.py`, `src/openlaw/api/app.py`
- Create: `tests/api/test_routes.py`

**Interfaces:**
- Consumes: `LocalisedAnswer` (multilingual Task 4), `crisis_response` (retrieval-and-answers Task 7), `check_quota`/`consume_quota` (Task 2), auth (Task 3)
- Produces:
  - `AskRequest` — `question: str` (1–2000), `language: str | None`, `state: str | None`
  - `SourceCard` — `chunk_id`, `source_url`, `title`
  - `AskResponse` — `answer`, `sources`, `language`, `corpus_version`, `footer`, `crisis_response`, `is_answerable`, `quota_remaining`
  - `to_response(localised, corpus_version, quota_remaining) -> AskResponse`
  - `create_app(answer_fn, session_factory, settings) -> FastAPI`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_routes.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.answers.escalation import CrisisKind
from openlaw.answers.schema import Claim
from openlaw.answers.validate import Source, ValidatedAnswer
from openlaw.api.app import create_app
from openlaw.config import Settings
from openlaw.db.models import Base
from openlaw.language.pipeline import LocalisedAnswer


def _settings(**overrides) -> Settings:
    defaults = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "database_url": "sqlite://",
        "quota_enabled": True,
        "anonymous_quota": 3,
        "supabase_jwt_secret": "test-secret",
    }
    return Settings(**{**defaults, **overrides})


def _localised(crisis: CrisisKind | None = None) -> LocalisedAnswer:
    return LocalisedAnswer(
        answer=ValidatedAnswer(
            summary="The deposit must be refunded.",
            claims=(Claim(text="A refund is required.", chunk_ids=("a",)),),
            sources=(
                Source(
                    chunk_id="a",
                    source_url="https://www.indiacode.nic.in/handle/123456789/1",
                    title="Model Tenancy Act",
                ),
            ),
            is_answerable=True,
        ),
        language="en",
        english_query="my landlord kept my deposit",
        crisis=crisis,
        footer="This is legal information, not legal advice. NALSA: 15100",
    )


def _client(localised: LocalisedAnswer | None = None, settings=None) -> TestClient:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    def session_factory():
        return Session(engine)

    def answer_fn(question: str, language: str | None, state: str | None):
        return localised or _localised()

    return TestClient(create_app(answer_fn, session_factory, settings or _settings()))


def test_health_endpoint_reports_ok() -> None:
    assert _client().get("/health").json()["status"] == "ok"


def test_ask_returns_the_answer_summary() -> None:
    response = _client().post("/ask", json={"question": "my landlord kept my deposit"})
    assert response.status_code == 200
    assert response.json()["answer"] == "The deposit must be refunded."


def test_ask_returns_source_cards_with_government_urls() -> None:
    body = _client().post("/ask", json={"question": "q"}).json()
    assert body["sources"][0]["source_url"].startswith("https://")


def test_ask_returns_the_corpus_version() -> None:
    """Every answer is time-bound — the reader must see which snapshot it is."""
    assert _client().post("/ask", json={"question": "q"}).json()["corpus_version"] == (
        "v2026.08"
    )


def test_ask_always_returns_the_escalation_footer() -> None:
    assert "15100" in _client().post("/ask", json={"question": "q"}).json()["footer"]


def test_crisis_response_is_returned_as_its_own_field() -> None:
    """The interface renders it above the legal content, never below."""
    client = _client(_localised(crisis=CrisisKind.DOMESTIC_VIOLENCE))
    body = client.post("/ask", json={"question": "my husband beats me"}).json()

    assert body["crisis_response"] is not None
    assert "181" in body["crisis_response"] or "1091" in body["crisis_response"]


def test_no_crisis_response_for_an_ordinary_question() -> None:
    assert _client().post("/ask", json={"question": "q"}).json()["crisis_response"] is (
        None
    )


def test_anonymous_quota_is_reported() -> None:
    assert _client().post("/ask", json={"question": "q"}).json()["quota_remaining"] == 2


def test_anonymous_user_is_blocked_after_the_free_allowance() -> None:
    client = _client()
    for _ in range(3):
        client.post("/ask", json={"question": "q"})
    blocked = client.post("/ask", json={"question": "q"})

    assert blocked.status_code == 429
    assert blocked.json()["signup_required"] is True


def test_quota_disabled_never_blocks() -> None:
    """Self-hosted deployments run without a cap."""
    client = _client(settings=_settings(quota_enabled=False))
    for _ in range(10):
        response = client.post("/ask", json={"question": "q"})
    assert response.status_code == 200


def test_rejects_a_blank_question() -> None:
    assert _client().post("/ask", json={"question": "   "}).status_code == 422


def test_rejects_an_overlong_question() -> None:
    assert _client().post("/ask", json={"question": "x" * 2001}).status_code == 422
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/api/test_routes.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.api.app'`

- [ ] **Step 3: Implement `src/openlaw/api/schemas.py`**

```python
"""API request and response shapes.

`crisis_response` is a separate field rather than folded into the answer text, so
the interface can render it above the legal content. A helpline printed below a
statutory explanation has already failed the person reading it.
"""

from pydantic import BaseModel, ConfigDict, Field

from openlaw.answers.escalation import crisis_response as crisis_text
from openlaw.language.pipeline import LocalisedAnswer


class AskRequest(BaseModel):
    """A question from a user."""

    question: str = Field(min_length=1, max_length=2000)
    language: str | None = None
    state: str | None = None


class SourceCard(BaseModel):
    """One verified citation rendered to the reader."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_url: str
    title: str | None = None


class AskResponse(BaseModel):
    """A verified answer with everything needed to render it responsibly."""

    answer: str
    sources: list[SourceCard]
    language: str
    corpus_version: str
    footer: str
    crisis_response: str | None = None
    is_answerable: bool
    quota_remaining: int


class QuotaExceeded(BaseModel):
    """Returned with HTTP 429 when the allowance is spent."""

    detail: str
    quota_remaining: int = 0
    signup_required: bool = False


def to_response(
    localised: LocalisedAnswer, corpus_version: str, quota_remaining: int
) -> AskResponse:
    """Map a localised answer onto the API response."""
    return AskResponse(
        answer=localised.answer.summary,
        sources=[
            SourceCard(
                chunk_id=source.chunk_id,
                source_url=source.source_url,
                title=source.title,
            )
            for source in localised.answer.sources
        ],
        language=localised.language,
        corpus_version=corpus_version,
        footer=localised.footer,
        crisis_response=crisis_text(localised.crisis) if localised.crisis else None,
        is_answerable=localised.answer.is_answerable,
        quota_remaining=quota_remaining,
    )
```

- [ ] **Step 4: Implement `src/openlaw/api/routes.py`**

```python
"""HTTP endpoints.

Anonymous users get a small free allowance before signup so the first question
costs no friction — the people this exists for will not create an account to find
out whether it helps.
"""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from openlaw.api.auth import AuthError, decode_token, extract_bearer, upsert_user
from openlaw.api.schemas import AskRequest, AskResponse, to_response
from openlaw.db.quota import check_quota, consume_quota

router = APIRouter()


def _client_key(request: Request) -> str:
    """Return a coarse key for anonymous allowance accounting."""
    return request.client.host if request.client else "unknown"


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.post("/ask", response_model=AskResponse)
def ask(
    request: Request,
    body: AskRequest,
    authorization: str | None = Header(default=None),
) -> AskResponse:
    """Answer a legal question, metering it against the caller's allowance."""
    settings = request.app.state.settings
    answer_fn: Callable = request.app.state.answer_fn
    session_factory: Callable = request.app.state.session_factory

    if not body.question.strip():
        raise HTTPException(status_code=422, detail="question cannot be blank")

    token = extract_bearer(authorization)

    if token and settings.supabase_jwt_secret:
        with session_factory() as session:
            try:
                identity = decode_token(token, settings.supabase_jwt_secret)
            except AuthError as error:
                raise HTTPException(status_code=401, detail=str(error)) from error
            user = upsert_user(session, identity)
            decision = consume_quota(
                session,
                user.id,
                limit=settings.monthly_quota,
                enabled=settings.quota_enabled,
            )
            session.commit()
        signup_required = False
        remaining = decision.remaining
    else:
        key = _client_key(request)
        used = request.app.state.anonymous_used.get(key, 0)
        decision = check_quota(used, settings.anonymous_quota, settings.quota_enabled)
        if decision.allowed and settings.quota_enabled:
            request.app.state.anonymous_used[key] = used + 1
        signup_required = True
        remaining = max(settings.anonymous_quota - (used + 1), 0)

    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "detail": "Free allowance used for this period.",
                "quota_remaining": 0,
                "signup_required": signup_required,
            },
        )

    localised = answer_fn(body.question, body.language, body.state)
    return to_response(localised, settings.corpus_version, remaining)
```

- [ ] **Step 5: Implement `src/openlaw/api/app.py`**

```python
"""FastAPI application factory.

Dependencies are injected rather than imported, so tests substitute a stub answer
function and an in-memory database without touching a model or a vector store.
"""

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openlaw.api.routes import router
from openlaw.config import Settings


def create_app(
    answer_fn: Callable, session_factory: Callable, settings: Settings
) -> FastAPI:
    """Build the application with its dependencies wired in."""
    app = FastAPI(title="OpenLaw.in", version="0.1.0")
    app.state.answer_fn = answer_fn
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.anonymous_used = {}
    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc: StarletteHTTPException) -> JSONResponse:
        """Return structured quota errors rather than a bare string."""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app
```

- [ ] **Step 6: Run it to verify it passes**

Run: `pytest tests/api/test_routes.py -v --no-cov`
Expected: PASS (15 passed)

- [ ] **Step 7: Commit**

```bash
git add src/openlaw/api tests/api
git commit -m "feat: ask endpoint with quota metering and crisis-aware responses"
```

---

## Task 5: Production wiring and migrations

**Files:**
- Create: `src/openlaw/api/deps.py`
- Create: `alembic.ini`, `migrations/env.py`
- Create: `tests/api/test_deps.py`

**Interfaces:**
- Consumes: everything from Plans 1–4
- Produces:
  - `RETRIEVAL_LIMIT: int` (10), `EXPANSION_LIMIT: int` (20)
  - `QdrantGraphRetriever(embedder, search_fn, expand_fn)` — satisfies the multilingual `Retriever` Protocol
  - `build_answer_fn(settings) -> Callable[[str, str | None, str | None], LocalisedAnswer]`
  - `production_app() -> FastAPI`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_deps.py`:

```python
from openlaw.api.deps import QdrantGraphRetriever
from openlaw.retrieval.context import ChunkKind, RetrievedChunk


class _Embedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 1024


def _chunk(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://x.gov.in/{chunk_id}",
        score=score,
        kind=ChunkKind.PROVISION,
    )


def test_retriever_returns_a_context_for_the_query() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (),
    )
    context = retriever.retrieve("my landlord kept my deposit")

    assert context.query == "my landlord kept my deposit"
    assert context.chunk_ids == frozenset({"a"})


def test_retriever_merges_graph_expansion_into_the_context() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (_chunk("b", 0.4),),
    )
    assert retriever.retrieve("q").chunk_ids == frozenset({"a", "b"})


def test_graph_expansion_never_displaces_a_directly_retrieved_chunk() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (_chunk("a", 0.1),),
    )
    context = retriever.retrieve("q")

    assert len(context.chunks) == 1
    assert context.chunks[0].score == 0.9


def test_expansion_is_asked_only_about_retrieved_chunks() -> None:
    seen: list[list[str]] = []

    def expand(chunk_ids: list[str]) -> tuple[RetrievedChunk, ...]:
        seen.append(list(chunk_ids))
        return ()

    QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=expand,
    ).retrieve("q")

    assert seen == [["a"]]


def test_empty_search_yields_an_empty_context() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (),
        expand_fn=lambda chunk_ids: (),
    )
    assert retriever.retrieve("q").chunks == ()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/api/test_deps.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.api.deps'`

- [ ] **Step 3: Implement `src/openlaw/api/deps.py`**

```python
"""Production dependency wiring.

The only module that knows how every piece fits together. Search and graph
expansion are injected as callables so the composition is testable without a live
Qdrant or Neo4j.
"""

from collections.abc import Callable

import httpx
from fastapi import FastAPI

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.models import ModelRegistry
from openlaw.api.app import create_app
from openlaw.config import Settings, get_settings
from openlaw.db.session import engine_from_settings, session_scope
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks
from openlaw.language.glossary import load_glossary
from openlaw.language.pipeline import LocalisedAnswer, answer_in_language
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk
from openlaw.retrieval.query import VoyageQueryEmbedder
from openlaw.retrieval.rerank import ScoreReranker, merge_chunks
from openlaw.retrieval.search import search

RETRIEVAL_LIMIT = 10
EXPANSION_LIMIT = 20


class QdrantGraphRetriever:
    """Composes vector search with bounded graph expansion."""

    def __init__(
        self,
        embedder,
        search_fn: Callable[[list[float]], tuple[RetrievedChunk, ...]],
        expand_fn: Callable[[list[str]], tuple[RetrievedChunk, ...]],
    ) -> None:
        self._embedder = embedder
        self._search = search_fn
        self._expand = expand_fn
        self._reranker = ScoreReranker()

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        vector = self._embedder.embed_query(english_query)
        primary = self._search(vector)
        expanded = self._expand([chunk.chunk_id for chunk in primary])
        merged = merge_chunks(primary, expanded)
        return RetrievalContext(
            query=english_query, chunks=self._reranker.rerank(english_query, merged)
        )


def build_answer_fn(settings: Settings) -> Callable[..., LocalisedAnswer]:
    """Return the production answer function used by the ask endpoint."""
    if not settings.voyage_api_key or not settings.anthropic_api_key:
        raise RuntimeError("both the Voyage and Anthropic API keys are required")

    adapter = AnthropicAdapter(settings.anthropic_api_key)
    registry = ModelRegistry.from_settings(settings)
    glossary = load_glossary()
    embedder = VoyageQueryEmbedder(settings.voyage_api_key)
    client = httpx.Client(timeout=60)

    def expand(chunk_ids: list[str]) -> tuple[RetrievedChunk, ...]:
        """Expand retrieved chunks through the knowledge graph."""
        if not chunk_ids:
            return ()
        with graph_driver(settings) as driver:
            rows = expand_chunks(driver, chunk_ids, limit=EXPANSION_LIMIT)
        return tuple(
            RetrievedChunk(
                chunk_id=str(row["id"]),
                text=str(row.get("label") or "related provision"),
                source_url=str(row.get("source_url") or ""),
                score=0.0,
                kind=ChunkKind.PROVISION,
            )
            for row in rows
            if row.get("id") and row.get("source_url")
        )

    retriever = QdrantGraphRetriever(
        embedder=embedder,
        search_fn=lambda vector: search(
            client, settings.qdrant_url, vector, limit=RETRIEVAL_LIMIT
        ),
        expand_fn=expand,
    )

    def answer_fn(
        question: str, language: str | None, state: str | None
    ) -> LocalisedAnswer:
        """Answer `question` in the language it was asked in."""
        return answer_in_language(
            question, retriever, adapter, registry, glossary, state=state
        )

    return answer_fn


def production_app() -> FastAPI:
    """Build the production application."""
    settings = get_settings()
    engine = engine_from_settings(settings)

    def session_factory():
        return session_scope(engine)

    return create_app(build_answer_fn(settings), session_factory, settings)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/api/test_deps.py -v --no-cov`
Expected: PASS (5 passed)

- [ ] **Step 5: Initialise Alembic and create the first migration**

```bash
alembic init migrations
```

In `migrations/env.py`, set the metadata target and URL:

```python
from openlaw.config import get_settings
from openlaw.db.models import Base

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

Then generate and apply:

```bash
docker compose up -d postgres
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Expected: five tables created — `users`, `quota_usage`, `conversations`, `messages`, `hallucination_log`.

- [ ] **Step 6: Commit**

```bash
git add src/openlaw/api/deps.py tests/api/test_deps.py alembic.ini migrations/
git commit -m "feat: production wiring and initial database migration"
```

---

## Task 6: The chat interface

**Files:**
- Create: `web/` (Next.js app), `web/components/AnswerView.tsx`, `web/components/LanguageSelector.tsx`
- Create: `web/components/__tests__/AnswerView.test.tsx`

**Interfaces:**
- Consumes: the `/ask` endpoint (Task 4)
- Produces: `AnswerData` type mirroring `AskResponse`; `AnswerView`; `LanguageSelector`

- [ ] **Step 1: Scaffold the front end**

```bash
npx create-next-app@latest web --typescript --app --no-src-dir --eslint
cd web && npm install @supabase/supabase-js @supabase/auth-ui-react
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom
```

- [ ] **Step 2: Write the failing component test**

Create `web/components/__tests__/AnswerView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AnswerView from '../AnswerView'

const baseAnswer = {
  answer: 'The deposit must be refunded within one month.',
  sources: [
    {
      chunk_id: 'a',
      source_url: 'https://www.indiacode.nic.in/handle/123456789/1',
      title: 'Model Tenancy Act',
    },
  ],
  language: 'en',
  corpus_version: 'v2026.08',
  footer: 'This is legal information, not legal advice. NALSA: 15100',
  crisis_response: null,
  is_answerable: true,
  quota_remaining: 29,
}

describe('AnswerView', () => {
  it('shows the answer text', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/deposit must be refunded/i)).toBeInTheDocument()
  })

  it('renders every source as a link to the government document', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(
      screen.getByRole('link', { name: /Model Tenancy Act/i }),
    ).toHaveAttribute('href', 'https://www.indiacode.nic.in/handle/123456789/1')
  })

  it('shows the corpus version so the answer is visibly time-bound', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/v2026\.08/)).toBeInTheDocument()
  })

  it('always shows the legal-aid escalation footer', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/15100/)).toBeInTheDocument()
  })

  it('renders the crisis response above the answer', () => {
    const { container } = render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('Call 181')).toBeLessThan(text.indexOf('deposit must be'))
  })

  it('shows a clear message when no answer could be grounded', () => {
    render(<AnswerView data={{ ...baseAnswer, is_answerable: false, sources: [] }} />)
    expect(screen.getByTestId('unanswerable')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd web && npx vitest run`
Expected: FAIL — `AnswerView` does not exist.

- [ ] **Step 4: Implement `web/components/AnswerView.tsx`**

```tsx
export type SourceCard = {
  chunk_id: string
  source_url: string
  title: string | null
}

export type AnswerData = {
  answer: string
  sources: SourceCard[]
  language: string
  corpus_version: string
  footer: string
  crisis_response: string | null
  is_answerable: boolean
  quota_remaining: number
}

/**
 * Renders one verified answer.
 *
 * Order matters: a crisis response renders above everything else. A helpline
 * printed below a statutory explanation has already failed the person reading it.
 */
export default function AnswerView({ data }: { data: AnswerData }) {
  return (
    <article>
      {data.crisis_response && (
        <section role="alert" data-testid="crisis">
          <p>{data.crisis_response}</p>
        </section>
      )}

      {data.is_answerable ? (
        <p>{data.answer}</p>
      ) : (
        <p data-testid="unanswerable">
          {data.answer} You can still get help from a free legal aid lawyer.
        </p>
      )}

      {data.sources.length > 0 && (
        <section aria-label="Sources">
          <h2>Where this comes from</h2>
          <ul>
            {data.sources.map((source) => (
              <li key={source.chunk_id}>
                <a href={source.source_url} target="_blank" rel="noreferrer">
                  {source.title ?? source.source_url}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer>
        <p>{data.footer}</p>
        <p>
          Based on the {data.corpus_version} snapshot of Indian law. Always check
          the official source before relying on this.
        </p>
      </footer>
    </article>
  )
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd web && npx vitest run`
Expected: PASS (6 passed)

- [ ] **Step 6: Add the language selector**

Create `web/components/LanguageSelector.tsx`:

```tsx
const LANGUAGES: Array<{ code: string; label: string }> = [
  { code: 'auto', label: 'Detect automatically' },
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'bn', label: 'বাংলা' },
  { code: 'ta', label: 'தமிழ்' },
  { code: 'te', label: 'తెలుగు' },
  { code: 'mr', label: 'मराठी' },
  { code: 'gu', label: 'ગુજરાતી' },
  { code: 'kn', label: 'ಕನ್ನಡ' },
  { code: 'ml', label: 'മലയാളം' },
  { code: 'pa', label: 'ਪੰਜਾਬੀ' },
  { code: 'or', label: 'ଓଡ଼ିଆ' },
]

/**
 * Language chooser.
 *
 * Marathi shares Devanagari with Hindi, and Assamese shares its script with
 * Bengali, so automatic detection cannot tell them apart. An explicit choice is
 * the only way those speakers get served correctly.
 */
export default function LanguageSelector({
  value,
  onChange,
}: {
  value: string
  onChange: (code: string) => void
}) {
  return (
    <label>
      Language
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {LANGUAGES.map((language) => (
          <option key={language.code} value={language.code}>
            {language.label}
          </option>
        ))}
      </select>
    </label>
  )
}
```

- [ ] **Step 7: Commit**

```bash
git add web/
git commit -m "feat: chat interface with source cards and language selection"
```

---

## Task 7: One-command self-hosting

Success criterion §12.7 — **someone other than the author can self-host from the README** — made executable.

**Files:**
- Create: `Dockerfile`, `web/Dockerfile`, `scripts/verify_selfhost.sh`
- Modify: `docker-compose.yml`, `README.md`

**Interfaces:**
- Consumes: everything
- Produces: a verification script. No importable code.

- [ ] **Step 1: Create the API `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY data ./data
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "openlaw.api.deps:production_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add the services to `docker-compose.yml`**

```yaml
  api:
    build: .
    depends_on: [postgres, qdrant, neo4j]
    environment:
      OPENLAW_DATABASE_URL: postgresql+psycopg://openlaw:openlaw@postgres:5432/openlaw
      OPENLAW_QDRANT_URL: http://qdrant:6333
      OPENLAW_NEO4J_URI: bolt://neo4j:7687
      OPENLAW_VOYAGE_API_KEY: ${OPENLAW_VOYAGE_API_KEY}
      OPENLAW_ANTHROPIC_API_KEY: ${OPENLAW_ANTHROPIC_API_KEY}
      OPENLAW_QUOTA_ENABLED: ${OPENLAW_QUOTA_ENABLED:-false}
    ports: ["8000:8000"]

  web:
    build: ./web
    depends_on: [api]
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    ports: ["3000:3000"]
```

`OPENLAW_QUOTA_ENABLED` defaults to `false` here: a legal-aid body running its own instance should not inherit the hosted tier's rate limit.

> **Security note (carried from Plan 1's fix round).** The datastore ports are bound
> to `127.0.0.1` in Plan 1's compose file and must stay that way — only `api` and
> `web` are the intended reachable surface. Two things must change before any
> deployment that is not localhost-only: the Postgres password must come from the
> environment rather than the literal `openlaw`, and `api`/`web` must sit behind a
> reverse proxy terminating TLS. Qdrant and Neo4j must never be published beyond
> loopback — the API reaches them over the compose network by service name.

- [ ] **Step 3: Write the verification script**

Create `scripts/verify_selfhost.sh`:

```bash
#!/usr/bin/env bash
# Verify a clean self-host works end to end.
# This is spec success criterion 12.7 made executable.
set -euo pipefail

echo "[Selfhost] starting the full stack"
docker compose up -d

echo "[Selfhost] waiting for the API"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[Selfhost] checking health"
curl -fsS http://localhost:8000/health | grep -q '"status":"ok"'

echo "[Selfhost] checking every datastore is reachable"
curl -fsS http://localhost:6333/healthz >/dev/null
curl -fsS http://localhost:7474 >/dev/null

echo "[Selfhost] asking a question"
curl -fsS -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "My landlord will not return my security deposit."}' \
  | grep -q '"corpus_version"'

echo "[Selfhost] PASS — the stack is up and answering"
```

Then: `chmod +x scripts/verify_selfhost.sh`

- [ ] **Step 4: Extend `README.md`**

Add a **Self-hosting** section covering, in order: copy `.env.example` to `.env`; set `OPENLAW_VOYAGE_API_KEY` and `OPENLAW_ANTHROPIC_API_KEY`; run the corpus build in sequence — `scripts/restore_acts_india.py`, `scripts/build_sc_index.py`, `scripts/build_graph.py`, `scripts/run_gds.py`; `docker compose up -d`; `alembic upgrade head`; `./scripts/verify_selfhost.sh`. State plainly that quota is disabled by default for self-hosters, and repeat the attribution and coverage-limit notices.

- [ ] **Step 5: Run the verification**

```bash
./scripts/verify_selfhost.sh
```

Expected: `[Selfhost] PASS`.

- [ ] **Step 6: Run the full suite**

Run: `pytest && (cd web && npx vitest run)`
Expected: all tests pass, Python coverage at or above 80%.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile web/Dockerfile docker-compose.yml README.md scripts/verify_selfhost.sh
git commit -m "feat: one-command self-hosting with an executable verification"
```

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Covered by |
|---|---|
| §9 FastAPI + Postgres + Qdrant + Neo4j | Tasks 1, 5 |
| §9 Supabase email and Google auth | Tasks 3, 6 |
| §9 anonymous 3, signed-in 30/month | Tasks 2, 4 |
| §9 self-host disables quota | Tasks 2, 7 |
| §9 Docker Compose covering all datastores | Task 7 |
| §10.1 legal information, not advice, on every answer | Tasks 4, 6 |
| §10.2 DLSA/NALSA escalation on every answer | Tasks 4, 6 |
| §10.3 refuse and route on weak retrieval | Task 6 `unanswerable` branch |
| §10.4 crisis routing ahead of legal analysis | Tasks 4, 6 — ordering is tested |
| §10.5 never solicit identifying details | Task 1 — the user table holds only an email |
| §10.5 conversations user-deletable | Task 1 cascade delete, tested |
| §10.6 corpus date on every answer | Tasks 4, 6 |
| §10.7 hallucination log | Task 1 `HallucinationLog` |
| §12.6 first question in under 60 seconds | Task 4 — the anonymous allowance removes signup from the first question |
| §12.7 someone else can self-host | Task 7, executable |
| Multilingual plan's Marathi/Assamese limitation | Task 6 `LanguageSelector` |

**2. Placeholder scan.** No `TBD`, `TODO`, "implement later", or "similar to Task N". Task 7 Step 4 enumerates the README sections rather than quoting the file in full — a documentation step whose content depends on the final script names, with every required section named.

**3. Type consistency.**
- `AnswerData` in `AnswerView.tsx` matches `AskResponse` field for field, including `crisis_response: string | null` ↔ `str | None` and `sources: SourceCard[]` ↔ `list[SourceCard]`.
- `to_response` consumes `LocalisedAnswer` — the type `answer_in_language` returns (multilingual Task 4).
- `crisis_response` is imported as `crisis_text` in `schemas.py` to avoid shadowing the `AskResponse` field of the same name; the function is the one defined in retrieval-and-answers Task 7.
- `QuotaDecision.remaining` is a property, used as such in `routes.py`.
- `consume_quota(session, user_id, limit, enabled, today=None)` and `check_quota(used, limit, enabled)` — defined Task 2, called Task 4 with keyword arguments.
- `decode_token(token, secret)`, `extract_bearer(header)`, `upsert_user(session, identity)` — defined Task 3, called Task 4.
- `QdrantGraphRetriever.retrieve(english_query: str) -> RetrievalContext` satisfies the multilingual plan's `Retriever` Protocol exactly.
- `create_app(answer_fn, session_factory, settings)` — same signature in `app.py`, `deps.py`, and the tests.
- `session_factory()` is used as a context manager in `routes.py`; `session_scope` is one, and the test's `Session(engine)` supports the same protocol.
- `expand_chunks(driver, chunk_ids, limit)` — defined knowledge-graph Task 7, called in `deps.py`.

**Two risks worth naming rather than discovering in production:**

1. **The anonymous allowance is tracked in process memory keyed by client IP.** It resets on restart, is defeated by changing IP, and does not work across multiple API replicas. Adequate at launch — the allowance is 3 questions, so the abuse ceiling is low — but it is not a real rate limiter, and a shared store is needed before horizontal scaling.
2. **`build_answer_fn` opens a Neo4j driver per expansion call.** Correct but wasteful; a long-lived driver should replace it once request volume justifies the change.

---

## Execution Handoff

**Plan 5 of 5 complete.** Depends on all four prior plans.

All five plans are written:

1. `2026-09-08-corpus-foundation.md` — verified corpus in Qdrant
2. `2026-09-08-knowledge-graph.md` — Neo4j graph and GDS precomputes
3. `2026-09-08-retrieval-and-answers.md` — retrieval and the citation validator
4. `2026-09-08-multilingual.md` — per-role models and translation
5. `2026-09-08-application.md` — auth, quota, interface, self-hosting

Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
