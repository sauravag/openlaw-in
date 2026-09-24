from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from openlaw.config import get_settings
from openlaw.db.session import engine_from_settings


@pytest.fixture
def live_engine() -> Iterator[Engine]:
    """A real Postgres engine, skipping the test cleanly if unreachable."""
    engine = engine_from_settings(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on live environment
        engine.dispose()
        pytest.skip(f"Postgres is not reachable: {exc}")
    yield engine
    engine.dispose()
