"""Conversation persistence, scoped to the owning identity (Plan 6 Task 14).

**Ownership binds to the token subject, never the email.**
`openlaw.api.auth.upsert_user` was hardened precisely because an earlier
version keyed users by their email claim, which let one person read
another's conversations by changing an email or receiving a recycled
mailbox (see that function's own docstring). `Owner.user_id` carries the
same Supabase subject `upsert_user` writes to `User.id` -- never the email
-- and `Owner.anon_token` carries the opaque per-client cookie token minted
for a conversation started without signing in (`openlaw.api.app`'s
`ANON_COOKIE_NAME`). An `Owner` can never be built with neither set
(`__post_init__` raises): `ck_conversations_has_owner` already forbids an
ownerless *row*, and failing at construction time, in Python, catches the
same mistake before it ever reaches SQL.

**The ownership check is a query clause, not a caller's remembered filter.**
`owner_clause` builds the one WHERE condition every function below adds to
its lookup: a `user_id`-owner matches only rows with that exact `user_id`;
an `anon_token`-owner matches only rows with that exact `anon_token` AND a
NULL `user_id` -- an anonymous token can never reach a row a real sign-in
later claimed. Every read and write in this module threads its
`conversation_id` lookup through this single clause (`find_owned_conversation`),
so there is no code path where a caller must remember to add the filter
separately: the row simply does not exist, from this query's point of view,
for anyone but its owner. Swap the ids in two requests and each caller's
query still only ever matches their own rows -- there is no shared
intermediate "load, then check" step for a bug to hide in.

A lookup that matches nothing raises `AccessDenied` rather than returning
`None` silently: `load_history` and `append_turn` need to say "you may not
read or write this", not quietly behave as if the conversation were empty.
`delete_conversation` is the one exception, by design -- see its own
docstring -- because delete is already idempotent by its return type.

**Anonymous rows expire; authenticated rows do not.** `Settings.
anonymous_conversation_ttl_hours` (Task 1) exists because a cookie-keyed
identity cannot reliably delete anything later, and an undeletable
conversation transcript is not acceptable. `create_conversation` sets
`expires_at` to `now + anonymous_conversation_ttl_hours` for an anonymous
owner and leaves it `NULL` for an authenticated one, matching `Conversation
.expires_at`'s own docstring (Task 2) and giving `scripts/purge_retention.py`
(Task 16) exactly what it needs to find expired rows cheaply via the
existing index.

**No safety event is persisted here.** `openlaw.answers.generate.
persist_answer_events` is called once, at the endpoint, for the
hallucination and classifier-failure logs a turn produces -- a second write
path in this module would duplicate or drop those events (Plan 5 had to fix
exactly this once). `append_turn` writes only the conversation transcript:
the question, and the claims/document_claims that survived validation.
`unverified_summary` is never read from `localised.answer` anywhere in this
module -- there is no column it could go in that would not render it to a
future turn as if it had been checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openlaw.answers.history import HistoryTurn
from openlaw.answers.schema import Claim, DocumentClaim
from openlaw.config import Settings
from openlaw.db.models import Conversation, Message

if TYPE_CHECKING:
    from openlaw.language.pipeline import LocalisedAnswer


@dataclass(frozen=True)
class Owner:
    """The identity a row belongs to: a signed-in subject, or an anonymous
    cookie token -- never both expressed at once in a database write (see
    `owner_columns`), and never neither (see `__post_init__`).

    `user_id` is the Supabase token subject (`openlaw.api.auth.
    AuthenticatedUser.user_id`), NEVER the email -- there is deliberately no
    email field on this type, so a caller cannot even accidentally construct
    an email-keyed `Owner`.
    """

    user_id: str | None
    anon_token: str | None

    def __post_init__(self) -> None:
        if self.user_id is None and self.anon_token is None:
            raise ValueError("Owner requires a user_id or an anon_token")


class AccessDenied(Exception):
    """Raised when `owner` does not match the row a function was asked for.

    Deliberately indistinguishable from "this id does not exist at all" --
    both come back as the same single query returning nothing (see
    `owner_clause`), and leaking which of the two is true would hand a
    probing caller a way to enumerate real conversation/document ids.
    """


def owner_clause(model: Any, owner: Owner) -> Any:
    """Return the SQLAlchemy WHERE clause that scopes `model` rows to `owner`.

    The one piece of logic every ownership check in this module (and in
    `openlaw.documents.store`) is built from, so the rule stated in the
    module docstring cannot drift between a conversation lookup and a
    document lookup: a `user_id` owner matches only that exact `user_id`;
    an `anon_token` owner matches only that exact `anon_token` on a row
    whose `user_id` is NULL, so a token can never reach a row a sign-in has
    since claimed.
    """
    if owner.user_id is not None:
        return model.user_id == owner.user_id
    return (model.user_id.is_(None)) & (model.anon_token == owner.anon_token)


def owner_columns(owner: Owner) -> dict[str, str | None]:
    """Return the `user_id`/`anon_token` values to WRITE for `owner`.

    Enforces "exactly one of the two is ever set on a row" at write time
    regardless of what `owner` itself carries (a signed-in caller's request
    may still carry a stale anonymous cookie) -- see `Conversation`'s and
    `Document`'s docstrings for why a signed-in row must never also carry an
    `anon_token`.
    """
    if owner.user_id is not None:
        return {"user_id": owner.user_id, "anon_token": None}
    return {"user_id": None, "anon_token": owner.anon_token}


def _anonymous_expiry(owner: Owner, ttl_hours: int) -> datetime | None:
    """Return the TTL-bounded expiry for an anonymous `owner`, else `None`.

    `None` for an authenticated owner: per the plan, authenticated history
    persists until the person deletes it themselves (see `Conversation.
    expires_at`'s docstring).
    """
    if owner.user_id is not None:
        return None
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def find_owned_conversation(
    session: Session, conversation_id: str, owner: Owner
) -> Conversation | None:
    """Return `conversation_id` if `owner` owns it, else `None`.

    The single filtered query every function below (and `openlaw.documents
    .store`, for the conversation a document is attached to) builds on --
    see the module docstring's "query clause, not a caller's remembered
    filter" paragraph.
    """
    return session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, owner_clause(Conversation, owner)
        )
    ).scalar_one_or_none()


def _require_owned_conversation(
    session: Session, conversation_id: str, owner: Owner
) -> Conversation:
    """Return the owned conversation, or raise `AccessDenied`."""
    conversation = find_owned_conversation(session, conversation_id, owner)
    if conversation is None:
        raise AccessDenied(
            f"conversation {conversation_id!r} is not accessible to this identity"
        )
    return conversation


def create_conversation(
    session: Session, owner: Owner, settings: Settings
) -> Conversation:
    """Create and return a new conversation owned by `owner`.

    No ownership check is needed here -- a caller always creates a
    conversation for themselves, never for someone else's id.
    """
    conversation = Conversation(
        **owner_columns(owner),
        expires_at=_anonymous_expiry(owner, settings.anonymous_conversation_ttl_hours),
    )
    session.add(conversation)
    session.flush()
    return conversation


def _turns_from_messages(messages: list[Message]) -> tuple[HistoryTurn, ...]:
    """Group `messages` into `HistoryTurn`s by `turn_index`.

    A turn missing either its user or its assistant half (should not happen
    under normal operation, since `append_turn` always writes both
    together) is skipped defensively rather than raising: a reader should
    never be unable to see an otherwise-complete conversation because one
    turn was interrupted mid-write.
    """
    by_turn: dict[int, dict[str, Message]] = {}
    for message in messages:
        by_turn.setdefault(message.turn_index, {})[message.role] = message

    turns: list[HistoryTurn] = []
    for index in sorted(by_turn):
        pair = by_turn[index]
        user_message = pair.get("user")
        assistant_message = pair.get("assistant")
        if user_message is None or assistant_message is None:
            continue
        turns.append(
            HistoryTurn(
                question=user_message.content,
                claims=tuple(Claim(**claim) for claim in assistant_message.claims),
                document_claims=tuple(
                    DocumentClaim(**claim)
                    for claim in assistant_message.document_claims
                ),
                language=assistant_message.language,
            )
        )
    return tuple(turns)


def load_history(
    session: Session, conversation_id: str, owner: Owner
) -> tuple[HistoryTurn, ...]:
    """Return `conversation_id`'s turns as `HistoryTurn`s, oldest first.

    Raises `AccessDenied` when `owner` does not own `conversation_id` --
    enforced by `_require_owned_conversation`'s single filtered query, never
    by loading the row first and checking its owner in Python afterwards.
    Returns `HistoryTurn`s built from the stored `claims`/`document_claims`
    JSON, never raw `Message` rows: a caller of this function can reach
    `unverified_summary` through nothing it returns, because nothing it
    returns has that field at all.
    """
    _require_owned_conversation(session, conversation_id, owner)
    messages = (
        session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.turn_index, Message.created_at)
        )
        .scalars()
        .all()
    )
    return _turns_from_messages(list(messages))


def _assistant_content(answer: Any) -> str:
    """Render the stored text of an assistant turn from VERIFIED claims only.

    Built from `answer.claims`/`answer.document_claims` text -- the same
    claims stored structurally in `Message.claims`/`.document_claims` --
    and NEVER from `answer.unverified_summary`, which is unchecked model
    prose (see `openlaw.answers.validate.ValidatedAnswer`'s docstring). A
    turn with no surviving claim stores an empty string, not a placeholder
    invented here, since there is deliberately no field on `Message` for a
    rendered "nothing was citable" marker -- a future turn's history view
    can draw that distinction from `claims == []` directly.
    """
    lines = [claim.text for claim in answer.claims]
    lines += [claim.text for claim in answer.document_claims]
    return "\n".join(lines)


def append_turn(
    session: Session,
    conversation_id: str,
    owner: Owner,
    question: str,
    localised: "LocalisedAnswer",
) -> None:
    """Persist one completed turn: the person's question and the verified
    answer, as two `Message` rows sharing `localised.turn_index`.

    Raises `AccessDenied` when `owner` does not own `conversation_id`,
    checked before anything is written. `claims`/`document_claims` are
    stored as structured JSON (`Claim.model_dump()`/`DocumentClaim
    .model_dump()`); `localised.answer.unverified_summary` is never read
    anywhere in this function, so a poisoned or hallucinated summary has no
    column to land in. Does not call `openlaw.answers.generate.
    persist_answer_events` or write any hallucination/classifier-failure
    event -- see the module docstring for why that stays a single write
    path at the endpoint.
    """
    conversation = _require_owned_conversation(session, conversation_id, owner)

    session.add(
        Message(
            conversation_id=conversation_id,
            role="user",
            content=question,
            turn_index=localised.turn_index,
            language=localised.language,
        )
    )
    session.add(
        Message(
            conversation_id=conversation_id,
            role="assistant",
            content=_assistant_content(localised.answer),
            claims=[claim.model_dump() for claim in localised.answer.claims],
            document_claims=[
                claim.model_dump() for claim in localised.answer.document_claims
            ],
            turn_index=localised.turn_index,
            language=localised.language,
        )
    )
    conversation.updated_at = datetime.now(UTC)
    session.flush()


def delete_conversation(session: Session, conversation_id: str, owner: Owner) -> bool:
    """Delete `conversation_id` if `owner` owns it. Returns whether a row
    was deleted.

    Returns `False`, rather than raising, for both "no such conversation"
    and "owned by someone else" -- a delete is naturally idempotent (the
    end state the caller wants, "this id is gone", already holds in both
    cases), and this function's `bool` return is exactly that signal. The
    lookup is still the same single owner-scoped query every other function
    here uses, so a mismatched owner never reaches the `session.delete`
    call regardless. Cascades to the conversation's `Message` and
    `Document` rows (and each document's excerpts in turn) via the ORM
    relationships declared on `openlaw.db.models.Conversation`.
    """
    conversation = find_owned_conversation(session, conversation_id, owner)
    if conversation is None:
        return False
    session.delete(conversation)
    session.flush()
    return True
