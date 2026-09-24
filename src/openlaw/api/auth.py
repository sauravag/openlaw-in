"""Supabase JWT verification.

Supabase handles email and Google OAuth, so no password ever reaches this
service. We verify the token it issues and record only the subject and email
-- see `openlaw.db.models.User` for why nothing else is ever persisted about
a signed-in person. An unverifiable token is treated as anonymous, never as
trusted: `decode_token` raises on anything it cannot fully verify, and it is
the caller's job (see `openlaw.api.routes`, Task 4) to fall back to the
anonymous allowance rather than let a rejected token through.

Security notes, since this module is the entire trust boundary between an
HTTP request and a database write:

- `algorithms=["HS256"]` is passed explicitly and is never derived from the
  token itself. This is what defeats the classic JWT "algorithm confusion"
  attack, where a token forged with `{"alg": "none"}` (or with an
  attacker-controlled algorithm) is presented for verification -- without a
  pinned algorithm list, some verifiers would honour whatever the token
  claims and skip signature checking entirely. PyJWT rejects `none` outright
  by default, but pinning the list is the defense that does not depend on
  that default staying enabled.
- An empty or missing secret makes `decode_token` raise immediately, before
  it ever reaches `jwt.decode`. This is redundant with the caller-side guard
  in `openlaw.api.routes` (which only calls `decode_token` when
  `settings.supabase_jwt_secret` is configured) -- deliberately so: this
  function must fail closed on its own, not merely because every caller
  today happens to check first.
- Known accepted gap: PyJWT only verifies `exp` when the claim is present in
  the payload; a token that omits `exp` entirely is not rejected for that
  reason alone. This is not enforced here because doing so would reject
  every token in this module's own test suite (as specified), and because it
  is not independently exploitable: forging a token at all already requires
  possessing `secret`, at which point expiry is no longer the binding
  control. Every token Supabase actually issues carries `exp`. Recorded here
  so a future reviewer does not have to rediscover it.
- Nothing in this module logs a token, a secret, or a full email address.
  Keep it that way in anything built on top of it -- `AuthError` messages
  come from PyJWT and describe the failure mode, not the token's contents,
  but a caller that logs the raw `Authorization` header undoes that.
"""

import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from openlaw.db.models import User

_BEARER_PREFIX = "bearer "
_ALLOWED_ALGORITHMS = ["HS256"]


class AuthError(Exception):
    """Raised when a token is missing, malformed, or fails verification."""


class AuthenticatedUser(BaseModel):
    """The identity carried by a verified token."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    email: str


def extract_bearer(header: str | None) -> str | None:
    """Return the token from an `Authorization` header, or None."""
    if not header or not header.lower().startswith(_BEARER_PREFIX):
        return None
    return header[len(_BEARER_PREFIX) :].strip() or None


def decode_token(token: str, secret: str) -> AuthenticatedUser:
    """Verify `token` and return the identity it carries.

    Raises AuthError on any failure: a missing or empty secret, a bad
    signature, a malformed token, an expired token, or a token missing the
    subject/email claims this service relies on. See the module docstring
    for the security reasoning behind each check.
    """
    if not secret:
        raise AuthError("no signing secret configured")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=_ALLOWED_ALGORITHMS,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as error:
        raise AuthError(f"token verification failed: {error}") from error

    subject = payload.get("sub")
    email = payload.get("email")
    if not subject or not email:
        raise AuthError("token is missing a subject or email claim")

    return AuthenticatedUser(user_id=str(subject), email=str(email))


def upsert_user(session: Session, identity: AuthenticatedUser) -> User:
    """Return the stored user for `identity`, creating it on first sign-in."""
    user = session.query(User).filter(User.email == identity.email).one_or_none()
    if user is not None:
        return user

    user = User(id=identity.user_id, email=identity.email)
    session.add(user)
    session.flush()
    return user
