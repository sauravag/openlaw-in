import re
from datetime import date
from pathlib import Path

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


# --- Final whole-plan review, M3 (Plan 5), revisited in Plan 6 Task 2:
# six tables are created and, today, still not written by any shipped code
# path -- see openlaw.db.models's module docstring for the split this test
# now tracks: `Conversation`/`Message`/`Document`/`DocumentExcerptRow`/
# `DocumentAccessLog` have a *known* future writer (Task 14 onward of the
# conversations-and-artifacts plan) but no writer yet, while
# `HallucinationLog` stays reserved indefinitely pending its restricted-role
# precondition. Both halves are still "no production writer today", so both
# stay covered by the same grep-based guard: if a future change starts
# constructing any of them from `src/` or `scripts/` without updating the
# docstring (and, for the first five, without this table actually landing in
# a persistence layer), this test is what catches it. -----------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_DIRS = (_REPO_ROOT / "src", _REPO_ROOT / "scripts")
_NOT_YET_WRITTEN_TABLE_CLASSES = (
    "Conversation",
    "Message",
    "Document",
    "DocumentExcerptRow",
    "DocumentAccessLog",
    "HallucinationLog",
)


def _unwritten_class_constructed_in(path: Path) -> str | None:
    """Return the first not-yet-written table class this file constructs
    (`Name(`), or None. A crude, deliberately simple grep -- exactly as
    trustworthy as the review's own `grep -rn` that found the gap in the
    first place."""
    text = path.read_text(encoding="utf-8")
    for name in _NOT_YET_WRITTEN_TABLE_CLASSES:
        if re.search(rf"\b{name}\(", text):
            return name
    return None


def test_reserved_unused_tables_have_no_production_writer() -> None:
    """None of `Conversation`, `Message`, `Document`, `DocumentExcerptRow`,
    `DocumentAccessLog`, or `HallucinationLog` is written by any shipped code
    path today (`openlaw.db.models`'s module docstring). This is the
    boundary test for that claim: if a future change starts constructing any
    of them from `src/` or `scripts/`, this test fails -- forcing that
    change to also update the documentation that currently asserts
    otherwise, rather than the two silently drifting apart the way
    `verify_selfhost.sh` and `escalation.py`'s stale comment already did
    elsewhere in this plan.

    As of Plan 6 Task 2, `Conversation`/`Message`/`Document`/
    `DocumentExcerptRow`/`DocumentAccessLog` are no longer *speculative*
    placeholders -- their schema is final and a writer is planned for Task
    14 onward -- but no shipped code writes to them yet, so this test still
    holds for all six until that writer lands. `HallucinationLog` alone
    remains reserved indefinitely, pending the restricted database role its
    own class docstring requires.
    """
    offenders: list[str] = []
    for directory in _PRODUCTION_DIRS:
        for path in directory.rglob("*.py"):
            if path.name == "models.py":
                continue  # the table definitions themselves, not a writer
            offender = _unwritten_class_constructed_in(path)
            if offender is not None:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: constructs {offender}(...)")

    assert offenders == []
