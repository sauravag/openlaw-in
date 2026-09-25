"""Retention purge -- run periodically to enforce the documented 90-day
window on verbatim, potentially sensitive user text.

Final whole-plan review, H5. `purge_stale_events`
(`openlaw.answers.escalation`) and `purge_stale_hallucination_log`
(`openlaw.db.retention`) were both correct, unit-tested functions that
nothing in the shipped deployment ever called -- a written retention
promise (README's "Application database" section, and both functions' own
docstrings) with no mechanism behind it. Task 7's fix mounted `./data/app`
as a host volume so the hallucination and classifier-failure logs survive
container recreation (correct, and the reason this became a live gap rather
than an academic one: before that fix, the logs died with the container,
which accidentally bounded retention). This script IS that mechanism -- see
README's "Retention" section for exactly how an operator schedules it.

Purges THREE stores in one run, all against the same default retention
window (`DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS`, defined once in
`openlaw.answers.escalation` and mirrored in `openlaw.db.retention`, kept in
lockstep by `tests/db/test_retention.py::test_default_retention_window_is_90_days`):

- the hallucination-log JSONL file (`Settings.hallucination_log_path`).
- the classifier-failure-log JSONL file
  (`Settings.classifier_failure_log_path`) -- `purge_stale_events` is
  already generic (it only reads a `"timestamp"` field from each line), so
  it applies unmodified to this second log; see that function's docstring.
- the `hallucination_log` database table
  (`openlaw.db.retention.purge_stale_hallucination_log`) -- currently
  unused in production (see `openlaw.db.models.HallucinationLog`'s
  docstring), but purged regardless so this script is correct on the day
  something does write to it, rather than needing a second remembered step.

Exits 0 whether or not anything was actually removed: an empty or
not-yet-existing log is the normal state for a fresh self-host, not a
failure. Exits 1, with the exception printed to stderr, only if a purge
itself raises (e.g. the database is unreachable).

Run directly: `python scripts/purge_retention.py`
Or, under `docker compose`: `docker compose run --rm purge`
(see docker-compose.yml's `purge` service and README's "Retention" section).
"""

from __future__ import annotations

import sys

from openlaw.answers.escalation import purge_stale_events
from openlaw.config import get_settings
from openlaw.db.retention import purge_stale_hallucination_log
from openlaw.db.session import engine_from_settings, session_scope


def run_purge() -> None:
    """Purge every retention-bound store, printing what was removed from each."""
    settings = get_settings()

    hallucination_removed = purge_stale_events(settings.hallucination_log_path)
    print(
        f"[purge] hallucination log ({settings.hallucination_log_path}): "
        f"removed {hallucination_removed} event(s)"
    )

    classifier_removed = purge_stale_events(settings.classifier_failure_log_path)
    print(
        f"[purge] classifier-failure log ({settings.classifier_failure_log_path}): "
        f"removed {classifier_removed} event(s)"
    )

    engine = engine_from_settings(settings)
    with session_scope(engine) as session:
        db_removed = purge_stale_hallucination_log(session)
    print(f"[purge] hallucination_log table: removed {db_removed} row(s)")


def main() -> int:
    """Entry point. Returns the process exit code (never raises)."""
    try:
        run_purge()
    except Exception as exc:  # noqa: BLE001 - this IS the operational boundary
        print(f"[purge] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
