"""Tests for `openlaw.api.auth` -- Supabase JWT verification and user upsert.

No live Supabase project is configured anywhere in this suite. Every token
here is minted locally with a throwaway HMAC secret, exactly as
`openlaw.api.auth`'s own module docstring says an unverifiable token must be
treated: as untrusted, never as a shortcut past verification.

`_claims()` builds a payload shaped like a real Supabase user-session token
(`sub`, `email`, `aud="authenticated"`, `role="authenticated"`, a future
`exp`) so "valid token" tests exercise the same claim set Supabase actually
issues, and "rejects a token missing X" tests drop exactly one claim from
that same baseline.
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

# >= the module's _MIN_SECRET_LENGTH (32 bytes) -- a short secret is now
# rejected on its own merits, exercised separately below.
SECRET = "unit-test-signing-secret-32-bytes-min!!"


def _claims(**overrides: object) -> dict:
    base = {
        "sub": "user-123",
        "email": "person@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    base.update(overrides)
    return base


def _claims_without(*keys: str) -> dict:
    claims = _claims()
    for key in keys:
        claims.pop(key, None)
    return claims


def _token(payload: dict, secret: str = SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


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
    identity = decode_token(_token(_claims()), SECRET)
    assert identity.user_id == "user-123"
    assert identity.email == "person@example.com"


def test_rejects_a_token_signed_with_the_wrong_secret() -> None:
    token = jwt.encode(
        _claims(), "a-completely-different-signing-secret!!", algorithm="HS256"
    )
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_rejects_a_malformed_token() -> None:
    with pytest.raises(AuthError):
        decode_token("not-a-token", SECRET)


def test_rejects_a_token_without_a_subject() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims_without("sub")), SECRET)


def test_rejects_a_token_without_an_email() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims_without("email")), SECRET)


def test_rejects_a_token_with_an_invalid_email_claim() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims(email="not-an-email-address")), SECRET)


def test_decode_folds_the_email_claims_case() -> None:
    """`EmailStr` alone only lowercases the domain half of an address --

    the local part ("Victim" vs "victim") is folded here, in decode_token,
    so the same person's token always yields the same identity regardless
    of how Supabase happens to have cased it.
    """
    identity = decode_token(_token(_claims(email="Victim@Example.com")), SECRET)
    assert identity.email == "victim@example.com"


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
    expired = _token(_claims(exp=int(time.time()) - 3600))
    with pytest.raises(AuthError):
        decode_token(expired, SECRET)


def test_rejects_a_token_without_an_expiry() -> None:
    """A legitimately signed token cached forever on a shared device must not

    be accepted forever. PyJWT only verifies `exp` when it is present in the
    payload -- a token that omits it outright would otherwise never expire.
    """
    with pytest.raises(AuthError):
        decode_token(_token(_claims_without("exp")), SECRET)


def test_rejects_a_token_without_an_audience() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims_without("aud")), SECRET)


def test_rejects_a_token_with_the_wrong_audience() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims(aud="anon")), SECRET)


def test_rejects_a_token_without_a_role() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims_without("role")), SECRET)


def test_rejects_a_token_with_the_wrong_role() -> None:
    """A service-role key that ever ships with `aud: authenticated` must

    still fail on its `role` claim -- a second, independent check against
    the same class of non-user token as the audience check above.
    """
    with pytest.raises(AuthError):
        decode_token(_token(_claims(role="service_role")), SECRET)


def test_rejects_a_token_shaped_like_the_supabase_anon_key() -> None:
    """The anon API key is public, signed with this same project secret, and

    must never be accepted as a user identity. It carries no `sub`/`email`
    and no `aud: authenticated` -- this is the scenario that used to pass
    only by accident.
    """
    anon_key_shaped = {
        "iss": "supabase",
        "ref": "abcdefghijklmno",
        "role": "anon",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    with pytest.raises(AuthError):
        decode_token(_token(anon_key_shaped), SECRET)


def test_decode_fails_closed_when_no_secret_is_configured() -> None:
    """Called with an empty secret, decode_token must deny, never accept."""
    with pytest.raises(AuthError):
        decode_token(_token(_claims()), "")


def test_decode_fails_closed_when_the_secret_is_too_short() -> None:
    with pytest.raises(AuthError):
        decode_token(_token(_claims()), "too-short")


def test_auth_error_never_exposes_its_detail_via_str() -> None:
    """The client-facing message must always be the same generic string --

    see the module docstring on why a caller must never be able to leak
    PyJWT internals or "no signing secret configured" via `str(error)`.
    """
    try:
        decode_token("not-a-token", SECRET)
    except AuthError as error:
        assert str(error) == "authentication failed"
        assert "not-a-token" not in str(error)
        assert error.detail != str(error)
    else:
        pytest.fail("expected AuthError")


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


def test_upsert_matches_by_subject_not_by_email() -> None:
    """A different subject re-using an existing email must never inherit

    that account -- see `upsert_user`'s docstring for the account-takeover
    scenario this defends against (an email change, or a recycled mailbox).
    """
    session = _session()
    upsert_user(
        session, AuthenticatedUser(user_id="user-a", email="shared@example.com")
    )

    with pytest.raises(AuthError):
        upsert_user(
            session, AuthenticatedUser(user_id="user-b", email="shared@example.com")
        )

    assert session.query(User).count() == 1
    assert session.get(User, "user-a").email == "shared@example.com"


def test_upsert_denies_an_existing_subjects_changed_email_if_it_collides() -> None:
    session = _session()
    upsert_user(session, AuthenticatedUser(user_id="user-a", email="a@example.com"))
    upsert_user(session, AuthenticatedUser(user_id="user-b", email="b@example.com"))

    with pytest.raises(AuthError):
        upsert_user(
            session, AuthenticatedUser(user_id="user-a", email="b@example.com")
        )

    assert session.get(User, "user-a").email == "a@example.com"


def test_upsert_folds_email_case_for_storage_and_conflict_detection() -> None:
    """"Victim@x.com" and "victim@x.com" must be treated as one identity --

    built directly here (bypassing decode_token's own folding) to prove the
    guarantee lives in upsert_user too, at the persistence boundary, not
    only at the token boundary.
    """
    session = _session()
    stored = upsert_user(
        session, AuthenticatedUser(user_id="user-a", email="Victim@Example.com")
    )
    assert stored.email == "victim@example.com"

    with pytest.raises(AuthError):
        upsert_user(
            session,
            AuthenticatedUser(user_id="user-b", email="VICTIM@example.com"),
        )
    assert session.query(User).count() == 1


def test_flush_or_deny_denies_a_concurrent_email_collision() -> None:
    """Simulates the race `upsert_user`'s pre-check cannot close alone: two

    sessions both passing the check before either commits. `User.email`'s
    `unique=True` constraint is the real backstop; this exercises it
    directly rather than needing genuine thread concurrency for a case that
    is otherwise unreachable through the public interface in one session.
    """
    from openlaw.api.auth import _flush_or_deny

    session = _session()
    session.add(User(id="user-a", email="race@example.com"))
    session.commit()

    session.add(User(id="user-b", email="race@example.com"))
    with pytest.raises(AuthError):
        _flush_or_deny(session)


def test_upsert_updates_the_stored_email_for_the_same_subject() -> None:
    """The same Supabase subject legitimately changing their email is not

    the attack this module defends against -- only a *different* subject
    claiming someone else's email is.
    """
    session = _session()
    upsert_user(session, AuthenticatedUser(user_id="user-a", email="old@example.com"))

    updated = upsert_user(
        session, AuthenticatedUser(user_id="user-a", email="new@example.com")
    )

    assert updated.email == "new@example.com"
    assert session.query(User).count() == 1
