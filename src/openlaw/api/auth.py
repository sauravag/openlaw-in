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
  claims and skip signature checking entirely.
- `exp`, `sub` and `aud` are all in `options={"require": [...]}`, not merely
  checked if present. PyJWT only *verifies* `exp` when the claim exists in
  the payload -- a token that omits it outright is otherwise treated as
  never expiring. That matters here specifically because a real, legitimately
  issued token can end up cached in a browser or a shared/cybercafe-device
  proxy, which is an ordinary setting for the people this product serves;
  requiring `exp` closes that window regardless of how the token got there.
- `audience="authenticated"` is verified, not skipped. Supabase signs its
  public `anon` API key with this same project JWT secret, and that key is
  shipped into every frontend by design -- it is not a secret. Before this
  check, such a key was only rejected by accident, because it happens to
  omit `sub`/`email`. Verifying `aud == "authenticated"` (the claim Supabase
  puts on real user session tokens, and never on the anon/service keys)
  makes that rejection a guarantee rather than a coincidence.
- The signing secret itself is length-checked (`_MIN_SECRET_LENGTH`, 32
  bytes -- the minimum PyJWT itself warns is recommended for HS256 per RFC
  7518 Section 3.2) and must be non-empty. Both make `decode_token` fail
  closed on its own if it is ever invoked with a weak or missing secret,
  rather than depending on every future caller to have checked first.
- `AuthenticatedUser.email` is `EmailStr`, not a bare `str`: a syntactically
  invalid "email" claim is rejected here, at the boundary, rather than
  reaching `openlaw.db.models.User.email` -- see the project's own rule that
  external input is validated at the boundary, never trusted downstream.
- Identity is bound to the token's `sub` (the Supabase subject), never to
  its `email` -- see `upsert_user`'s docstring for why matching on email
  alone was unsafe.
- `AuthError.__str__` is always the same fixed, uninformative string. The
  real reason lives in `.detail`, for a caller that wants to log it
  server-side, but is never meant to reach an HTTP client: a caller that
  does `detail=str(error)` (as planned for the `/ask` endpoint) cannot leak
  PyJWT internals, claim contents, or "no signing secret configured" to
  someone who is, by definition, not yet authenticated.
- Nothing in this module logs a token, a secret, or a full email address.
  Keep it that way in anything built on top of it.
"""

import jwt
from pydantic import BaseModel, ConfigDict, EmailStr, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openlaw.db.models import User

_BEARER_PREFIX = "bearer "
_ALLOWED_ALGORITHMS = ["HS256"]
_REQUIRED_CLAIMS = ["exp", "sub", "aud"]
_REQUIRED_AUDIENCE = "authenticated"
# RFC 7518 Section 3.2's recommended minimum HMAC key length for SHA-256 --
# also what PyJWT itself warns about (InsecureKeyLengthWarning) below this.
_MIN_SECRET_LENGTH = 32

_GENERIC_AUTH_FAILURE_MESSAGE = "authentication failed"


class AuthError(Exception):
    """Raised when a token is missing, malformed, or fails verification.

    `str(error)` is always `_GENERIC_AUTH_FAILURE_MESSAGE`, deliberately --
    see the module docstring's note on why the specific reason must never
    reach an HTTP client. That reason is still available, for server-side
    logs only, as `.detail`.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(_GENERIC_AUTH_FAILURE_MESSAGE)


class AuthenticatedUser(BaseModel):
    """The identity carried by a verified token."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    email: EmailStr


def extract_bearer(header: str | None) -> str | None:
    """Return the token from an `Authorization` header, or None."""
    if not header or not header.lower().startswith(_BEARER_PREFIX):
        return None
    return header[len(_BEARER_PREFIX) :].strip() or None


def _decode_claims(token: str, secret: str) -> dict:
    """Verify `token`'s signature, algorithm, audience and required claims.

    Raises AuthError on any failure. Does not yet look at `sub`/`email`
    individually -- that is `decode_token`'s job, since only the caller
    knows what an empty-but-present claim should mean.
    """
    if not secret:
        raise AuthError("no signing secret configured")
    if len(secret) < _MIN_SECRET_LENGTH:
        raise AuthError(f"signing secret shorter than {_MIN_SECRET_LENGTH} bytes")

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=_ALLOWED_ALGORITHMS,
            audience=_REQUIRED_AUDIENCE,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as error:
        raise AuthError(f"token verification failed: {error}") from error


def decode_token(token: str, secret: str) -> AuthenticatedUser:
    """Verify `token` and return the identity it carries.

    Raises AuthError on any failure: a missing, empty or too-short secret; a
    bad signature; a malformed token; an expired token; a token whose
    audience is not "authenticated" (which excludes Supabase's public anon
    key, signed with this same secret); a token missing the subject,
    audience or email claims this service relies on; or an email claim that
    is not a syntactically valid email address.
    """
    payload = _decode_claims(token, secret)

    subject = payload.get("sub")
    email = payload.get("email")
    if not subject or not email:
        raise AuthError("token is missing a subject or email claim")

    try:
        return AuthenticatedUser(user_id=str(subject), email=str(email))
    except ValidationError as error:
        raise AuthError("token email claim is not a valid email address") from error


def _email_owned_by_another_subject(
    session: Session, email: str, subject: str
) -> bool:
    """True if `email` already belongs to a user whose id is not `subject`."""
    conflict = (
        session.query(User)
        .filter(User.email == email, User.id != subject)
        .one_or_none()
    )
    return conflict is not None


def upsert_user(session: Session, identity: AuthenticatedUser) -> User:
    """Return the stored user for `identity`, creating it on first sign-in.

    Users are identified by the Supabase subject (`identity.user_id`), never
    by email. Matching on email alone -- the brief's original shape -- let a
    *different* Supabase identity that later controls the same email address
    (an email change on Supabase's side, or an unrelated person receiving a
    recycled mailbox; `users` rows are never deleted, so an old email stays
    claimed indefinitely) silently inherit an existing account: its id, and
    therefore every `Conversation` row already bound to that id. For a
    legal-aid product this is not a hypothetical -- it is one plausible path
    from "person disclosed a family situation" to "the person who now
    controls their mailbox reads the transcript."

    Raises AuthError, instead of silently returning a foreign row, when the
    token's email already belongs to a different subject. This can happen
    both when creating a brand-new row and when an already-known subject's
    email has changed on Supabase's side to collide with someone else's --
    both are handled the same way here.

    No schema migration is needed for this: `users.id` was already being set
    to `identity.user_id` on creation (see the brief's original code), so
    every row already carries the correct Supabase subject as its primary
    key today. The only defect was the *lookup* matching on email instead of
    id. A deployment that had already accepted real sign-ins under the old
    logic would need to audit for duplicate/foreign email claims before
    upgrading, since two different Supabase subjects could already be
    sharing one row's id under the old behaviour -- not applicable here,
    since this project holds no production data yet.
    """
    if _email_owned_by_another_subject(session, identity.email, identity.user_id):
        raise AuthError("email is registered to a different account")

    user = session.get(User, identity.user_id)
    if user is not None:
        if user.email != identity.email:
            user.email = identity.email
            _flush_or_deny(session)
        return user

    user = User(id=identity.user_id, email=identity.email)
    session.add(user)
    _flush_or_deny(session)
    return user


def _flush_or_deny(session: Session) -> None:
    """Flush `session`, converting a uniqueness race into an AuthError.

    The pre-check in `upsert_user` is not atomic with this write: two
    concurrent sign-ins for the same email under different subjects could
    both pass the check before either commits. The `unique=True` constraint
    on `User.email` is the real backstop; this only turns its violation into
    the same denial the pre-check would have given, instead of an
    unhandled database error.
    """
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise AuthError("email is registered to a different account") from error
