"""Tests for `openlaw.api.auth` -- Supabase JWT verification and user upsert.

No live Supabase project is configured anywhere in this suite. Every token
here is minted locally with a throwaway HMAC secret, exactly as
`openlaw.api.auth`'s own module docstring says an unverifiable token must be
treated: as untrusted, never as a shortcut past verification.
"""

import base64
import json
import time

import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.api.auth import (
    AuthenticatedUser,
    AuthError,
    decode_token,
    extract_bearer,
    upsert_user,
)
from openlaw.db.models import Base, User

SECRET = "test-secret"


def _token(payload: dict) -> str:
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _b64(data: dict) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_extracts_a_bearer_token() -> None:
    assert extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"


def test_bearer_extraction_is_case_insensitive() -> None:
    assert extract_bearer("bearer abc") == "abc"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer"])
def test_returns_none_for_a_non_bearer_header(header: str | None) -> None:
    assert extract_bearer(header) is None


def test_decodes_a_valid_supabase_token() -> None:
    identity = decode_token(
        _token({"sub": "user-123", "email": "person@example.com"}), SECRET
    )
    assert identity.user_id == "user-123"
    assert identity.email == "person@example.com"


def test_rejects_a_token_signed_with_the_wrong_secret() -> None:
    token = jwt.encode({"sub": "u", "email": "e@x.com"}, "other", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_rejects_a_malformed_token() -> None:
    with pytest.raises(AuthError):
        decode_token("not-a-token", SECRET)


def test_rejects_a_token_without_a_subject() -> None:
    with pytest.raises(AuthError):
        decode_token(_token({"email": "person@example.com"}), SECRET)


def test_rejects_a_token_without_an_email() -> None:
    with pytest.raises(AuthError):
        decode_token(_token({"sub": "user-123"}), SECRET)


def test_rejects_a_token_signed_with_none_algorithm() -> None:
    """`alg: none` must never be accepted, no matter what the header claims.

    PyJWT's own `encode` never produces this shape for a secret key, so the
    classic algorithm-confusion attack is reproduced by hand: an
    `{"alg": "none"}` header with no signature segment at all.
    """
    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64({"sub": "attacker", "email": "attacker@example.com"})
    forged = f"{header}.{body}."

    with pytest.raises(AuthError):
        decode_token(forged, SECRET)


def test_rejects_an_expired_token() -> None:
    expired = _token(
        {
            "sub": "user-123",
            "email": "person@example.com",
            "exp": int(time.time()) - 3600,
        }
    )
    with pytest.raises(AuthError):
        decode_token(expired, SECRET)


def test_decode_fails_closed_when_no_secret_is_configured() -> None:
    """Called with an empty secret, decode_token must deny, never accept."""
    token = _token({"sub": "user-123", "email": "person@example.com"})
    with pytest.raises(AuthError):
        decode_token(token, "")


def test_upsert_creates_a_user_on_first_sign_in() -> None:
    session = _session()
    user = upsert_user(
        session, AuthenticatedUser(user_id="user-123", email="person@example.com")
    )

    assert user.email == "person@example.com"
    assert session.query(User).count() == 1


def test_upsert_is_idempotent_across_sign_ins() -> None:
    session = _session()
    identity = AuthenticatedUser(user_id="user-123", email="person@example.com")

    first = upsert_user(session, identity)
    second = upsert_user(session, identity)

    assert first.id == second.id
    assert session.query(User).count() == 1
