"""Tests for `openlaw.accounts.migrate` against the live TEST Postgres.

Each test uses unique tokens/user ids and cleans up its own rows, so the
shared `openlaw_test` database is left as found. The caller owns the commit
(repo convention), so tests commit explicitly where production would.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openlaw.accounts import migrate as migrate_module
from openlaw.accounts.migrate import MigrationResult, migrate_anonymous_material
from openlaw.config import Settings
from openlaw.db.conversations import Owner, create_conversation
from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    DocumentExcerptRow,
    User,
)
from openlaw.documents.crypto import decrypt, load_keyring
from openlaw.documents.store import store_document

pytestmark = pytest.mark.integration

_KEY = base64.b64encode(b"1" * 32).decode()
_TEXT = b"The landlord must refund the security deposit within thirty days."


def _settings() -> Settings:
    return Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        document_encryption_key=_KEY,
        auth_enabled=True,
    )


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def session(live_engine: Engine) -> Iterator[Session]:
    Base.metadata.create_all(live_engine, checkfirst=True)
    session = Session(live_engine)
    created_users: list[str] = []
    session.info["users"] = created_users
    yield session
    session.rollback()
    user_ids = created_users
    conv_ids = [
        c.id
        for c in session.scalars(
            select(Conversation).where(Conversation.user_id.in_(user_ids))
        )
    ] + session.info.get("conversations", [])
    doc_ids = [
        d.id
        for d in session.scalars(
            select(Document).where(Document.conversation_id.in_(conv_ids))
        )
    ]
    session.query(DocumentAccessLog).filter(
        DocumentAccessLog.document_id.in_(doc_ids)
    ).delete(synchronize_session=False)
    session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(
        synchronize_session=False
    )
    session.query(User).filter(User.id.in_(user_ids)).delete(
        synchronize_session=False
    )
    session.commit()
    session.close()


def _user(session: Session) -> str:
    user = User(email=f"{_uid()}@example.com")
    session.add(user)
    session.flush()
    session.info["users"].append(user.id)
    return user.id


def _anon_material(
    session: Session, token: str, documents: int = 1
) -> tuple[Conversation, list[Document]]:
    settings = _settings()
    owner = Owner(user_id=None, anon_token=token)
    conversation = create_conversation(session, owner, settings)
    session.info.setdefault("conversations", []).append(conversation.id)
    docs = [
        store_document(
            session, owner, conversation.id, f"n{i}.txt", "text/plain", _TEXT, settings
        )
        for i in range(documents)
    ]
    session.commit()
    return conversation, docs


def _migrate_logs(session: Session, doc_ids: list[str]) -> list[DocumentAccessLog]:
    return list(
        session.scalars(
            select(DocumentAccessLog).where(
                DocumentAccessLog.document_id.in_(doc_ids),
                DocumentAccessLog.action == "migrate",
            )
        )
    )


def test_moves_conversations_and_documents_owned_by_the_token(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    conversation, docs = _anon_material(session, token, documents=2)

    result = migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()
    session.expire_all()

    assert result == MigrationResult(1, 2, False)
    assert conversation.user_id == user_id and conversation.anon_token is None
    assert all(d.user_id == user_id and d.anon_token is None for d in docs)


def test_clears_expires_at_on_conversations_and_documents(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    conversation, docs = _anon_material(session, token)
    assert conversation.expires_at is not None and docs[0].expires_at is not None

    migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()
    session.expire_all()

    assert conversation.expires_at is None
    assert docs[0].expires_at is None


def test_leaves_another_tokens_rows_untouched(session: Session) -> None:
    mine, other, user_id = _uid(), _uid(), _user(session)
    _anon_material(session, mine)
    other_conv, other_docs = _anon_material(session, other)

    migrate_anonymous_material(session, mine, user_id, _settings())
    session.commit()
    session.expire_all()

    assert other_conv.anon_token == other and other_conv.user_id is None
    assert other_conv.expires_at is not None
    assert other_docs[0].anon_token == other and other_docs[0].expires_at is not None


def test_leaves_rows_owned_by_another_account_even_if_they_carry_the_token(
    session: Session,
) -> None:
    """The CHECK allows `user_id` AND `anon_token` both set, so the
    `user_id IS NULL` half of the predicate is what protects such a row."""
    token, new_user, old_user = _uid(), _user(session), _user(session)
    conversation = Conversation(user_id=old_user, anon_token=token)
    session.add(conversation)
    session.flush()
    session.info.setdefault("conversations", []).append(conversation.id)
    document = Document(
        user_id=old_user,
        anon_token=token,
        conversation_id=conversation.id,
        filename="x.txt",
        content_type="text/plain",
        byte_size=1,
        page_count=1,
        ciphertext=b"c",
        nonce=b"n",
        key_id="k",
    )
    session.add(document)
    session.commit()

    result = migrate_anonymous_material(session, token, new_user, _settings())
    session.commit()
    session.expire_all()

    assert result == MigrationResult(0, 0, True)
    assert conversation.user_id == old_user and conversation.anon_token == token
    assert document.user_id == old_user and document.anon_token == token
    assert _migrate_logs(session, [document.id]) == []


def test_anon_owner_clause_requires_user_id_is_null() -> None:
    from openlaw.db.conversations import owner_clause

    sql = str(owner_clause(Conversation, Owner(user_id=None, anon_token="t")))

    assert "user_id IS NULL" in sql and "anon_token =" in sql


def test_loaded_orm_objects_reflect_the_new_owner_without_expiring(
    session: Session,
) -> None:
    token, user_id = _uid(), _user(session)
    conversation, docs = _anon_material(session, token)
    assert conversation.anon_token == token  # loaded, in the identity map

    migrate_anonymous_material(session, token, user_id, _settings())

    assert conversation.user_id == user_id and conversation.anon_token is None
    assert conversation.expires_at is None
    assert docs[0].user_id == user_id and docs[0].anon_token is None


def test_second_call_returns_zeros_and_already_migrated(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    _, docs = _anon_material(session, token)
    migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()

    again = migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()

    assert again == MigrationResult(0, 0, True)
    assert len(_migrate_logs(session, [docs[0].id])) == 1


def test_writes_one_migrate_audit_row_per_document(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    _, docs = _anon_material(session, token, documents=3)

    migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()

    logs = _migrate_logs(session, [d.id for d in docs])
    assert sorted(log.document_id for log in logs) == sorted(d.id for d in docs)
    assert all(log.actor_kind == "user" and log.actor_ref == user_id for log in logs)


def test_failure_updating_documents_leaves_nothing_moved(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    conversation, docs = _anon_material(session, token)
    real = migrate_module.reassign_owner
    moved: list[list[str]] = []

    def failing(sess: Session, model: type, *args: object) -> list[str]:
        if model is Document:
            raise RuntimeError("boom")
        ids = real(sess, model, *args)
        moved.append(ids)
        return ids

    with patch.object(migrate_module, "reassign_owner", failing):
        with pytest.raises(RuntimeError):
            migrate_anonymous_material(session, token, user_id, _settings())
    assert moved == [[conversation.id]]  # the conversation UPDATE really ran
    session.rollback()
    session.expire_all()

    assert conversation.anon_token == token and conversation.user_id is None
    assert docs[0].anon_token == token
    assert _migrate_logs(session, [docs[0].id]) == []


def test_failure_after_audit_rolls_back_moves_and_audit_rows(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    conversation, docs = _anon_material(session, token, documents=2)
    real_audit = migrate_module.audit
    calls = {"n": 0}

    def flaky(sess: Session, doc_id: str, owner: Owner, action: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("audit down")
        real_audit(sess, doc_id, owner, action)

    with patch.object(migrate_module, "audit", flaky):
        with pytest.raises(RuntimeError):
            migrate_anonymous_material(session, token, user_id, _settings())
    session.rollback()
    session.expire_all()

    assert conversation.anon_token == token
    assert all(d.anon_token == token and d.expires_at is not None for d in docs)
    assert _migrate_logs(session, [d.id for d in docs]) == []


def test_migrated_document_text_still_decrypts(session: Session) -> None:
    token, user_id = _uid(), _user(session)
    _, docs = _anon_material(session, token)
    migrate_anonymous_material(session, token, user_id, _settings())
    session.commit()
    session.expire_all()

    document = docs[0]
    plaintext = decrypt(
        document.ciphertext,
        document.nonce,
        load_keyring(_settings()),
        document.id,
        document.key_id,
    )
    excerpts = session.scalars(
        select(DocumentExcerptRow).where(DocumentExcerptRow.document_id == document.id)
    ).all()
    assert document.user_id == user_id
    assert "security deposit" in plaintext
    assert excerpts  # excerpts follow their document; they carry no owner columns


@pytest.mark.parametrize(
    ("token", "user_id"), [("", "u1"), ("t1", ""), ("", "")]
)
def test_empty_inputs_raise_before_any_sql(token: str, user_id: str) -> None:
    class Boom:
        def execute(self, *a: object, **k: object) -> None:
            raise AssertionError("SQL issued")

    with pytest.raises(ValueError):
        migrate_anonymous_material(Boom(), token, user_id, _settings())  # type: ignore[arg-type]


def test_concurrent_migrations_move_and_audit_once(live_engine: Engine) -> None:
    import threading

    Base.metadata.create_all(live_engine, checkfirst=True)
    token = _uid()
    with Session(live_engine) as setup:
        user = User(email=f"{_uid()}@example.com")
        setup.add(user)
        setup.flush()
        user_id = user.id
        conversation, docs = _anon_material(setup, token, documents=2)
        conv_id, doc_ids = conversation.id, [d.id for d in docs]
        setup.commit()

    outcome: dict[str, object] = {}

    def second() -> None:
        try:
            with Session(live_engine) as s2:
                s2.execute(text("SET lock_timeout = '10s'"))
                outcome["result"] = migrate_anonymous_material(
                    s2, token, user_id, _settings()
                )
                s2.commit()
        except BaseException as exc:  # re-raised in the main thread
            outcome["error"] = exc

    s1 = Session(live_engine)
    try:
        first = migrate_anonymous_material(s1, token, user_id, _settings())
        assert first == MigrationResult(1, 2, False)  # uncommitted, locks held

        thread = threading.Thread(target=second)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "second migration should block on the row lock"

        s1.commit()
        thread.join(timeout=15)
        assert not thread.is_alive()
        if "error" in outcome:
            raise outcome["error"]  # type: ignore[misc]
        assert outcome["result"] == MigrationResult(0, 0, True)
        with Session(live_engine) as check:
            assert len(_migrate_logs(check, doc_ids)) == 2
    finally:
        s1.close()
        with Session(live_engine) as cleanup:
            cleanup.query(DocumentAccessLog).filter(
                DocumentAccessLog.document_id.in_(doc_ids)
            ).delete(synchronize_session=False)
            cleanup.query(Conversation).filter(Conversation.id == conv_id).delete()
            cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()


def test_reassign_owner_refuses_non_anonymous_source_and_anonymous_destination() -> None:
    from openlaw.db.conversations import reassign_owner

    anon = Owner(user_id=None, anon_token="t")
    user = Owner(user_id="u", anon_token=None)
    with pytest.raises(ValueError):
        reassign_owner(None, Conversation, user, user)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reassign_owner(None, Conversation, anon, anon)  # type: ignore[arg-type]
