"""Auth-mode discovery, available in BOTH modes.

`GET /auth/status` is registered unconditionally. An endpoint that existed only
when auth is enabled could not be used to detect that auth is *disabled*, and
that is exactly the answer a frontend built with a sign-in button needs in
order to notice it is talking to a backend that has no idea what sign-in is.
(`NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` is fixed at frontend build time;
`OPENLAW_AUTH_ENABLED` is read at backend run time, so they can disagree.)

`GET /auth/unavailable` is the shared, localised "sign-in is not available
here" refusal, built by `openlaw.api.refusal` so it carries the escalation
footer and helplines like every other terminal state. The frontend's sign-in
routes return it when they detect a mode mismatch; it is mode-independent
because the frontend, not the backend, knows the build is mismatched.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from openlaw.accounts.delete import delete_account
from openlaw.accounts.identity import remove_supabase_identity
from openlaw.accounts.migrate import migrate_anonymous_material
from openlaw.accounts.notes import deletion_notes
from openlaw.api.auth import AuthError, decode_token, extract_bearer, upsert_user
from openlaw.api.refusal import raise_refusal

logger = logging.getLogger(__name__)

router = APIRouter()
#: Account endpoints (migrate, delete). Included by `create_app` only when
#: `settings.auth_enabled` (404 in Mode A).
migration_router = APIRouter()


class AuthStatus(BaseModel):
    """What the frontend needs to know about the server's auth mode.

    Carries NO secret and NO identity: `auth_enabled` and, when enabled, the
    Supabase project URL the browser would be redirected to anyway. The
    `supabase_jwt_secret` is never exposed here or anywhere else.
    """

    auth_enabled: bool
    supabase_url: str | None = None


@router.get("/auth/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthStatus(auth_enabled=False)
    return AuthStatus(auth_enabled=True, supabase_url=settings.supabase_url)


@router.get("/auth/unavailable")
async def auth_unavailable(language: str | None = Query(default=None)) -> None:
    """Return the localised, help-carrying 'sign-in unavailable' refusal."""
    raise_refusal("auth_unavailable", language=language)


class MigrationResponse(BaseModel):
    """What moved. Counts only: never whether the token owned anything."""

    conversations_moved: int
    documents_moved: int


_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}
_NOTHING_MOVED = MigrationResponse(conversations_moved=0, documents_moved=0)


def _verified_user_id(request: Request, session_factory) -> str:
    """Verify the bearer and commit the user row; refuse on any failure.

    The upsert is committed on its own here: `_flush_or_deny` rolls back the
    WHOLE transaction on an `IntegrityError`, so the migration must never be
    staged alongside it. `AuthError.detail` goes to the server log only.
    """
    settings = request.app.state.settings
    token = extract_bearer(request.headers.get("authorization"))
    try:
        if token is None:
            raise AuthError("no bearer token")
        identity = decode_token(token, settings.supabase_jwt_secret)
    except AuthError as error:
        logger.warning("account request refused: %s", error.detail)
        raise_refusal("sign_in_required", headers=_BEARER_CHALLENGE)
    try:
        with session_factory() as session:
            user_id = upsert_user(session, identity).id
            session.commit()
    except AuthError as error:
        # Only the email-collision case: a verified token whose email belongs
        # to another subject. Signing in again cannot fix it, hence 409.
        logger.warning("account request refused: %s", error.detail)
        raise_refusal("account_email_conflict")
    return user_id


@migration_router.post("/account/migrate", response_model=MigrationResponse)
def migrate_account(request: Request) -> MigrationResponse:
    """Claim the caller's anonymous material for their signed-in account.

    Registered only when `settings.auth_enabled`. Requires a verified bearer:
    an unverifiable one is a refusal, NOT a fall back to the anonymous
    allowance as `/ask` does -- "migrate to my account" has no anonymous
    meaning. Identity comes only from the verified token's `sub`; the
    anonymous token comes only from the HttpOnly cookie (via
    `request.state.anon_token`), never from the body.

    The migration runs in its own transaction, committed here on success and
    rolled back on any exception (`migrate_anonymous_material` only flushes).
    Plain `def`, not `async def`: the SQLAlchemy work is synchronous and must
    run in FastAPI's threadpool, not on the event loop. Nothing here awaits.
    """
    session_factory = request.app.state.session_factory
    user_id = _verified_user_id(request, session_factory)
    anon_token = getattr(request.state, "anon_token", None)
    if not anon_token:
        return _NOTHING_MOVED
    settings = request.app.state.settings
    with session_factory() as session:
        try:
            result = migrate_anonymous_material(session, anon_token, user_id, settings)
            session.commit()
        except Exception:
            session.rollback()
            raise
    return MigrationResponse(
        conversations_moved=result.conversations_moved,
        documents_moved=result.documents_moved,
    )



class DeletionResponse(BaseModel):
    """What an erasure removed, and, honestly, what it did not.

    `audit_rows_retained` counts the access-log rows kept for the erased
    documents (including the ones this erasure just wrote). `identity_removed`
    is True only when the Supabase identity is confirmed gone. Both notes are
    always sent so the UI can show either language.
    """

    conversations_deleted: int
    documents_deleted: int
    messages_deleted: int
    audit_rows_retained: int
    identity_removed: bool
    note_en: str
    note_hi: str


@migration_router.delete("/account", response_model=DeletionResponse)
def delete_own_account(request: Request) -> DeletionResponse:
    """Erase the caller's own account: local data first, then the identity.

    Registered only when `settings.auth_enabled`. Identity comes ONLY from
    the verified token's `sub`: there is no id in the path, query or body, so
    no admin path exists. All local deletes run in one transaction, committed
    here on success and rolled back on any exception. Only AFTER that commit
    is the Supabase identity removed (when a service key is configured):
    deleting it first and then failing locally would leave someone unable to
    sign in to retry erasure of data that still exists. A failed identity
    removal is reported (`identity_removed=False`), never hidden.

    Plain `def`, not `async def`: the SQLAlchemy and httpx work is
    synchronous and must run in FastAPI's threadpool.
    """
    session_factory = request.app.state.session_factory
    user_id = _verified_user_id(request, session_factory)
    with session_factory() as session:
        try:
            result = delete_account(session, user_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
    identity_removed = remove_supabase_identity(request.app.state.settings, user_id)
    note_en, note_hi = deletion_notes(identity_removed)
    return DeletionResponse(
        conversations_deleted=result.conversations_deleted,
        documents_deleted=result.documents_deleted,
        messages_deleted=result.messages_deleted,
        audit_rows_retained=result.audit_rows_retained,
        identity_removed=identity_removed,
        note_en=note_en,
        note_hi=note_hi,
    )
