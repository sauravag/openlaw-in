"""Document artifact persistence, scoped to the owning identity (Plan 6 Task 14).

Extends the ownership model `openlaw.db.conversations` establishes to the
one kind of row more sensitive than conversation text: an uploaded eviction
notice, FIR, or medical record. `Owner`, `AccessDenied`, `owner_clause`, and
`owner_columns` are imported and reused from that module rather than
re-derived here -- the ownership rule (never the caller's responsibility to
filter correctly) must not have two implementations that can drift apart.

**Encrypted at rest, with a `key_id` that is never optional.**
`openlaw.documents.crypto.encrypt` always returns `(ciphertext, nonce,
key_id)`, and every write below uses all three. `Document.key_id`/
`DocumentExcerptRow.key_id` are `nullable=False` (Task 3b) specifically so a
write that tried to omit it, or pass a placeholder, fails loudly at the
database rather than silently breaking a future key rotation -- this module
never constructs either row without the `key_id` `encrypt` just returned.

**What gets encrypted, and under what AAD.** `Document.ciphertext` holds the
document's extracted text (all pages joined), AAD-bound to the document's
own id -- the whole-document copy a future "show me everything I uploaded"
view would read. `DocumentExcerptRow.ciphertext` holds one excerpt's text,
AAD-bound to that excerpt's own id -- the per-excerpt copy `load_excerpts`
decrypts for citation, which is deliberately re-derived from the stored
excerpts rather than by re-chunking the whole-document copy, so the two
never need to agree on chunking logic at read time. See `openlaw.documents
.crypto`'s module docstring for why AAD binding, not just the key, is what
stops a ciphertext blob from decrypting under a row it was never written
for.

**Anonymous rows expire; authenticated rows do not.** Same reasoning as
`openlaw.db.conversations.create_conversation` -- `store_document` sets
`expires_at` to `now + Settings.anonymous_upload_ttl_hours` for an
anonymous owner and `NULL` for an authenticated one.

**Every access leaves a record.** `audit` appends one row to
`document_access_log` (Task 2) -- deliberately not FK-linked to `documents`,
so the record outlives a deleted document. Every function below that
touches a document (create, read, delete) calls it, including the denial
path: a mismatch writes `action="denied"` before `AccessDenied` is raised
(`load_attached_documents`) or `False` is returned (`delete_document`), so
"who tried to read what, and when" survives even a refusal.

**...but only durably if the caller's transaction commits, which is why
`audit_fn` is injectable (Plan 6 Task 15 ruling).** `audit` writes on the
session it is handed, so it lives and dies with that session's transaction --
and a denied access is the single request most likely to end in a rollback,
which is precisely the row an audit trail exists to keep. This module cannot
fix that alone: it does not own the transaction boundary, the HTTP layer
does. Every function here therefore takes an `audit_fn` (defaulting to
`audit`, so nothing changes for an existing caller) and
`openlaw.api.artifacts` passes one that writes each row on its own session
and commits it immediately. See `AuditFn` below.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from openlaw.config import Settings
from openlaw.db.conversations import (
    AccessDenied,
    Owner,
    find_owned_conversation,
    owner_clause,
    owner_columns,
)
from openlaw.db.models import Document, DocumentAccessLog, DocumentExcerptRow
from openlaw.documents.context import DocumentExcerpt
from openlaw.documents.crypto import decrypt, encrypt, load_key, load_keyring
from openlaw.documents.extract import chunk_pages, extract_pages, scan_for_crisis


def audit(session: Session, document_id: str, owner: Owner, action: str) -> None:
    """Append one append-only row to `document_access_log`.

    `actor_kind`/`actor_ref` name the same identity `owner_clause` checks
    against -- `"user"`/`owner.user_id` for a signed-in caller, `"anon"`/
    `owner.anon_token` otherwise -- so the audit trail answers "which
    identity" in exactly the terms the rest of this module enforces access
    by. `document_id` is written verbatim and is NOT required to resolve to
    an existing row: `DocumentAccessLog.document_id` is deliberately not a
    foreign key (Task 2), both so a deleted document's access history
    survives it and so a denied attempt against an id that never existed
    (or belongs to someone else) can still be recorded.
    """
    actor_kind = "user" if owner.user_id is not None else "anon"
    actor_ref = owner.user_id if owner.user_id is not None else owner.anon_token
    session.add(
        DocumentAccessLog(
            document_id=document_id,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            action=action,
        )
    )
    session.flush()


#: How a function in this module records one access. Injectable for exactly
#: one reason (Plan 6 Task 15 ruling): `audit` above shares its caller's
#: transaction, so a DENIED cross-owner access whose request then rolls back
#: left no row at all -- which defeats the point of auditing denials, since a
#: denial is precisely the request most likely to end in a rollback. The HTTP
#: layer owns the transaction boundary, so it is the layer that passes an
#: `audit_fn` writing on its own, independently committed session (see
#: `openlaw.api.artifacts.durable_audit`). The default is `audit` itself, so
#: every existing caller keeps the previous same-transaction behaviour and
#: this module gains no knowledge of sessions it does not already have.
AuditFn = Callable[[Session, str, Owner, str], None]


@dataclass(frozen=True)
class AttachedDocuments:
    """Everything one turn needs from the documents attached to it.

    Two values from a single owner-scoped pass, rather than two passes and
    two sets of audit rows: the decrypted `excerpts` the answer may quote,
    and each document's STORED upload-time `crisis_kinds` verdict
    (`openlaw.db.models.Document.crisis_kind`, positionally aligned with the
    `document_ids` requested). The second is the one `openlaw.language
    .pipeline.answer_in_conversation` needs on every turn, including turns
    that select no excerpt at all -- a protection order can contribute its
    verdict while sharing no token with the question.
    """

    excerpts: tuple[DocumentExcerpt, ...]
    crisis_kinds: tuple[str | None, ...]


def _anonymous_expiry(owner: Owner, ttl_hours: int) -> datetime | None:
    """Return the TTL-bounded expiry for an anonymous `owner`, else `None`."""
    if owner.user_id is not None:
        return None
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def _find_owned_document(
    session: Session, document_id: str, owner: Owner
) -> Document | None:
    """Return `document_id` if `owner` owns it, else `None`.

    The single filtered query `load_excerpts` and `delete_document` both
    build on -- see `openlaw.db.conversations`'s module docstring for why
    this must be one query, not a load followed by a Python-side check.
    """
    return session.execute(
        select(Document).where(Document.id == document_id, owner_clause(Document, owner))
    ).scalar_one_or_none()


def store_document(
    session: Session,
    owner: Owner,
    conversation_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    settings: Settings,
    audit_fn: AuditFn = audit,
) -> Document:
    """Extract, encrypt and store one uploaded document, with its excerpts.

    Raises `AccessDenied` if `owner` does not own `conversation_id` --
    checked before any extraction or encryption work runs, so an attacker
    cannot spend this function's cost (parsing, crisis scanning, AES-GCM)
    attaching a document to a conversation that is not theirs.
    `openlaw.documents.extract.extract_pages` raises `ExtractionError` for
    hostile or unreadable input (wrong type, too large, too many pages, no
    text, corrupt) -- propagated unchanged, since the caller (an API route)
    is what knows how to turn that into a user-facing refusal.

    `document.crisis_kind` is `openlaw.documents.extract.scan_for_crisis`'s
    verdict, computed once here and re-read on every later turn (see
    `openlaw.language.pipeline`'s module docstring) rather than re-scanned
    per question. Every write (`Document`, each `DocumentExcerptRow`) uses
    the `key_id` `encrypt` returns for that specific ciphertext -- never a
    shared or placeholder value -- and a `create` row is written to the
    access log on success.
    """
    conversation = find_owned_conversation(session, conversation_id, owner)
    if conversation is None:
        raise AccessDenied(
            f"conversation {conversation_id!r} is not accessible to this identity"
        )

    pages = extract_pages(data, content_type, settings)
    crisis_kind = scan_for_crisis(pages)
    document_id = str(uuid.uuid4())
    excerpts = chunk_pages(pages, document_id)

    key = load_key(settings)
    full_text = "\n\n".join(page.text for page in pages)
    ciphertext, nonce, key_id = encrypt(full_text, key, document_id)

    document = Document(
        id=document_id,
        **owner_columns(owner),
        conversation_id=conversation_id,
        filename=filename,
        content_type=content_type,
        byte_size=len(data),
        page_count=len(pages),
        ciphertext=ciphertext,
        nonce=nonce,
        key_id=key_id,
        crisis_kind=crisis_kind.value if crisis_kind is not None else None,
        expires_at=_anonymous_expiry(owner, settings.anonymous_upload_ttl_hours),
    )
    session.add(document)

    for ordinal, (excerpt_id, page_number, excerpt_text) in enumerate(excerpts):
        excerpt_cipher, excerpt_nonce, excerpt_key_id = encrypt(
            excerpt_text, key, excerpt_id
        )
        session.add(
            DocumentExcerptRow(
                document_id=document_id,
                excerpt_id=excerpt_id,
                page_number=page_number,
                ciphertext=excerpt_cipher,
                nonce=excerpt_nonce,
                key_id=excerpt_key_id,
                ordinal=ordinal,
            )
        )

    session.flush()
    audit_fn(session, document_id, owner, "create")
    return document


def load_attached_documents(
    session: Session,
    document_ids: tuple[str, ...],
    owner: Owner,
    settings: Settings,
    audit_fn: AuditFn = audit,
) -> AttachedDocuments:
    """Return the decrypted excerpts and stored crisis verdicts of
    `document_ids`.

    Raises `AccessDenied` on the FIRST `document_id` that does not resolve
    under `owner`'s scoped query -- never a partial result that silently
    drops someone's document from the set while returning the others. The
    denial itself is still audited (`action="denied"`) before the
    exception is raised. A document that DOES resolve is audited as
    `action="read"` before its excerpts are decrypted and returned.

    `crisis_kinds` is positionally aligned with `document_ids`, so a caller
    can hand it straight to `openlaw.language.pipeline
    .answer_in_conversation`/`answer_in_language` without re-querying. It is
    read from the stored column only: nothing here re-scans or re-classifies
    document text, which is what keeps a document's crisis verdict free on
    every turn after the upload that computed it.
    """
    if not document_ids:
        return AttachedDocuments(excerpts=(), crisis_kinds=())

    keyring = load_keyring(settings)
    excerpts: list[DocumentExcerpt] = []
    crisis_kinds: list[str | None] = []
    for document_id in document_ids:
        document = _find_owned_document(session, document_id, owner)
        if document is None:
            audit_fn(session, document_id, owner, "denied")
            raise AccessDenied(
                f"document {document_id!r} is not accessible to this identity"
            )
        audit_fn(session, document_id, owner, "read")
        crisis_kinds.append(document.crisis_kind)

        rows = (
            session.execute(
                select(DocumentExcerptRow)
                .where(DocumentExcerptRow.document_id == document_id)
                .order_by(DocumentExcerptRow.ordinal)
            )
            .scalars()
            .all()
        )
        for row in rows:
            text = decrypt(row.ciphertext, row.nonce, keyring, row.excerpt_id, row.key_id)
            excerpts.append(
                DocumentExcerpt(
                    excerpt_id=row.excerpt_id,
                    document_id=document_id,
                    filename=document.filename,
                    page_number=row.page_number,
                    text=text,
                )
            )
    return AttachedDocuments(
        excerpts=tuple(excerpts), crisis_kinds=tuple(crisis_kinds)
    )


def load_excerpts(
    session: Session,
    document_ids: tuple[str, ...],
    owner: Owner,
    settings: Settings,
    audit_fn: AuditFn = audit,
) -> tuple[DocumentExcerpt, ...]:
    """Return every excerpt of every document in `document_ids`, decrypted.

    The excerpts-only view of `load_attached_documents`, kept because that is
    all most callers want -- and implemented BY it rather than beside it, so
    the ownership check, the audit rows and the decryption have exactly one
    implementation that cannot drift.
    """
    return load_attached_documents(
        session, document_ids, owner, settings, audit_fn
    ).excerpts


def delete_document(
    session: Session,
    document_id: str,
    owner: Owner,
    audit_fn: AuditFn = audit,
) -> bool:
    """Delete `document_id` if `owner` owns it. Returns whether a row was
    deleted.

    Same idempotent-delete shape as `openlaw.db.conversations.
    delete_conversation`: `False` for both "no such document" and "owned by
    someone else", audited as `action="denied"` either way; `True` for an
    actual delete, audited as `action="delete"` first. Cascades to the
    document's excerpts via the ORM relationship on `openlaw.db.models
    .Document`; the access-log rows above are untouched, by design -- see
    the module docstring.
    """
    document = _find_owned_document(session, document_id, owner)
    if document is None:
        audit_fn(session, document_id, owner, "denied")
        return False
    audit_fn(session, document_id, owner, "delete")
    session.delete(document)
    session.flush()
    return True
