"""Plan 7 Task 2: in the default mode nothing expires and nothing is capped.

Parametrised over both modes so a flag exercised in one state cannot break
quietly in the other. Mode A (auth off) must write `expires_at IS NULL` for
every owner; Mode B (auth on) keeps the anonymous TTL.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.api.artifacts import _retention_notice
from openlaw.config import Settings
from openlaw.db import conversations as conversations_module
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import Base
from openlaw.documents import store as store_module


def _settings(auth_enabled: bool) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        auth_enabled=auth_enabled,
    )


ANON = Owner(None, "anon-token")


def test_retention_is_enforced_only_with_auth() -> None:
    assert _settings(False).retention_enforced is False
    assert _settings(True).retention_enforced is True


@pytest.mark.parametrize("module", [conversations_module, store_module])
def test_mode_a_anonymous_expiry_is_none(module: object) -> None:
    assert module._anonymous_expiry(ANON, _settings(False)) is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("module", "ttl_field"),
    [
        (conversations_module, "anonymous_conversation_ttl_hours"),
        (store_module, "anonymous_upload_ttl_hours"),
    ],
)
def test_mode_b_anonymous_expiry_is_now_plus_ttl(module: object, ttl_field: str) -> None:
    settings = _settings(True)
    before = datetime.now(UTC)

    expiry = module._anonymous_expiry(ANON, settings)  # type: ignore[attr-defined]

    assert expiry is not None
    assert expiry >= before + timedelta(hours=getattr(settings, ttl_field))
    assert expiry < before + timedelta(hours=getattr(settings, ttl_field), minutes=1)


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_authenticated_owner_never_expires_in_either_mode(auth_enabled: bool) -> None:
    owner = Owner("user-1", None)
    settings = _settings(auth_enabled)
    assert conversations_module._anonymous_expiry(owner, settings) is None
    assert store_module._anonymous_expiry(owner, settings) is None


@pytest.mark.parametrize(("auth_enabled", "expects_expiry"), [(False, False), (True, True)])
def test_created_anonymous_conversation_expiry_follows_the_mode(
    auth_enabled: bool, expects_expiry: bool
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = create_conversation(session, ANON, _settings(auth_enabled))

        assert (conversation.expires_at is not None) is expects_expiry
        # Ownership is untouched by either mode: the row is still keyed.
        assert conversation.anon_token == "anon-token"


@pytest.mark.parametrize(("auth_enabled", "mentions_deletion"), [(False, False), (True, True)])
def test_retention_copy_never_promises_a_deletion_that_will_not_happen(
    auth_enabled: bool, mentions_deletion: bool
) -> None:
    notice = _retention_notice(ANON, _settings(auth_enabled), "en")

    assert ("deleted automatically" in notice) is mentions_deletion
    assert ("stays until you delete it" in notice) is not mentions_deletion


@pytest.mark.parametrize(("auth_enabled", "enforced"), [(False, False), (True, True)])
def test_burst_limiter_runs_only_in_mode_b(auth_enabled: bool, enforced: bool) -> None:
    assert _settings(auth_enabled).burst_limit_enforced is enforced


def test_mode_a_allows_more_documents_per_conversation_than_mode_b() -> None:
    mode_a = _settings(False).effective_max_documents_per_conversation
    mode_b = _settings(True).effective_max_documents_per_conversation

    assert mode_b == 3
    assert mode_a == 25


def test_mode_a_never_lowers_an_operator_configured_document_cap() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        max_documents_per_conversation=100,
    )
    assert settings.effective_max_documents_per_conversation == 100


def test_mode_a_route_is_never_burst_limited() -> None:
    from fastapi.testclient import TestClient

    from tests.api.test_routes import _client, _settings as route_settings

    client = _client(settings=route_settings(auth_enabled=False, quota_enabled=False))
    now = __import__("time").monotonic()
    client.app.state.rate_limit_hits["testclient"] = [now] * 1000  # type: ignore[attr-defined]

    response = client.post("/ask", json={"question": "what is bail?"})

    assert response.status_code != 429
    assert isinstance(client, TestClient)
