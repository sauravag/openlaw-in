"""Retention purge for database tables holding verbatim, sensitive text.

Mirrors `openlaw.answers.escalation.purge_stale_events`: that function
enforces a 90-day retention window on the JSONL hallucination log by
rewriting the file in place. `HallucinationLog` (see its docstring) is a
second copy of the same class of sensitive data and must not be allowed to
grow forever with no counterpart purge -- this module is that counterpart.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from openlaw.db.models import HallucinationLog

#: Default retention window in days, matching
#: `openlaw.answers.escalation.DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS` --
#: the JSONL log and this table hold the same class of verbatim, sensitive
#: text and must not diverge on how long they keep it.
DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS = 90


def purge_stale_hallucination_log(
    session: Session,
    max_age_days: int = DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
) -> int:
    """Delete `HallucinationLog` rows older than `max_age_days`.

    Returns the number of rows deleted (0 if none are stale). Does not
    commit `session` -- callers control the transaction, e.g. via
    `openlaw.db.session.session_scope`. Intended to run periodically (an
    operational maintenance job), not on every write, the same way
    `purge_stale_events` is run separately from `append_events`.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    result = session.execute(
        delete(HallucinationLog).where(HallucinationLog.created_at < cutoff)
    )
    return result.rowcount or 0
