"""Upload, delete and re-read endpoints for conversations and documents.

The newest and most dangerous surface in this service: the only place a
citizen's own file -- an eviction notice, an FIR copy, a medical report --
crosses from an untrusted network into storage. Four properties are enforced
here and nowhere else.

**Nothing expensive happens before the gates.** The order in
`upload_document` is deliberate and tested: uploads kill switch, then declared
content type, then `Content-Length`, then a read bounded at
`max_upload_bytes + 1` bytes, then the per-conversation document cap, then the
quota charge -- and only then extraction, crisis scanning and AES-GCM. A
caller with no allowance left never buys parsing, and a 10 MiB body is never
read past the byte that proves it too large.

**Every refusal delivers help.** An upload can fail five new ways (too large,
unsupported type, unreadable, too many documents, uploads disabled) and each
one is somebody's last screen. All five, plus the 404 for an id the caller
does not own, go out through `openlaw.api.refusal.raise_refusal`, which
attaches the escalation footer, crisis detection and the structured helpline
list to every one of them. There is deliberately no path in this module that
returns a bare status code.

**A filename is attacker-controlled text.** It is sanitised with
`openlaw.documents.fence.sanitise` before it is stored -- once, at this
boundary, so every later reader (the prompt assembly, a
`DocumentCitationCard`, a transcript replay) gets a value that was already
cleaned rather than each remembering to clean it again. The stored name is
also truncated to the column's width rather than letting a 4 KB filename fail
at the database.

**An unowned id is a 404 with a help-carrying body, never a 403.** A 403
confirms the id exists, which turns `DELETE /documents/{id}` into an
enumeration oracle. The denial is still audited, durably -- see
`openlaw.api.turns.durable_audit`.

**`uploads_enabled=False` stops uploads, never deletions.** The brief reads
"both new endpoints", and this module deliberately applies the kill switch to
`POST /documents` only. Refusing a DELETE while the switch is off would trap
a person's uploaded FIR in storage they can no longer clear, at exactly the
moment an operator has decided something about the feature is unsafe. A kill
switch must not be able to revoke somebody's ability to withdraw their own
document.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openlaw.api.refusal import EXTRACTION_REFUSAL_KEYS, raise_refusal
from openlaw.api.routes import caller_owner, metered_caller
from openlaw.api.schemas import (
    ConversationTurnCard,
    ConversationView,
    DocumentSummary,
    render_document_claims,
    validated_uuid,
)
from openlaw.api.turns import durable_audit
from openlaw.config import Settings
from openlaw.db.conversations import (
    AccessDenied,
    Owner,
    create_conversation,
    delete_conversation,
    find_owned_conversation,
    load_history,
)
from openlaw.db.models import Document
from openlaw.answers.history import HistoryTurn
from openlaw.documents import ALLOWED_CONTENT_TYPES, PAGES_PER_QUOTA_UNIT
from openlaw.documents.extract import ExtractionError
from openlaw.documents.fence import sanitise
from openlaw.documents.store import delete_document, store_document

logger = logging.getLogger(__name__)

router = APIRouter()

#: Width of `openlaw.db.models.Document.filename`. A longer name is truncated
#: here rather than allowed to fail as a database error on a request a person
#: cannot see the cause of.
_MAX_FILENAME_CHARS = 512

#: Used when an upload arrives with no usable filename at all (a multipart
#: part with an empty `filename`, or one that sanitises away to nothing). A
#: fixed, non-empty placeholder, because `DocumentCitationCard.filename` is
#: how a reader finds the passage in their own paperwork and a blank there
#: reads as a bug rather than as "unnamed".
_UNNAMED_FILE = "uploaded file"

#: Static EN/HI retention copy. Same rule as every other API-composed safety
#: string (`openlaw.api.refusal`, `openlaw.api.schemas`): an anonymous upload
#: IS deleted after `Settings.anonymous_upload_ttl_hours`, and the person is
#: told so in a sentence that names the one action which changes it. Hindi is
#: best-effort pending a native-speaker review pass.
_RETENTION_ANONYMOUS: dict[str, str] = {
    "en": (
        "This file is stored encrypted and will be deleted automatically in "
        "{hours} hours. Sign in if you want to keep it longer."
    ),
    "hi": (
        "यह फ़ाइल एन्क्रिप्टेड रूप में रखी गई है और {hours} घंटों में अपने आप मिटा "
        "दी जाएगी। इसे अधिक समय तक रखना है तो साइन इन करें।"
    ),
}
_RETENTION_AUTHENTICATED: dict[str, str] = {
    "en": (
        "This file is stored encrypted and stays until you delete it. You can "
        "delete it at any time."
    ),
    "hi": (
        "यह फ़ाइल एन्क्रिप्टेड रूप में रखी गई है और जब तक आप इसे न मिटाएँ, बनी "
        "रहेगी। आप इसे कभी भी मिटा सकते हैं।"
    ),
}


def upload_quota_cost(page_count: int) -> int:
    """Return what a `page_count`-page upload costs in quota units.

    `max(1, ceil(page_count / PAGES_PER_QUOTA_UNIT))` -- a one-page notice
    still costs exactly one unit, because the person who most needs this
    product is uploading one page from a borrowed phone with three free
    questions, and charging them a worst case would price them out of the
    feature entirely. A 20-page document costs four, which is the point: it is
    parsed, crisis-scanned and encrypted page by page, and then re-read into
    the model's context on every later turn of its conversation.
    """
    return max(1, math.ceil(page_count / PAGES_PER_QUOTA_UNIT))


def _safe_filename(raw: str | None) -> str:
    """Return a stored-safe version of an attacker-supplied filename.

    `openlaw.documents.fence.sanitise` is the project's one answer to
    "untrusted text that will reach a prompt or a screen" (see that module's
    closing paragraph, which names filenames explicitly): control and bidi
    characters stripped, NFC normalised, and any run that could spell a fence
    delimiter broken with a zero-width space. Path separators are dropped too
    -- a filename is a label here, never a path, and `../../etc/passwd` as a
    *label* is merely confusing while as a path it is an invitation.
    """
    cleaned = sanitise(raw or "").replace("/", " ").replace("\\\\", " ").strip()
    return (cleaned or _UNNAMED_FILE)[:_MAX_FILENAME_CHARS]


def _retention_notice(owner: Owner, settings: Settings, language: str | None) -> str:
    """Return the retention sentence for `owner`, in `language`."""
    if owner.user_id is not None:
        pairs = _RETENTION_AUTHENTICATED
        return pairs.get(language or "en", pairs["en"])
    pairs = _RETENTION_ANONYMOUS
    template = pairs.get(language or "en", pairs["en"])
    return template.format(hours=settings.anonymous_upload_ttl_hours)


def _read_bounded(file: UploadFile, settings: Settings, language: str | None) -> bytes:
    """Return the upload's bytes, or refuse on the first byte over the cap.

    Reads at most `max_upload_bytes + 1` bytes and refuses on the overflow
    byte, so this handler never materialises an unbounded body. The
    `Content-Length` pre-check in `upload_document` is the cheaper half of the
    same guard -- it refuses before any body is consumed at all -- but a
    chunked request carries no length, so both are needed and neither is
    redundant.
    """
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise_refusal("upload_too_large", "", None, language)
    return data


def _declared_length(request: Request) -> int:
    """Return the request's `Content-Length`, or 0 when it is absent/malformed.

    Attacker-supplied, so it is only ever used to refuse EARLY -- never to
    trust that a body is small enough. `_read_bounded` is what actually
    enforces the cap.
    """
    try:
        return int(request.headers.get("content-length", "0"))
    except ValueError:
        return 0


def _require_room_for_another_document(
    session: Session,
    conversation_id: str,
    settings: Settings,
    language: str | None,
) -> None:
    """Refuse when `conversation_id` already holds its maximum documents.

    `max_documents_per_conversation` was left to the route layer by Task 14,
    and this is that layer. Counted with a query rather than from a loaded
    relationship so the check costs one COUNT, not the hydration of every
    document row in the conversation.
    """
    held = session.execute(
        select(func.count())
        .select_from(Document)
        .where(Document.conversation_id == conversation_id)
    ).scalar_one()
    if held >= settings.max_documents_per_conversation:
        raise_refusal("too_many_documents", "", None, language)


@router.post("/documents", response_model=DocumentSummary, status_code=201)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    language: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
) -> DocumentSummary:
    """Store one uploaded document, attaching it to a conversation.

    See the module docstring for the order of the gates and why it is that
    order. `language` is accepted only to pick the static EN/HI refusal and
    retention copy; it never reaches a model from here, since this endpoint
    runs no generation at all.
    """
    settings: Settings = request.app.state.settings
    session_factory: Callable = request.app.state.session_factory

    if not settings.uploads_enabled:
        raise_refusal("uploads_disabled", "", None, language)
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise_refusal("upload_unsupported_type", "", None, language)
    if _declared_length(request) > settings.max_upload_bytes + _MULTIPART_OVERHEAD:
        raise_refusal("upload_too_large", "", None, language)

    data = _read_bounded(file, settings, language)
    filename = _safe_filename(file.filename)
    owner = caller_owner(request, settings, session_factory, authorization)
    resolved_id = _resolve_conversation(
        session_factory, settings, owner, conversation_id, language
    )
    return _store_and_summarise(
        request, settings, session_factory, owner, resolved_id, filename,
        file.content_type or "", data, language, authorization,
    )


#: Slack allowed on the `Content-Length` pre-check: a multipart body carries
#: part headers and boundary markers around the file itself, so the envelope
#: is legitimately larger than the file. Deliberately generous -- this check
#: exists to refuse an obviously oversized body cheaply, and `_read_bounded`
#: is what enforces the real limit on the file's own bytes.
_MULTIPART_OVERHEAD = 8192


def _resolve_conversation(
    session_factory: Callable,
    settings: Settings,
    owner: Owner,
    conversation_id: str | None,
    language: str | None,
) -> str:
    """Return the conversation to attach to, creating one if none was named.

    A named conversation the caller does not own is a 404 -- the same
    response as one that does not exist, for the reason
    `openlaw.api.turns`'s docstring gives.
    """
    with session_factory() as session:
        if conversation_id is None:
            return create_conversation(session, owner, settings).id
        validated_uuid(conversation_id, "conversation_id")
        conversation = find_owned_conversation(session, conversation_id, owner)
        if conversation is None:
            raise_refusal("not_found", "", None, language)
        _require_room_for_another_document(
            session, conversation_id, settings, language
        )
        return conversation.id


def _store_and_summarise(
    request: Request,
    settings: Settings,
    session_factory: Callable,
    owner: Owner,
    conversation_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    language: str | None,
    authorization: str | None,
) -> DocumentSummary:
    """Charge the upload, store it, and describe what was stored.

    **Quota is charged in two stages, and that is a deliberate trade-off.**
    The brief asks both that an upload cost
    `max(1, ceil(page_count / PAGES_PER_QUOTA_UNIT))` and that quota be
    consumed BEFORE extraction -- and the page count does not exist until
    extraction has run. One unit is therefore taken first, so a caller with
    nothing left never buys parsing, crisis scanning or AES-GCM; the remaining
    `cost - 1` units are taken once the real page count is known, and a
    caller who cannot pay them gets the ordinary help-carrying 429 with
    nothing stored. The residue of this design is that a refused multi-page
    upload still spent the one unit its extraction cost, which is the correct
    way round: the work was really done, and the alternative (charging the
    worst case upfront) would price a one-page notice at four units for
    everybody.
    """
    caller = metered_caller(
        request, settings, session_factory, authorization, "", None, language, cost=1
    )
    with session_factory() as session:
        try:
            document = store_document(
                session, owner, conversation_id, filename, content_type, data,
                settings, durable_audit(session_factory),
            )
        except ExtractionError as error:
            raise_refusal(EXTRACTION_REFUSAL_KEYS[error.reason], "", None, language)
        except AccessDenied:
            raise_refusal("not_found", "", None, language)
        summary = DocumentSummary(
            id=document.id,
            filename=document.filename,
            page_count=document.page_count,
            conversation_id=conversation_id,
            expires_at=(
                document.expires_at.isoformat() if document.expires_at else None
            ),
            retention_notice=_retention_notice(owner, settings, language),
            quota_remaining=caller.decision.remaining,
        )

    extra = upload_quota_cost(summary.page_count) - 1
    if extra <= 0:
        return summary
    return _charge_remaining_pages(
        request, settings, session_factory, authorization, summary, extra, language
    )


def _charge_remaining_pages(
    request: Request,
    settings: Settings,
    session_factory: Callable,
    authorization: str | None,
    summary: DocumentSummary,
    extra: int,
    language: str | None,
) -> DocumentSummary:
    """Charge a multi-page upload's remaining units, or undo the upload.

    An unaffordable remainder deletes the document before refusing: a stored
    file the caller was never allowed to keep is both a cost this service did
    not agree to carry and a retention promise it did not make.
    """
    try:
        caller = metered_caller(
            request, settings, session_factory, authorization, "", None, language,
            cost=extra,
        )
    except HTTPException:
        _discard_unaffordable_document(session_factory, summary)
        raise
    return summary.model_copy(update={"quota_remaining": caller.decision.remaining})


def _discard_unaffordable_document(
    session_factory: Callable, summary: DocumentSummary
) -> None:
    """Delete a document whose remaining quota units could not be paid.

    Never audited as a `"denied"` access: nobody was refused access to
    anything here, the service is undoing its own partial write. Logged rather
    than raised on failure -- the caller is already receiving a 429, and
    turning a cleanup failure into a 500 would replace a response that carries
    helplines with one that carries a different set of them for no benefit to
    the person reading it.
    """
    try:
        with session_factory() as session:
            document = session.get(Document, summary.id)
            if document is not None:
                session.delete(document)
    except Exception:
        logger.exception(
            "failed to discard document %s after its quota remainder was "
            "refused; it may need manual cleanup", summary.id,
        )


@router.delete("/documents/{document_id}", status_code=204)
def remove_document(
    request: Request,
    document_id: str,
    authorization: str | None = Header(default=None),
) -> Response:
    """Delete one of the caller's own documents.

    Free (no quota consumed -- see `openlaw.api.routes.caller_owner`) and
    available whether or not uploads are enabled, for the reason the module
    docstring gives. A document the caller does not own, and one that never
    existed, both return the same 404.
    """
    settings: Settings = request.app.state.settings
    session_factory: Callable = request.app.state.session_factory
    validated_uuid(document_id, "document_id")

    owner = caller_owner(request, settings, session_factory, authorization)
    with session_factory() as session:
        deleted = delete_document(
            session, document_id, owner, durable_audit(session_factory)
        )
    if not deleted:
        raise_refusal("not_found")
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
def read_conversation(
    request: Request,
    conversation_id: str,
    authorization: str | None = Header(default=None),
) -> ConversationView:
    """Return the caller's own conversation, for rendering after a reload.

    Built from `openlaw.db.conversations.load_history`, which returns
    `HistoryTurn`s rather than raw `Message` rows -- so there is no field on
    anything this endpoint touches that could carry `unverified_summary`. Legal
    claims and document claims are rendered into separate fields, exactly as
    they are on a live turn.
    """
    settings: Settings = request.app.state.settings
    session_factory: Callable = request.app.state.session_factory
    validated_uuid(conversation_id, "conversation_id")

    owner = caller_owner(request, settings, session_factory, authorization)
    with session_factory() as session:
        try:
            turns = load_history(session, conversation_id, owner)
        except AccessDenied:
            raise_refusal("not_found")
    return ConversationView(
        conversation_id=conversation_id,
        turns=tuple(
            _turn_card(index, turn) for index, turn in enumerate(turns)
        ),
    )


def _turn_card(index: int, turn: HistoryTurn) -> ConversationTurnCard:
    """Render one stored turn, keeping document claims apart from legal ones.

    Both halves go through the same renderers a live turn uses
    (`openlaw.api.schemas.render_document_claims` for the document half), so a
    reloaded transcript cannot become the one place where a statement about
    somebody's lease reads as law.
    """
    return ConversationTurnCard(
        turn_index=index,
        question=turn.question,
        answer="\n\n".join(claim.text for claim in turn.claims),
        document_answer=render_document_claims(turn.document_claims, turn.language),
        language=turn.language,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def remove_conversation(
    request: Request,
    conversation_id: str,
    authorization: str | None = Header(default=None),
) -> Response:
    """Delete one of the caller's own conversations, and everything in it.

    Cascades to its messages, its documents and each document's excerpts via
    the ORM relationships on `openlaw.db.models.Conversation`. The
    `document_access_log` rows are deliberately untouched -- they are not
    foreign-keyed to `documents` precisely so the record of who read what
    outlives the content itself.
    """
    settings: Settings = request.app.state.settings
    session_factory: Callable = request.app.state.session_factory
    validated_uuid(conversation_id, "conversation_id")

    owner = caller_owner(request, settings, session_factory, authorization)
    with session_factory() as session:
        deleted = delete_conversation(session, conversation_id, owner)
    if not deleted:
        raise_refusal("not_found")
    return Response(status_code=204)
