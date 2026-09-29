"""Schema tests for Plan 6 Task 2: document artifacts and conversation
ownership.

Covers the new `Document`, `DocumentExcerptRow`, and `DocumentAccessLog`
tables, the ownership check constraints on `Conversation` and `Document`,
and the cascade-delete behaviour a later purge/deletion path depends on.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import LargeBinary, String, Text, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    DocumentExcerptRow,
    Message,
    User,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _document(**overrides: object) -> Document:
    defaults: dict = {
        "filename": "notice.pdf",
        "content_type": "application/pdf",
        "byte_size": 1024,
        "page_count": 2,
        "ciphertext": b"cipher",
        "nonce": b"nonce-bytes-",
        "key_id": "0" * 16,
    }
    defaults.update(overrides)
    return Document(**defaults)


# --- Column sets -----------------------------------------------------------


def test_conversation_has_ownership_and_retention_columns() -> None:
    columns = {column.name for column in Conversation.__table__.columns}
    assert {
        "id",
        "user_id",
        "anon_token",
        "created_at",
        "updated_at",
        "expires_at",
    } <= columns


def test_message_has_claims_and_language_columns() -> None:
    columns = {column.name for column in Message.__table__.columns}
    assert {
        "turn_index",
        "claims",
        "document_claims",
        "language",
        "content",
    } <= columns


def test_document_has_expected_columns() -> None:
    columns = {column.name for column in Document.__table__.columns}
    assert columns == {
        "id",
        "user_id",
        "anon_token",
        "conversation_id",
        "filename",
        "content_type",
        "byte_size",
        "page_count",
        "ciphertext",
        "nonce",
        "key_id",
        "crisis_kind",
        "created_at",
        "expires_at",
    }


def test_document_excerpt_row_has_expected_columns() -> None:
    columns = {column.name for column in DocumentExcerptRow.__table__.columns}
    assert columns == {
        "id",
        "document_id",
        "excerpt_id",
        "page_number",
        "ciphertext",
        "nonce",
        "key_id",
        "ordinal",
    }


def test_document_access_log_has_expected_columns() -> None:
    columns = {column.name for column in DocumentAccessLog.__table__.columns}
    assert columns == {
        "id",
        "document_id",
        "actor_kind",
        "actor_ref",
        "action",
        "created_at",
    }


# --- Ownership check constraints -------------------------------------------


def test_conversation_rejects_a_row_with_neither_owner() -> None:
    session = _session()
    session.add(Conversation(user_id=None, anon_token=None))
    with pytest.raises(IntegrityError):
        session.commit()


def test_conversation_accepts_a_user_owned_row() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    session.add(Conversation(user_id=user.id, anon_token=None))
    session.commit()
    assert session.query(Conversation).count() == 1


def test_conversation_accepts_an_anonymous_owned_row() -> None:
    session = _session()
    session.add(Conversation(user_id=None, anon_token="anon-token-abc"))
    session.commit()
    assert session.query(Conversation).count() == 1


def test_document_rejects_a_row_with_neither_owner() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(
        _document(user_id=None, anon_token=None, conversation_id=conversation.id)
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_document_accepts_an_anonymous_owned_row() -> None:
    session = _session()
    conversation = Conversation(user_id=None, anon_token="anon-token-abc")
    session.add(conversation)
    session.flush()

    session.add(
        _document(
            user_id=None,
            anon_token="anon-token-abc",
            conversation_id=conversation.id,
        )
    )
    session.commit()
    assert session.query(Document).count() == 1


# --- Cascades ----------------------------------------------------------


def test_deleting_a_document_deletes_its_excerpts() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    document = _document(user_id=user.id, conversation_id=conversation.id)
    session.add(document)
    session.flush()

    session.add(
        DocumentExcerptRow(
            document_id=document.id,
            excerpt_id="doc:abc:p0:0",
            page_number=0,
            ciphertext=b"c",
            nonce=b"n",
            key_id="0" * 16,
            ordinal=0,
        )
    )
    session.commit()

    session.delete(document)
    session.commit()

    assert session.query(DocumentExcerptRow).count() == 0


def test_deleting_a_conversation_deletes_its_documents() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(_document(user_id=user.id, conversation_id=conversation.id))
    session.commit()

    session.delete(conversation)
    session.commit()

    assert session.query(Document).count() == 0


def test_deleting_a_document_leaves_its_access_log_rows_intact() -> None:
    """The audit trail is not a foreign key -- see `DocumentAccessLog`'s
    docstring for why deleting a document must not cascade into it."""
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    document = _document(user_id=user.id, conversation_id=conversation.id)
    session.add(document)
    session.flush()

    session.add(
        DocumentAccessLog(
            document_id=document.id,
            actor_kind="user",
            actor_ref=user.id,
            action="create",
        )
    )
    session.commit()

    session.delete(document)
    session.commit()

    assert session.query(DocumentAccessLog).count() == 1


# --- Uniqueness --------------------------------------------------------


def test_document_excerpt_ids_are_unique_within_a_document() -> None:
    session = _session()
    user = User(email="person@example.com")
    session.add(user)
    session.flush()
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()
    document = _document(user_id=user.id, conversation_id=conversation.id)
    session.add(document)
    session.flush()

    session.add(
        DocumentExcerptRow(
            document_id=document.id,
            excerpt_id="doc:abc:p0:0",
            page_number=0,
            ciphertext=b"c1",
            nonce=b"n1",
            key_id="0" * 16,
            ordinal=0,
        )
    )
    session.commit()

    session.add(
        DocumentExcerptRow(
            document_id=document.id,
            excerpt_id="doc:abc:p0:0",
            page_number=0,
            ciphertext=b"c2",
            nonce=b"n2",
            key_id="0" * 16,
            ordinal=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


# --- Retrieval and retention queryability -----------------------------


def test_expired_anonymous_conversations_are_cheaply_queryable() -> None:
    """`scripts/purge_retention.py` must find expired rows via the
    `expires_at` index, not a full scan -- proven here by exercising the
    exact filtered query it will run."""
    session = _session()
    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=1)
    session.add(Conversation(anon_token="expired", expires_at=past))
    session.add(Conversation(anon_token="still-live", expires_at=future))
    session.commit()

    expired = (
        session.query(Conversation)
        .filter(Conversation.expires_at.is_not(None))
        .filter(Conversation.expires_at < datetime.now(UTC))
        .all()
    )
    assert [row.anon_token for row in expired] == ["expired"]
    assert ("conversations", "expires_at") in {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for index in table.indexes
        for column in index.columns
    }


def test_expired_documents_are_cheaply_queryable() -> None:
    assert ("documents", "expires_at") in {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for index in table.indexes
        for column in index.columns
    }


# --- No plaintext ever stored -------------------------------------------


def test_documents_never_store_plaintext() -> None:
    """`Document` and `DocumentExcerptRow` may only hold ciphertext for any
    field that could carry extracted document content -- `ciphertext` and
    `nonce` are `LargeBinary`; every `Text`/`String` column left over must be
    metadata (filename, content type, ids), never content."""
    metadata_only = {
        "id",
        "document_id",
        "excerpt_id",
        "filename",
        "content_type",
        "crisis_kind",
        "user_id",
        "anon_token",
        "conversation_id",
        "key_id",
    }
    for table_class in (Document, DocumentExcerptRow):
        for column in table_class.__table__.columns:
            if isinstance(column.type, (Text, String)):
                assert column.name in metadata_only, (
                    f"{table_class.__name__}.{column.name} is a "
                    f"{column.type} column not in the metadata allow-list -- "
                    "extracted document content must only ever be stored as "
                    "ciphertext"
                )
            if column.name in ("ciphertext", "nonce"):
                assert isinstance(column.type, LargeBinary)
