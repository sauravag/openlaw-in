"""Tests for `openlaw.db.retention.purge_stale_hallucination_log`.

Mirrors `tests/answers/test_escalation.py`'s coverage of `purge_stale_events`
(the JSONL sibling): a row inside the retention window survives, a row
outside it is deleted, and the default matches the documented 90 days.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.db.models import Base, HallucinationLog
from openlaw.db.retention import (
    DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
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
