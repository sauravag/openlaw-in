"""Move an anonymous person's material into their account at sign-in.

This is the only code that changes who owns a row.

Owner-bearing tables (audited by grep): `conversations` and `documents`
carry `user_id`/`anon_token`/`expires_at` and are moved here.
`document_excerpts` and `messages` carry none of them -- they hang off their
parent by foreign key, so they follow the parent with no update of their own.

Transaction convention: the CALLER owns the commit (as in
`openlaw.db.retention` and `openlaw.db.conversations`). This function only
flushes, so the moves and the `migrate` audit rows (written on the same
session via `openlaw.documents.store.audit`) commit or roll back together.

THREAT, stated rather than mitigated away: anyone holding a person's
`openlaw_anon_id` cookie value can migrate that person's material into their
own account. The cookie is HttpOnly and SameSite=Lax and only sent to this
origin, so obtaining it means the device, browser profile or TLS
interception -- which already allows reading the material directly. Migration
adds PERSISTENCE and an audit trail (`document_access_log` names the claiming
account); it grants nothing the cookie holder could not already read. It is
therefore bound to a cookie the browser never exposes, never to anything a
person could type.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from openlaw.config import Settings
from openlaw.db.conversations import Owner, reassign_owner
from openlaw.db.models import Conversation, Document
from openlaw.documents.store import audit

MIGRATE_ACTION = "migrate"


@dataclass(frozen=True)
class MigrationResult:
    """What one migration attempt moved. All zeros on a repeat attempt.

    `already_migrated=True` also covers a token that never owned anything
    (deliberately indistinguishable, so a probe learns nothing). Callers must
    not show "already migrated" to a person; to them it means "nothing to
    move".
    """

    conversations_moved: int
    documents_moved: int
    already_migrated: bool


def migrate_anonymous_material(
    session: Session,
    anon_token: str,
    user_id: str,
    settings: Settings,
) -> MigrationResult:
    """Reassign everything owned by `anon_token` to `user_id`.

    Goes through `reassign_owner` (`owner_clause`/`owner_columns`), so it
    cannot touch a row another account owns. Idempotent: a retry matches no
    anonymous rows and returns zeros with `already_migrated=True`. Clears
    `expires_at` on what it moves. Writes one `migrate` audit row per moved
    document, attributed to the destination account. Does not commit; the
    caller's transaction makes the whole move atomic.

    Raises `ValueError` for an empty token or user id, before any SQL.
    """
    if not anon_token or not user_id:
        raise ValueError("anon_token and user_id must both be non-empty")
    source = Owner(user_id=None, anon_token=anon_token)
    destination = Owner(user_id=user_id, anon_token=None)

    conversation_ids = reassign_owner(session, Conversation, source, destination)
    document_ids = reassign_owner(session, Document, source, destination)
    for document_id in document_ids:
        audit(session, document_id, destination, MIGRATE_ACTION)

    return MigrationResult(
        conversations_moved=len(conversation_ids),
        documents_moved=len(document_ids),
        already_migrated=not conversation_ids and not document_ids,
    )
