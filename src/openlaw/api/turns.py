"""Loading one conversation turn: ownership, documents, and the audit trail.

Separated from `openlaw.api.routes` for two reasons. It is the piece BOTH the
`/ask` endpoint and the document endpoints (`openlaw.api.artifacts`) need, and
it depends on none of the quota or rate-limit machinery in `routes` -- so
putting it here is what lets both routers share it without an import cycle.

**This module is where the transaction boundary lives, and that is the whole
point.** `openlaw.documents.store.audit` writes on the session it is handed,
so a `document_access_log` row only survives if the caller's transaction
commits -- and a denied cross-owner access is precisely the request that ends
in a rollback. `durable_audit` below is what makes a denial outlive the
request that was refused. See its docstring for the one action deliberately
left on the request's own transaction, and why.

**A conversation or document that belongs to somebody else is a 404, never a
403.** `openlaw.db.conversations.AccessDenied` is deliberately
indistinguishable from "no such id" (see that class's docstring), and this
module keeps it that way on the wire: a 403 would confirm the id is real and
hand a probing caller an enumeration oracle. The 404 still carries the
escalation footer and the full helpline list, like every other terminal
response this API can produce.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openlaw.answers.history import HistoryTurn
from openlaw.api.refusal import raise_refusal
from openlaw.config import Settings
from openlaw.db.conversations import (
    AccessDenied,
    Owner,
    append_turn,
    create_conversation,
    load_history,
)
from openlaw.documents.context import DocumentExcerpt
from openlaw.documents.store import (
    AuditFn,
    audit,
    load_attached_documents,
    load_document_crisis_kinds,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle / load-cost guard only
    from openlaw.language.pipeline import LocalisedAnswer

logger = logging.getLogger(__name__)


def durable_audit(session_factory: Callable) -> AuditFn:
    """Return an `AuditFn` that commits each row on its OWN session.

    **The ruling this closes.** `openlaw.documents.store.audit` writes on the
    session it is handed, so a `document_access_log` row lives and dies with
    that session's transaction -- and the request most likely to end in a
    rollback is exactly the denied cross-owner access an audit trail exists to
    record. Routing every audit write through a short, separately committed
    session makes the record independent of the request's outcome: the HTTP
    layer owns the transaction boundary, so this is the layer that can.

    **`"create"` is the one deliberate exception.** A `create` row is written
    after the `Document` and its excerpt rows have been flushed, so it is the
    one action whose audit write happens while the request's own transaction
    already holds write locks -- a second connection inserting then is a
    self-inflicted lock conflict on any engine that takes database-level write
    locks. It is also the one action whose subject does not outlive a
    rollback: if the upload transaction is discarded, the document never
    existed, and a "created" record for it would be a false one. Every other
    action (`read`, `denied`, `delete`) is decided from a read-only query
    before any write, and is true whether or not the request goes on to
    succeed.
    """

    def audit_fn(session, document_id: str, owner: Owner, action: str) -> None:
        if action in _TRANSACTIONAL_AUDIT_ACTIONS:
            audit(session, document_id, owner, action)
            return
        with session_factory() as audit_session:
            audit(audit_session, document_id, owner, action)

    return audit_fn


#: Audit actions written on the request's own transaction rather than a
#: separate committed one -- see `durable_audit`.
_TRANSACTIONAL_AUDIT_ACTIONS = frozenset({"create"})


@dataclass(frozen=True)
class TurnInputs:
    """Everything one conversation turn needs, loaded before generation.

    `document_excerpts` is EVERY decrypted excerpt of the attached documents,
    unselected. Selection is `openlaw.language.pipeline
    .answer_in_conversation`'s, because only that function holds the
    TRANSLATED query -- see its docstring and the final whole-plan review's
    HIGH-1 for the Hindi-question/English-document case this layer silently
    broke by selecting here, on the raw question.
    """

    conversation_id: str
    history: tuple[HistoryTurn, ...]
    document_excerpts: tuple[DocumentExcerpt, ...]
    document_crisis_kinds: tuple[str | None, ...]


@dataclass(frozen=True)
class TurnDocuments:
    """Which documents a turn may use, and whether it may read their TEXT.

    Two different permissions, because the uploads kill switch revokes one of
    them and not the other (final whole-plan review, LOW-2). `ids` is always
    the caller's own list, so a document's STORED crisis verdict is still
    read; `text_allowed` is what `Settings.uploads_enabled` turns off.
    """

    ids: tuple[str, ...]
    text_allowed: bool


def turn_documents(
    document_ids: tuple[str, ...], settings: Settings,
    question: str, state: str | None, language: str | None,
) -> TurnDocuments:
    """Return what this turn may do with `document_ids`, or raise a refusal.

    Two route-layer gates Task 14 deliberately left here.
    `max_documents_per_conversation` IS refused, with help attached -- a
    caller asking about four documents when three is the limit needs to be
    told, not quietly answered about three of them. It is now checked FIRST,
    whatever the kill switch says, because the ids are no longer discarded
    when uploads are off and an unbounded list would otherwise buy unbounded
    ownership lookups.

    `uploads_enabled` is a kill switch, and what it revokes is narrower than
    it was. Document TEXT is not read with uploads off -- no excerpt is
    decrypted, no document pass runs -- but the ids are still resolved so the
    STORED crisis verdicts (`openlaw.documents.store
    .load_document_crisis_kinds`) keep reaching the answer. Before this fix
    the ids were discarded outright, which silently withdrew the helpline card
    attached to an already-uploaded protection order from every subsequent
    turn: an operator disabling a feature was also, invisibly, disabling a
    safety signal. Nothing about the switch's cost or injection properties
    changes -- verdicts are a column read, computed once at upload.
    """
    if len(document_ids) > settings.max_documents_per_conversation:
        raise_refusal("too_many_documents", question, state, language)
    return TurnDocuments(ids=document_ids, text_allowed=settings.uploads_enabled)


def load_turn_inputs(
    session_factory: Callable,
    settings: Settings,
    owner: Owner,
    conversation_id: str | None,
    documents: TurnDocuments,
    question: str,
    state: str | None,
    language: str | None,
) -> TurnInputs:
    """Resolve the conversation, its history and its attached documents.

    Every lookup is owner-scoped by the data layer's own query clause (see
    `openlaw.db.conversations`'s module docstring), so a conversation or
    document id belonging to anybody else simply does not exist from here --
    and `AccessDenied` becomes a 404, never a 403, because a 403 confirms the
    id is real. The denial is still audited, durably: `durable_audit` writes
    it on its own committed session, so the rollback this raise causes cannot
    erase the record of the attempt.

    `document_crisis_kinds` is read on EVERY turn, not only the turn that
    uploaded, which is the whole point of storing the verdict at upload time
    (`openlaw.documents.extract.scan_for_crisis`): a protection order attached
    three questions ago still attaches its helplines to this answer -- and,
    since LOW-2, even on a turn where `Settings.uploads_enabled` is off and no
    excerpt is decrypted at all (see `TurnDocuments`).

    Excerpts come back UNSELECTED. Ranking them against the question is
    `answer_in_conversation`'s job, because the query that must do the ranking
    is the translated one and this layer does not have it (HIGH-1).
    """
    with session_factory() as session:
        # Documents first, then history, and the conversation INSERT last.
        # Ordering, not taste: `durable_audit` writes a denial on its own
        # connection, and doing that while this session already holds write
        # locks from a freshly created conversation is a self-inflicted lock
        # conflict on any engine that locks at database level. Reads cannot
        # hold those locks, so ordering every read ahead of the one write
        # removes the conflict rather than working around it -- and a request
        # that is about to be refused no longer mints a conversation it will
        # immediately roll back.
        try:
            excerpts, crisis_kinds = _load_documents(
                session, documents, owner, settings, durable_audit(session_factory)
            )
        except AccessDenied:
            raise_refusal("not_found", question, state, language)

        if conversation_id is None:
            history: tuple[HistoryTurn, ...] = ()
            conversation_id = create_conversation(session, owner, settings).id
        else:
            try:
                history = load_history(session, conversation_id, owner)
            except AccessDenied:
                raise_refusal("not_found", question, state, language)
        return TurnInputs(
            conversation_id=conversation_id,
            history=history,
            document_excerpts=excerpts,
            document_crisis_kinds=crisis_kinds,
        )


def _load_documents(
    session,
    documents: TurnDocuments,
    owner: Owner,
    settings: Settings,
    audit_fn: AuditFn,
) -> tuple[tuple[DocumentExcerpt, ...], tuple[str | None, ...]]:
    """Return this turn's excerpts and stored crisis verdicts.

    With `TurnDocuments.text_allowed` False the excerpts are empty and only
    the verdicts are read -- the kill switch withdraws the capability and not
    the safety signal (LOW-2). Both branches run the same owner-scoped lookup
    and write the same audit rows (`openlaw.documents.store
    ._resolve_owned_documents`), so a denial is a 404 and a durable `denied`
    row either way.
    """
    if not documents.text_allowed:
        return (), load_document_crisis_kinds(
            session, documents.ids, owner, audit_fn
        )
    attached = load_attached_documents(
        session, documents.ids, owner, settings, audit_fn
    )
    return attached.excerpts, attached.crisis_kinds


def persist_turn(
    session_factory: Callable,
    owner: Owner,
    conversation_id: str,
    question: str,
    localised: "LocalisedAnswer",
) -> None:
    """Append this completed turn to its conversation.

    A failure here must not cost the caller the answer they already paid for:
    the turn is logged and the response still goes out. The cost of the
    alternative is a 500 on a successfully generated answer, which is strictly
    worse for the person reading it than a conversation that forgets one turn.
    """
    try:
        with session_factory() as session:
            append_turn(session, conversation_id, owner, question, localised)
    except Exception:
        logger.exception(
            "failed to persist turn %s of conversation %s; the answer was still "
            "delivered", getattr(localised, "turn_index", "?"), conversation_id,
        )
