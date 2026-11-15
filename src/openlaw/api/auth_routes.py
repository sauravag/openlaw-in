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

from openlaw.accounts.migrate import migrate_anonymous_material
from openlaw.api.auth import AuthError, decode_token, extract_bearer, upsert_user
from openlaw.api.refusal import raise_refusal

logger = logging.getLogger(__name__)

router = APIRouter()
#: Included by `create_app` only when `settings.auth_enabled` (404 in Mode A).
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
        logger.warning("account migration refused: %s", error.detail)
        raise_refusal("sign_in_required", headers=_BEARER_CHALLENGE)
    try:
        with session_factory() as session:
            user_id = upsert_user(session, identity).id
            session.commit()
    except AuthError as error:
        # Only the email-collision case: a verified token whose email belongs
        # to another subject. Signing in again cannot fix it, hence 409.
        logger.warning("account migration refused: %s", error.detail)
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

