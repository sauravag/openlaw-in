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

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from openlaw.api.refusal import raise_refusal

router = APIRouter()


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
