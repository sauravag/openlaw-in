"""`GET /conversations`: the signed-in caller's own conversations (Mode B only).

Why it exists (final review H1): the page remembers the current conversation
id in `sessionStorage`, so a new device, a closed tab or a re-sign-in has no
way back to a conversation that is "kept until you delete it". Without a list,
that promise holds in Postgres while its owner cannot reach it.

What it returns is deliberately thin: id, created/updated time and a document
count. No question, answer or filename, so the list is safe to render before
anything is opened, and it adds no new way to read content. The one exception
is opt-in: with `include_titles=true` each item also carries a short title cut
from the conversation's first question, for the sidebar. Without it the list
stays metadata-only.

Mode B only: registered by `create_app` beside the account routes, so it is a
router-level 404 in Mode A, where there are no accounts to list. An anonymous
caller (no bearer, or one that fails to verify) gets the help-carrying 401
sign-in refusal; there is no anonymous fall-back, because "my conversations"
has no meaning without an account and a cookie-keyed list would let anyone on
a shared device enumerate the previous visitor's threads.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from openlaw.api.auth import AuthError, decode_token, extract_bearer
from openlaw.api.refusal import raise_refusal
from openlaw.db.models import Conversation, Document, Message

logger = logging.getLogger(__name__)

router = APIRouter()

#: Default page size. Small: the list is rendered on a cheap phone.
CONVERSATION_LIST_LIMIT = 20
#: The largest page a caller may ask for; above it the request is refused.
CONVERSATION_LIST_MAX_LIMIT = 50

#: A title is at most this many code points, ellipsis included.
TITLE_MAX_CHARS = 60

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


class ConversationListItem(BaseModel):
    """One conversation, metadata only."""

    id: str
    created_at: str
    updated_at: str
    document_count: int
    #: Set only when the caller passed `include_titles=true` (None: no question
    #: yet, e.g. an upload-only conversation). Left unset, it is not serialised.
    title: str | None = None


class ConversationList(BaseModel):
    """One page of the caller's conversations, newest first.

    `next_offset` is the `offset` for the following page, or None when this
    page is the last.
    """

    conversations: tuple[ConversationListItem, ...]
    next_offset: int | None


def _signed_in_user_id(request: Request, authorization: str | None, language: str | None) -> str:
    """The verified token subject, or raise the 401 sign-in refusal."""
    settings = request.app.state.settings
    token = extract_bearer(authorization)
    try:
        if token is None:
            raise AuthError("no bearer token")
        return decode_token(token, settings.supabase_jwt_secret).user_id
    except AuthError as error:
        logger.warning("conversation list refused: %s", error.detail)
        raise_refusal(
            "conversations_sign_in_required", language=language, headers=_BEARER_CHALLENGE
        )


def shorten_title(question: str) -> str:
    """Collapse whitespace and cut to `TITLE_MAX_CHARS`, at a word where possible."""
    text = " ".join(question.split())
    if len(text) <= TITLE_MAX_CHARS:
        return text
    cut = text[: TITLE_MAX_CHARS - 1]
    # Mid-word cut: back up to the last space, if there is one to back up to.
    if text[TITLE_MAX_CHARS - 1] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return f"{cut.strip()}…"


def _first_questions(session_factory: Callable, conversation_ids: list[str]) -> dict[str, str]:
    """Each conversation's first user question, fetched in one query."""
    if not conversation_ids:
        return {}
    first_turn = (
        select(Message.conversation_id, func.min(Message.turn_index).label("turn_index"))
        .where(Message.role == "user", Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
        .subquery()
    )
    query = select(Message.conversation_id, Message.content).join(
        first_turn,
        (Message.conversation_id == first_turn.c.conversation_id)
        & (Message.turn_index == first_turn.c.turn_index)
        & (Message.role == "user"),
    )
    with session_factory() as session:
        return {row[0]: row[1] for row in session.execute(query).all()}


def _page(session_factory: Callable, user_id: str, limit: int, offset: int) -> list[tuple]:
    """`limit + 1` rows from `offset`, so the caller can tell if more follow."""
    document_count = (
        select(func.count(Document.id))
        .where(Document.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    query = (
        select(Conversation.id, Conversation.created_at, Conversation.updated_at, document_count)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc(), Conversation.id)
        .limit(limit + 1)
        .offset(offset)
    )
    with session_factory() as session:
        return list(session.execute(query).all())


def _item(row: tuple, titles: dict[str, str] | None) -> ConversationListItem:
    """One list item; `title` is set (possibly None) only when titles were asked for."""
    fields = {
        "id": row[0],
        "created_at": row[1].isoformat(),
        "updated_at": row[2].isoformat(),
        "document_count": row[3],
    }
    if titles is not None:
        question = titles.get(row[0])
        fields["title"] = shorten_title(question) if question is not None else None
    return ConversationListItem(**fields)


@router.get("/conversations", response_model=ConversationList, response_model_exclude_unset=True)
def list_own_conversations(
    request: Request,
    limit: int = Query(default=CONVERSATION_LIST_LIMIT, ge=1, le=CONVERSATION_LIST_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    include_titles: bool = Query(default=False),
    language: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> ConversationList:
    """List the verified caller's own conversations, newest first.

    Identity comes only from the verified token's `sub`; nothing in the query
    selects whose list is read. Plain `def`: synchronous SQLAlchemy work runs
    in the threadpool. `language` only picks the refusal's language.
    """
    user_id = _signed_in_user_id(request, authorization, language)
    session_factory = request.app.state.session_factory
    rows = _page(session_factory, user_id, limit, offset)
    has_more = len(rows) > limit
    page = rows[:limit]
    titles = (
        _first_questions(session_factory, [row[0] for row in page]) if include_titles else None
    )
    return ConversationList(
        conversations=tuple(_item(row, titles) for row in page),
        next_offset=offset + limit if has_more else None,
    )
