"""Red team: migration cannot be used to take or probe anyone else's rows."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openlaw.accounts.migrate import MigrationResult, migrate_anonymous_material
from openlaw.config import Settings
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import Base, Conversation, User

pytestmark = pytest.mark.integration


@pytest.fixture
def session(live_engine: Engine) -> Iterator[Session]:
    Base.metadata.create_all(live_engine, checkfirst=True)
    with Session(live_engine) as s:
        yield s
        s.rollback()


def _settings() -> Settings:
    return Settings(vaquill_root="/tmp/v", kanoongpt_root="/tmp/k", auth_enabled=True)


def test_token_b_while_signed_in_as_a_moves_only_b_rows_into_a(
    session: Session,
) -> None:
    token_a, token_b = str(uuid.uuid4()), str(uuid.uuid4())
    user_a = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user_a)
    session.flush()
    owner_b = Owner(user_id=None, anon_token=token_b)
    owner_a = Owner(user_id=None, anon_token=token_a)
    conv_b = create_conversation(session, owner_b, _settings())
    conv_a = create_conversation(session, owner_a, _settings())
    session.commit()
    ids = (conv_a.id, conv_b.id, user_a.id)
    try:
        result = migrate_anonymous_material(session, token_b, user_a.id, _settings())
        session.commit()
        session.expire_all()

        assert result.conversations_moved == 1
        assert conv_b.user_id == user_a.id
        assert conv_a.user_id is None and conv_a.anon_token == token_a
    finally:
        session.rollback()
        session.query(Conversation).filter(Conversation.id.in_(ids[:2])).delete(
            synchronize_session=False
        )
        session.query(User).filter(User.id == ids[2]).delete()
        session.commit()


def test_unknown_token_yields_zeros_not_an_error(session: Session) -> None:
    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    session.flush()
    try:
        result = migrate_anonymous_material(
            session, str(uuid.uuid4()), user.id, _settings()
        )
        assert result == MigrationResult(0, 0, True)
    finally:
        session.rollback()
