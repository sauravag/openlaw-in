"""Tests for `openlaw.db.session` — engine construction and the session
context manager's commit/rollback behaviour.

Commit/rollback semantics are generic SQLAlchemy behaviour, so they are
exercised against an in-memory SQLite engine rather than live Postgres --
no live dependency is needed to prove `session_scope` commits on success and
rolls back on error. A single `@pytest.mark.integration` test below proves
`engine_from_settings` actually reaches the live Postgres container, mirroring
`tests/graph/test_connection.py`'s `live_driver` pattern: it skips cleanly
(never fails) when Postgres is unreachable.
"""

import pytest
from sqlalchemy import create_engine, text

from openlaw.config import Settings, get_settings
from openlaw.db.models import Base, User
from openlaw.db.session import engine_from_settings, session_scope


def test_engine_from_settings_uses_the_configured_database_url() -> None:
    settings = Settings(
        vaquill_root="/tmp/v", kanoongpt_root="/tmp/k", database_url="sqlite://"
    )
    engine = engine_from_settings(settings)
    assert str(engine.url) == "sqlite://"


def test_session_scope_commits_on_success() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with session_scope(engine) as session:
        session.add(User(email="person@example.com"))

    with session_scope(engine) as session:
        assert session.query(User).count() == 1


def test_session_scope_rolls_back_and_reraises_on_error() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with pytest.raises(RuntimeError, match="boom"):
        with session_scope(engine) as session:
            session.add(User(email="person@example.com"))
            session.flush()
            raise RuntimeError("boom")

    with session_scope(engine) as session:
        assert session.query(User).count() == 0


@pytest.mark.integration
def test_engine_from_settings_connects_to_a_live_postgres() -> None:
    """Smoke test against the running container; skips if unreachable."""
    engine = engine_from_settings(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on live environment
        pytest.skip(f"Postgres is not reachable: {exc}")
    finally:
        engine.dispose()
