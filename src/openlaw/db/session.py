"""Database engine and session lifecycle."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from openlaw.config import Settings


def engine_from_settings(settings: Settings) -> Engine:
    """Return a SQLAlchemy engine built from `settings`."""
    return create_engine(settings.database_url, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on failure."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
