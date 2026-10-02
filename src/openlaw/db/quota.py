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
`UPDATE ... SET questions_used = questions_used + :cost WHERE
questions_used + :cost <= limit RETURNING questions_used`: the row lock Postgres takes for that
statement's duration means a second concurrent UPDATE against the same row
waits and then sees the first one's result, rather than racing it. The
check (`questions_used + :cost <= limit`) and the increment happen in the same
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


def next_period_start(today: date | None = None) -> date:
    """Return the first day of the month AFTER `today`'s.

    Used to tell a quota-refused caller *when* their allowance comes back --
    a bare "limit reached" with no date is a dead end (Task 4 review, M6).
    Kept alongside `current_period` since both are the same month-boundary
    arithmetic and a caller (`openlaw.api.routes`) needs both: the current
    period to check/consume against, and the next one to report as the reset
    date.
    """
    period = current_period(today)
    if period.month == 12:
        return date(period.year + 1, 1, 1)
    return date(period.year, period.month + 1, 1)


def _require_positive_cost(cost: int) -> None:
    """Reject a cost below one -- a free request is not a metered request.

    A zero or negative cost would make the conditional UPDATE in
    `consume_quota` a no-op that still reported `allowed=True`, which is
    silent unmetered usage rather than an error anybody would notice. Raised
    rather than clamped: every cost in this codebase comes from
    `max(1, ...)` arithmetic, so a value below one is a bug at the call
    site, not a caller's choice.
    """
    if cost < 1:
        raise ValueError(f"quota cost must be at least 1, got {cost}")


def check_quota(used: int, limit: int, enabled: bool, cost: int = 1) -> QuotaDecision:
    """Return the quota decision for a user who has used `used` questions.

    `cost` is how many units THIS request consumes (Plan 6 Task 15): a turn
    costs 1, an upload costs `max(1, ceil(page_count / PAGES_PER_QUOTA_UNIT))`
    -- see `openlaw.documents.PAGES_PER_QUOTA_UNIT`. The decision is whether
    the whole cost fits, never whether one unit does: a 4-unit upload with 2
    units left is refused outright rather than consuming what it can.

    `used` is reported back unchanged, exactly as it is given: this function
    is pure policy and the caller owns the counter (see
    `openlaw.api.routes._anonymous_decision`, which adds the cost to its own
    in-memory tally only once the decision allows it).
    """
    _require_positive_cost(cost)
    if not enabled:
        return QuotaDecision(allowed=True, used=used, limit=limit)
    return QuotaDecision(allowed=used + cost <= limit, used=used, limit=limit)


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
    cost: int = 1,
) -> QuotaDecision:
    """Consume `cost` units from `user_id`'s allowance for the current month.

    Returns a refusal without incrementing when the allowance cannot absorb
    the WHOLE cost, and writes nothing at all when quota is disabled. See the
    module docstring for why the increment is one atomic conditional UPDATE
    rather than a read-modify-write, and why this function commits its own
    transaction.

    `cost` defaults to 1 -- one question, the only cost that existed before
    Plan 6 Task 15. An upload costs `max(1, ceil(page_count /
    PAGES_PER_QUOTA_UNIT))`, because a 20-page document is not the same
    expense as a one-line question: it is parsed, crisis-scanned, encrypted
    page by page, and then re-read into the model's context on every later
    turn of its conversation. The `questions_used + cost <= limit` guard keeps
    the check and the increment inside the same statement for a multi-unit
    cost exactly as it did for a single one, so a 4-unit upload can never
    half-consume an allowance that had room for only 2.
    """
    _require_positive_cost(cost)
    if not enabled:
        return QuotaDecision(allowed=True, used=0, limit=limit)

    period = current_period(today)
    _ensure_usage_row(session, user_id, period)

    result = session.execute(
        update(QuotaUsage)
        .where(
            QuotaUsage.user_id == user_id,
            QuotaUsage.period_month == period,
            QuotaUsage.questions_used + cost <= limit,
        )
        .values(questions_used=QuotaUsage.questions_used + cost)
        .returning(QuotaUsage.questions_used)
    )
    row = result.first()

    if row is None:
        used = _current_usage(session, user_id, period)
        session.commit()
        return QuotaDecision(allowed=False, used=used, limit=limit)

    session.commit()
    return QuotaDecision(allowed=True, used=row[0], limit=limit)
