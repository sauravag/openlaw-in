"""Retention purge -- run periodically to enforce every retention promise
this project makes on verbatim, potentially sensitive user text and on the
user-owned rows Plan 6 (conversations-and-artifacts) added.

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

Plan 6 Task 16 extends this same script to `documents` and `conversations`,
on a rule that is deliberately asymmetric rather than a uniform age cutoff:
**an authenticated user's uploads and conversations (`expires_at IS NULL`)
are NEVER purged by this script, no matter their age -- only an explicit
user deletion removes those.** Only an ANONYMOUS upload or conversation past
its `Settings.anonymous_upload_ttl_hours` / `anonymous_conversation_ttl_hours`
TTL (set at write time, Task 14) is eligible, because a cookie-keyed
identity cannot reliably come back and delete anything itself -- see
`openlaw.db.retention`'s module docstring for why. `document_access_log`
rows are a fourth, independent store: never deleted by a document's own
purge (by design -- an audit record must outlive its subject), aged out on
the SAME 90-day window as the hallucination log instead.

Purges SEVEN things in one run:

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
- the `documents` table (`purge_expired_documents`) -- expired anonymous
  uploads only, cascading to their excerpts.
- the `conversations` table (`purge_expired_conversations`) -- expired
  anonymous conversations only, cascading to their messages and documents.
- the `document_access_log` table (`purge_stale_document_audit`) -- rows
  older than the 90-day window, independent of whether the document they
  describe still exists.

The four database purges share ONE transaction (one `Session`), committed
together on success -- `run_purge` does not partially apply them. Exits 0
whether or not anything was actually removed: an empty or not-yet-existing
log, or a database with nothing stale, is the normal state for a fresh
self-host, not a failure. Exits 1, with the exception printed to stderr,
only if a purge itself raises (e.g. the database is unreachable) -- and in
that case NOTHING from this run is committed, by the same transaction.

**`--dry-run` reports every count above without deleting or rewriting
anything.** The four database purges run against a real session so their
counts are exact, then the session is rolled back instead of committed; the
two JSONL log purges are skipped entirely (a dry preview would otherwise
need its own copy of the file-filtering logic, which could drift from the
real one) and reported as skipped. Safe to run before ever trusting a cron
schedule with real data, and safe to run twice back to back: a second run,
dry or not, finds nothing newly stale and reports zeroes.

Run directly: `python scripts/purge_retention.py [--dry-run]`
Or, under `docker compose`: `docker compose run --rm purge [-- --dry-run]`
(see docker-compose.yml's `purge` service and README's "Retention" section).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from openlaw.answers.escalation import purge_stale_events
from openlaw.config import get_settings
from openlaw.db.retention import (
    purge_expired_conversations,
    purge_expired_documents,
    purge_stale_document_audit,
    purge_stale_hallucination_log,
)
from openlaw.db.session import engine_from_settings


def _purge_database_stores(engine: Engine, dry_run: bool) -> tuple[int, int, int, int]:
    """Run all four database purges in one transaction.

    Commits on success unless `dry_run` is set, in which case the same
    queries and deletes run (so the returned counts are exact) but the
    transaction is rolled back, leaving the database unchanged. Rolls back
    and re-raises on any failure either way -- a half-applied purge across
    these four tables is not an acceptable partial state.
    """
    session = Session(engine)
    try:
        hallucination_removed = purge_stale_hallucination_log(session)
        documents_removed = purge_expired_documents(session)
        conversations_removed = purge_expired_conversations(session)
        audit_removed = purge_stale_document_audit(session)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return hallucination_removed, documents_removed, conversations_removed, audit_removed


def run_purge(dry_run: bool = False) -> None:
    """Purge every retention-bound store, printing what was removed from each.

    See the module docstring for exactly what `dry_run=True` does and does
    not simulate.
    """
    settings = get_settings()
    tag = "[purge] (dry-run) " if dry_run else "[purge] "

    if dry_run:
        print(f"{tag}hallucination log ({settings.hallucination_log_path}): skipped")
        print(
            f"{tag}classifier-failure log "
            f"({settings.classifier_failure_log_path}): skipped"
        )
    else:
        hallucination_removed = purge_stale_events(settings.hallucination_log_path)
        print(
            f"{tag}hallucination log ({settings.hallucination_log_path}): "
            f"removed {hallucination_removed} event(s)"
        )
        classifier_removed = purge_stale_events(settings.classifier_failure_log_path)
        print(
            f"{tag}classifier-failure log ({settings.classifier_failure_log_path}): "
            f"removed {classifier_removed} event(s)"
        )

    engine = engine_from_settings(settings)
    hallucination_db, documents, conversations, audit = _purge_database_stores(
        engine, dry_run
    )
    print(f"{tag}hallucination_log table: removed {hallucination_db} row(s)")
    print(f"{tag}documents table: removed {documents} row(s)")
    print(f"{tag}conversations table: removed {conversations} row(s)")
    print(f"{tag}document_access_log table: removed {audit} row(s)")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be purged without deleting or rewriting anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code (never raises).

    `argv` defaults to an empty argument list, NOT `sys.argv` -- so calling
    `main()` directly (as every test in `tests/scripts/test_purge_retention.py`
    does) never picks up pytest's own command-line arguments. The real CLI
    entry point below passes `sys.argv[1:]` explicitly.
    """
    args = _parse_args([] if argv is None else argv)
    try:
        run_purge(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - this IS the operational boundary
        print(f"[purge] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
