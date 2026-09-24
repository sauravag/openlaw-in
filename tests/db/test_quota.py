"""Tests for `openlaw.db.quota` — the policy is pure and DB-free
(`check_quota`), while `consume_quota` is exercised both against a
throwaway SQLite engine (single-session behaviour) and, in one
`@pytest.mark.integration` test, against genuinely concurrent sessions on
live Postgres — see that test's docstring for why SQLite cannot stand in
for it.
"""

import threading
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.db.models import Base, QuotaUsage, User
from openlaw.db.quota import check_quota, consume_quota, current_period, next_period_start
from openlaw.db.session import session_scope


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


def test_next_period_start_is_the_first_of_next_month() -> None:
    assert next_period_start(date(2026, 9, 8)) == date(2026, 10, 1)


def test_next_period_start_rolls_over_the_year_in_december() -> None:
    assert next_period_start(date(2026, 12, 15)) == date(2027, 1, 1)


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
    assert session.query(QuotaUsage).count() == 1


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


@pytest.mark.integration
def test_concurrent_requests_never_exceed_the_limit(live_engine) -> None:
    """Genuinely concurrent sessions must not lose increments.

    SQLite cannot stand in for this test: a single-process SQLite database
    serialises all writes through one file lock, so a read-modify-write
    quota implementation would pass here even though it loses increments
    under Postgres's READ COMMITTED isolation, where two sessions can each
    hold their own snapshot of `questions_used` at once. This test opens a
    genuinely separate `Session` per thread — mirroring one session per HTTP
    request in production — and fires them at the same user concurrently. A
    read-modify-write `consume_quota` would let more than `limit` of them
    see `allowed=True` (the lost-increment bug the module docstring
    describes); the atomic conditional UPDATE must not.
    """
    Base.metadata.create_all(live_engine, checkfirst=True)

    with session_scope(live_engine) as setup_session:
        user = User(email="concurrent-quota-test@example.com")
        setup_session.add(user)
        setup_session.flush()
        user_id = user.id

    limit = 5
    request_count = 20
    today = date(2026, 9, 8)
    results: list[bool] = []
    results_lock = threading.Lock()

    def make_request() -> None:
        with session_scope(live_engine) as thread_session:
            decision = consume_quota(
                thread_session, user_id, limit=limit, enabled=True, today=today
            )
        with results_lock:
            results.append(decision.allowed)

    try:
        threads = [
            threading.Thread(target=make_request) for _ in range(request_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == limit
        assert results.count(False) == request_count - limit

        with session_scope(live_engine) as check_session:
            stored = (
                check_session.query(QuotaUsage)
                .filter(
                    QuotaUsage.user_id == user_id,
                    QuotaUsage.period_month == date(2026, 9, 1),
                )
                .one()
            )
            assert stored.questions_used == limit
    finally:
        with session_scope(live_engine) as cleanup_session:
            cleanup_session.query(QuotaUsage).filter(
                QuotaUsage.user_id == user_id
            ).delete()
            cleanup_session.query(User).filter(User.id == user_id).delete()
