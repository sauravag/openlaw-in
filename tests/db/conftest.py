"""Fixtures for tests that need a real, live Postgres connection.

`live_engine` targets a database SEPARATE from `Settings.database_url`
(Task 5 review, H2). It used to point at the operator's real application
database, and `tests/db/test_quota.py`'s only `@pytest.mark.integration`
test creates schema there (`Base.metadata.create_all(live_engine,
checkfirst=True)`) -- so a fresh clone that followed the documented
`docker compose up -d` -> `pytest` order got its application tables created
by the test suite, behind Alembic's back, and a later `alembic upgrade head`
died on `DuplicateTable` with no documented recovery. Pointing every live-
Postgres test at its own database (`openlaw_test` by default, override with
`OPENLAW_TEST_DATABASE_URL`) makes that failure structurally impossible:
running the test suite can never again put an operator's real database into
a state where migrations fail, no matter what a future test does with
`live_engine`.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

#: Assumes the same user/password as `docker-compose.yml`'s `postgres`
#: service -- only the database name differs from `Settings.database_url`'s
#: default. Override with `OPENLAW_TEST_DATABASE_URL` for a differently
#: named or differently hosted test database.
_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://openlaw:openlaw@localhost:5432/openlaw_test"
)


def _test_database_url() -> str:
    """Return the URL live-Postgres-backed tests connect to.

    Deliberately NOT `openlaw.config.get_settings().database_url` -- see the
    module docstring for why sharing that URL with the operator's real
    application database was the defect, not a detail.
    """
    return os.environ.get("OPENLAW_TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def live_engine() -> Iterator[Engine]:
    """A real Postgres engine against the TEST database, skipping cleanly if
    unreachable (including "the test database itself doesn't exist yet" --
    see README's Setup section for the one-time `createdb`)."""
    engine = create_engine(_test_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on live environment
        engine.dispose()
        pytest.skip(f"Test Postgres database is not reachable: {exc}")
    yield engine
    engine.dispose()
