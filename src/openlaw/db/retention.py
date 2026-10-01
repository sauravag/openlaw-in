"""Retention purge for database tables holding verbatim, sensitive text, and
for the user-owned rows the Plan 6 conversations-and-artifacts schema adds.

Mirrors `openlaw.answers.escalation.purge_stale_events`: that function
enforces a 90-day retention window on the JSONL hallucination log by
rewriting the file in place. `HallucinationLog` (see its docstring) is a
second copy of the same class of sensitive data and must not be allowed to
grow forever with no counterpart purge -- this module is that counterpart.

**Task 16 extends the same module to `Document` and `Conversation`, on a
deliberately asymmetric rule: authenticated rows (`expires_at IS NULL`) are
NEVER touched here, under any `max_age_days`, no matter how old -- only an
explicit user deletion (`openlaw.documents.store.delete_document`,
`openlaw.db.conversations.delete_conversation`) removes those.** Anonymous
rows carry a bounded TTL in `expires_at` (Task 1's
`anonymous_upload_ttl_hours` / `anonymous_conversation_ttl_hours`, applied at
write time by Task 14) precisely because a cookie-keyed identity cannot
reliably come back and delete anything -- an undeletable FIR belonging to
nobody is the outcome this purge prevents, not the outcome it could cause.
The two purge functions below express that rule structurally: their WHERE
clause excludes `expires_at IS NULL` rows outright, so "authenticated never
expires" is not a runtime check a future edit could weaken, but a condition
an authenticated row can never satisfy.

**`DocumentAccessLog` rows are never deleted by `purge_expired_documents`.**
`document_id` is deliberately not a foreign key (Task 14's module docstring,
`openlaw.documents.store`) so an audit record outlives the document it
describes; purging a `Document` here relies on the ORM relationship cascade
(`Document.excerpts`), which has no edge into `document_access_log` at all,
so there is nothing for a document purge to cascade into even accidentally.
`purge_stale_document_audit` is the separate, independent purge for that
table -- see its own docstring for why audit rows are *not* kept forever
either.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openlaw.db.models import Conversation, Document, DocumentAccessLog, HallucinationLog

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


def purge_expired_documents(session: Session) -> int:
    """Delete `Document` rows whose `expires_at` has passed.

    `Document.expires_at.is_not(None)` makes an authenticated upload
    (`expires_at IS NULL`, per that column's own docstring) structurally
    unreachable by this query -- not skipped by a runtime check, but a row
    no value of `max_age_days` could ever match, because there is no
    `max_age_days` here at all. Deletion goes through the ORM
    (`session.delete`, not a bulk `DELETE` statement) specifically so
    `Document.excerpts`'s `cascade="all, delete-orphan"` relationship fires
    and each expired document's `DocumentExcerptRow` siblings are destroyed
    with it -- the encrypted excerpt ciphertext never needs a separate
    delete. `DocumentAccessLog` rows for a purged document are untouched;
    see the module docstring for why that is correct, not an oversight.
    Returns the number of documents deleted (0 if none are stale). Flushes
    but does not commit -- same contract as `purge_stale_hallucination_log`.
    """
    now = datetime.now(UTC)
    expired = (
        session.execute(
            select(Document).where(
                Document.expires_at.is_not(None), Document.expires_at < now
            )
        )
        .scalars()
        .all()
    )
    for document in expired:
        session.delete(document)
    session.flush()
    return len(expired)


def purge_expired_conversations(session: Session) -> int:
    """Delete anonymous `Conversation` rows whose `expires_at` has passed.

    Same NULL-excludes-authenticated-rows reasoning as
    `purge_expired_documents`. Deletion goes through the ORM so
    `Conversation.messages` and `Conversation.documents`'s
    `cascade="all, delete-orphan"` relationships fire: every message in an
    expired anonymous conversation is destroyed, and every document
    attached to it is destroyed (cascading in turn to that document's own
    excerpts) even if that document's own `expires_at` has not yet elapsed
    on its own. A conversation's lifetime bounds everything filed under it.
    Returns the number of conversations deleted. Flushes but does not
    commit -- same contract as the other purge functions in this module.
    """
    now = datetime.now(UTC)
    expired = (
        session.execute(
            select(Conversation).where(
                Conversation.expires_at.is_not(None), Conversation.expires_at < now
            )
        )
        .scalars()
        .all()
    )
    for conversation in expired:
        session.delete(conversation)
    session.flush()
    return len(expired)


def purge_stale_document_audit(
    session: Session,
    max_age_days: int = DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
) -> int:
    """Delete `DocumentAccessLog` rows older than `max_age_days`.

    An audit trail that is itself immortal is its own privacy problem: a
    record of who read which (possibly long-deleted) eviction notice,
    retained forever, is exactly the kind of permanent, cross-referenceable
    log this project's data-minimisation stance (`openlaw.db.models`'s
    module docstring) argues against. So audit rows are purged too --
    deliberately on the SAME `DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS`
    window as the hallucination log, rather than a bespoke one, so the
    project has one documented retention period for verbatim/sensitive
    records instead of several to keep in sync. This is independent of
    `purge_expired_documents`: an access log row survives the document it
    describes (by design, see the module docstring) and is aged out here on
    its own clock, not when its subject is purged -- a record that vanished
    the moment its document did would be useless as an audit trail (it could
    never show what happened to a document that is now gone).
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    result = session.execute(
        delete(DocumentAccessLog).where(DocumentAccessLog.created_at < cutoff)
    )
    return result.rowcount or 0
