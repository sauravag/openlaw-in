"""Erase an account and everything it owns (DPDP right to erasure).

This is the only code that destroys user data on request.

Foreign keys (all `ON DELETE CASCADE`, so the database would also remove the
children): `conversations.user_id`, `documents.user_id` and
`quota_usage.user_id` -> `users.id`; `messages.conversation_id` and
`documents.conversation_id` -> `conversations.id`; `document_excerpts.
document_id` -> `documents.id`. Because a cascade would silently swallow the
counts, every child is COUNTED and deleted explicitly, in this order:
documents (and their excerpts), messages, conversations, quota rows, then the
`users` row. Defence in depth: the order never depends on a cascade, and a
failure part-way leaves owned rows rather than ownerless ones (which
`ck_conversations_has_owner` forbids outright).

Transaction convention (as `openlaw.accounts.migrate`): the CALLER owns the
commit. This function only flushes, so every delete and every audit row
commits or rolls back together. Ownership goes only through `owner_clause`.

`document_access_log` rows are deliberately NOT deleted, and the count is
returned so the caller can say so honestly. They establish who read what,
carry no document content, and `DocumentAccessLog.document_id` is
deliberately not a foreign key for exactly this reason (see
`openlaw.documents.store.audit`). Before each document is removed one
`delete` row is written -- the action the single-document delete path already
uses -- so the retained trail records that this account erased it. Under
DPDP the legitimate-purpose basis for retaining an access log survives
erasure of the content; the UI must TELL the person this.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from openlaw.db.conversations import Owner, owner_clause
from openlaw.db.models import (
    Conversation,
    Document,
    DocumentAccessLog,
    DocumentExcerptRow,
    Message,
    QuotaUsage,
    User,
)
from openlaw.documents.store import audit

#: Same action name `openlaw.documents.store.delete_document` audits with.
DELETE_ACTION = "delete"


@dataclass(frozen=True)
class DeletionResult:
    """What one erasure removed. All zeros on a repeat."""

    conversations_deleted: int
    documents_deleted: int
    messages_deleted: int
    audit_rows_retained: int


def _delete_documents(session: Session, owner: Owner) -> list[str]:
    """Audit then delete the owner's documents and excerpts; return their ids."""
    document_ids = list(
        session.scalars(select(Document.id).where(owner_clause(Document, owner)))
    )
    for document_id in document_ids:
        audit(session, document_id, owner, DELETE_ACTION)
    if document_ids:
        session.execute(
            delete(DocumentExcerptRow).where(
                DocumentExcerptRow.document_id.in_(document_ids)
            )
        )
        session.execute(delete(Document).where(Document.id.in_(document_ids)))
    return document_ids


def _delete_messages(session: Session, conversation_ids: list[str]) -> int:
    if not conversation_ids:
        return 0
    result = session.execute(
        delete(Message).where(Message.conversation_id.in_(conversation_ids))
    )
    return result.rowcount or 0


def _count_retained_audit_rows(session: Session, document_ids: list[str]) -> int:
    if not document_ids:
        return 0
    return session.scalar(
        select(func.count())
        .select_from(DocumentAccessLog)
        .where(DocumentAccessLog.document_id.in_(document_ids))
    ) or 0


def delete_account(session: Session, user_id: str) -> DeletionResult:
    """Erase `user_id`'s account and everything it owns.

    Does not commit. Raises `ValueError` for an empty `user_id`, before any
    SQL. Idempotent: a repeat matches nothing and returns zeros.
    """
    if not user_id:
        raise ValueError("user_id must be non-empty")
    owner = Owner(user_id=user_id, anon_token=None)
    # Lock the users row first: a child insert by this same user takes a
    # FOR KEY SHARE lock on it, so a concurrent insert either committed before
    # this point (and is counted and audited below) or waits for this commit.
    session.execute(select(User.id).where(User.id == user_id).with_for_update())

    document_ids = _delete_documents(session, owner)
    conversation_ids = list(
        session.scalars(select(Conversation.id).where(owner_clause(Conversation, owner)))
    )
    messages_deleted = _delete_messages(session, conversation_ids)
    if conversation_ids:
        session.execute(
            delete(Conversation).where(Conversation.id.in_(conversation_ids))
        )
    session.execute(delete(QuotaUsage).where(QuotaUsage.user_id == user_id))
    session.execute(delete(User).where(User.id == user_id))
    session.flush()

    return DeletionResult(
        conversations_deleted=len(conversation_ids),
        documents_deleted=len(document_ids),
        messages_deleted=messages_deleted,
        audit_rows_retained=_count_retained_audit_rows(session, document_ids),
    )
