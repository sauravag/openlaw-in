"""Monthly quota metering.

Thirty questions a month is deliberately generous enough that one real legal
problem can be worked through end to end, and bounded enough that costs stay
predictable. Self-hosted deployments — legal-aid authorities, NGOs, law-school
clinics — disable it entirely with one configuration flag.

`check_quota` is pure, so the policy is testable without a database.

**Why the increment is one atomic UPDATE, not read-modify-write.** Under
Postgres's default READ COMMITTED isolation, "read `questions_used`, add one,
write it back" is not one operation — it is three, with a gap in the middle
where a second concurrent request (a second browser tab, a double-tap, a
client retry after a timeout) can read the same stale value. Both requests
then compute the same "+1", both see themselves as under the limit, and one
increment is silently lost: the counter undercounts real usage, and more
answers get served than the limit allows — the exact failure this module
exists to prevent. `consume_quota` instead issues a single
`UPDATE ... SET questions_used = questions_used + 1 WHERE questions_used <
limit RETURNING questions_used`: the row lock Postgres takes for that
statement's duration means a second concurrent UPDATE against the same row
waits and then sees the first one's result, rather than racing it. The
check (`questions_used < limit`) and the increment happen in the same
statement, so there is no gap between them either — nothing can slip past
the limit in a window that does not exist.

`consume_quota` commits before returning, deliberately taking ownership of
its own transaction boundary rather than deferring to the caller: the row
lock above is only released on commit, and quota correctness must not
depend on how long the rest of the caller's request takes. Call it before
staging other writes on the same session, or expect those writes to be
committed alongside it.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
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


def _ensure_usage_row(session: Session, user_id: str, period: date) -> None:
    """Create this user's usage row for `period` if it does not exist yet.

    Checks first (the common case: the row already exists) and only
    attempts an INSERT when it is missing. Two concurrent first-questions-
    of-the-month can both reach the INSERT with no row present — only one
    can win against `UniqueConstraint("user_id", "period_month")` — so the
    loser's `IntegrityError` is caught and swallowed here rather than
    raised: the row it wanted now exists, and the atomic UPDATE in
    `consume_quota` is what actually enforces correctness, not this
    function.
    """
    existing = session.execute(
        select(QuotaUsage.id).where(
            QuotaUsage.user_id == user_id, QuotaUsage.period_month == period
        )
    ).first()
    if existing is not None:
        return

    session.add(QuotaUsage(user_id=user_id, period_month=period, questions_used=0))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()


def _current_usage(session: Session, user_id: str, period: date) -> int:
    """Return this user's `questions_used` for `period`."""
    return session.execute(
        select(QuotaUsage.questions_used).where(
            QuotaUsage.user_id == user_id, QuotaUsage.period_month == period
        )
    ).scalar_one()


def consume_quota(
    session: Session,
    user_id: str,
    limit: int,
    enabled: bool,
    today: date | None = None,
) -> QuotaDecision:
    """Consume one question from `user_id`'s allowance for the current month.

    Returns a refusal without incrementing when the allowance is exhausted,
    and writes nothing at all when quota is disabled. See the module
    docstring for why the increment is one atomic conditional UPDATE rather
    than a read-modify-write, and why this function commits its own
    transaction.
    """
    if not enabled:
        return QuotaDecision(allowed=True, used=0, limit=limit)

    period = current_period(today)
    _ensure_usage_row(session, user_id, period)

    result = session.execute(
        update(QuotaUsage)
        .where(
            QuotaUsage.user_id == user_id,
            QuotaUsage.period_month == period,
            QuotaUsage.questions_used < limit,
        )
        .values(questions_used=QuotaUsage.questions_used + 1)
        .returning(QuotaUsage.questions_used)
    )
    row = result.first()

    if row is None:
        used = _current_usage(session, user_id, period)
        session.commit()
        return QuotaDecision(allowed=False, used=used, limit=limit)

    session.commit()
    return QuotaDecision(allowed=True, used=row[0], limit=limit)
