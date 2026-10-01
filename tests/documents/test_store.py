"""Tests for `openlaw.documents.store` (Plan 6 Task 14).

Mirrors `tests/db/test_conversations.py`'s ownership discipline for the one
kind of row more sensitive than conversation text: an uploaded document.
Fixtures use `text/plain` uploads throughout -- `openlaw.documents.extract`
already has its own dedicated PDF/DOCX fixtures (`tests/documents/
test_extract.py`); this file is about persistence and access control, not
parsing.
"""

import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openlaw.config import Settings
from openlaw.db.conversations import AccessDenied, Owner, create_conversation
from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    DocumentExcerptRow,
    User,
)
from openlaw.documents.crypto import key_fingerprint
from openlaw.documents.extract import ExtractionError
from openlaw.documents.store import (
    audit,
    delete_document,
    list_conversation_documents,
    load_document_crisis_kinds,
    load_excerpts,
    store_document,
)

VALID_KEY = base64.b64encode(b"0" * 32).decode()
_TEXT_TYPE = "text/plain"
_NOTICE_TEXT = (
    b"Your landlord must refund the security deposit within thirty days "
    b"of vacating the rented premises."
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "document_encryption_key": VALID_KEY,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _user(session: Session, email: str = "person@example.com") -> User:
    user = User(email=email)
    session.add(user)
    session.flush()
    return user


def _conversation(session: Session, owner: Owner, settings: Settings) -> Conversation:
    return create_conversation(session, owner, settings)


# --- Ownership: the test that matters most ----------------------------------


def test_a_second_subject_cannot_read_another_subjects_document() -> None:
    session = _session()
    owner_a = _user(session, "a@example.com")
    owner_b = _user(session, "b@example.com")
    settings = _settings()
    conversation = _conversation(session, Owner(owner_a.id, None), settings)
    document = store_document(
        session,
        Owner(owner_a.id, None),
        conversation.id,
        "f.txt",
        _TEXT_TYPE,
        _NOTICE_TEXT,
        settings,
    )

    with pytest.raises(AccessDenied):
        load_excerpts(session, (document.id,), Owner(owner_b.id, None), settings)


def test_a_second_anonymous_token_cannot_read_anothers_document() -> None:
    session = _session()
    settings = _settings()
    conversation = _conversation(session, Owner(None, "token-a"), settings)
    document = store_document(
        session,
        Owner(None, "token-a"),
        conversation.id,
        "f.txt",
        _TEXT_TYPE,
        _NOTICE_TEXT,
        settings,
    )

    with pytest.raises(AccessDenied):
        load_excerpts(session, (document.id,), Owner(None, "token-b"), settings)


def test_an_owner_can_read_their_own_document_excerpts() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    excerpts = load_excerpts(session, (document.id,), owner, settings)

    assert len(excerpts) == 1
    assert "security deposit" in excerpts[0].text


def test_load_excerpts_with_no_document_ids_returns_empty_and_audits_nothing() -> None:
    session = _session()
    user = _user(session)
    settings = _settings()

    assert load_excerpts(session, (), Owner(user.id, None), settings) == ()
    assert session.query(DocumentAccessLog).count() == 0


def test_store_document_raises_access_denied_for_a_mismatched_conversation() -> None:
    session = _session()
    owner_a = _user(session, "a@example.com")
    owner_b = _user(session, "b@example.com")
    settings = _settings()
    conversation = _conversation(session, Owner(owner_a.id, None), settings)

    with pytest.raises(AccessDenied):
        store_document(
            session,
            Owner(owner_b.id, None),
            conversation.id,
            "f.txt",
            _TEXT_TYPE,
            _NOTICE_TEXT,
            settings,
        )
    assert session.query(Document).count() == 0


# --- Anonymous rows expire; authenticated rows do not -----------------------


def test_store_document_sets_an_expiry_for_an_anonymous_owner() -> None:
    session = _session()
    settings = _settings(anonymous_upload_ttl_hours=24)
    conversation = _conversation(session, Owner(None, "anon-tok"), settings)

    document = store_document(
        session,
        Owner(None, "anon-tok"),
        conversation.id,
        "f.txt",
        _TEXT_TYPE,
        _NOTICE_TEXT,
        settings,
    )

    assert document.expires_at is not None


def test_store_document_sets_no_expiry_for_an_authenticated_owner() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)

    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert document.expires_at is None


# --- key_id is never optional ------------------------------------------------


def test_store_document_always_sets_a_real_key_id_matching_the_configured_key() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    key = base64.b64decode(VALID_KEY)

    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert document.key_id == key_fingerprint(key)
    excerpt_row = (
        session.query(DocumentExcerptRow).filter_by(document_id=document.id).one()
    )
    assert excerpt_row.key_id == key_fingerprint(key)


def test_a_document_row_cannot_be_written_without_a_key_id() -> None:
    """Bypassing `store_document` entirely and writing the ORM row by hand
    must still fail -- `key_id` is `nullable=False` at the schema level
    (Task 3b), so there is no way to persist a placeholder or omitted value."""
    session = _session()
    user = _user(session)
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    session.flush()

    session.add(
        Document(
            user_id=user.id,
            conversation_id=conversation.id,
            filename="f.txt",
            content_type=_TEXT_TYPE,
            byte_size=1,
            page_count=1,
            ciphertext=b"c",
            nonce=b"n",
            # key_id deliberately omitted
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_store_document_propagates_extraction_errors_without_writing_anything() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)

    with pytest.raises(ExtractionError):
        store_document(
            session,
            owner,
            conversation.id,
            "f.jpg",
            "image/jpeg",
            b"not-a-real-image",
            settings,
        )
    assert session.query(Document).count() == 0


# --- Cascades ----------------------------------------------------------


def test_deleting_a_document_cascades_its_excerpts() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )
    assert (
        session.query(DocumentExcerptRow).filter_by(document_id=document.id).count() == 1
    )

    assert delete_document(session, document.id, owner) is True

    assert (
        session.query(DocumentExcerptRow).filter_by(document_id=document.id).count() == 0
    )
    assert session.query(Document).count() == 0


def test_delete_document_returns_false_for_a_mismatched_owner() -> None:
    session = _session()
    owner_a = _user(session, "a@example.com")
    owner_b = _user(session, "b@example.com")
    settings = _settings()
    conversation = _conversation(session, Owner(owner_a.id, None), settings)
    document = store_document(
        session,
        Owner(owner_a.id, None),
        conversation.id,
        "f.txt",
        _TEXT_TYPE,
        _NOTICE_TEXT,
        settings,
    )

    assert delete_document(session, document.id, Owner(owner_b.id, None)) is False
    assert session.query(Document).count() == 1


def test_delete_document_returns_false_for_a_nonexistent_id() -> None:
    session = _session()
    user = _user(session)
    assert delete_document(session, "does-not-exist", Owner(user.id, None)) is False


# --- The access log survives its document's deletion ------------------------


def test_the_access_log_survives_the_documents_deletion() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )
    load_excerpts(session, (document.id,), owner, settings)

    delete_document(session, document.id, owner)

    # The document row is gone...
    assert session.query(Document).filter_by(id=document.id).count() == 0
    # ...but every access log row naming it is untouched.
    log_rows = (
        session.query(DocumentAccessLog)
        .filter_by(document_id=document.id)
        .order_by(DocumentAccessLog.created_at)
        .all()
    )
    actions = [row.action for row in log_rows]
    assert actions == ["create", "read", "delete"]


def test_a_denied_read_attempt_is_still_audited() -> None:
    session = _session()
    owner_a = _user(session, "a@example.com")
    owner_b = _user(session, "b@example.com")
    settings = _settings()
    conversation = _conversation(session, Owner(owner_a.id, None), settings)
    document = store_document(
        session,
        Owner(owner_a.id, None),
        conversation.id,
        "f.txt",
        _TEXT_TYPE,
        _NOTICE_TEXT,
        settings,
    )

    with pytest.raises(AccessDenied):
        load_excerpts(session, (document.id,), Owner(owner_b.id, None), settings)

    denied_rows = (
        session.query(DocumentAccessLog)
        .filter_by(document_id=document.id, action="denied")
        .all()
    )
    assert len(denied_rows) == 1
    assert denied_rows[0].actor_ref == owner_b.id


def test_audit_records_anonymous_actors_distinctly_from_signed_in_ones() -> None:
    session = _session()
    audit(session, "doc-1", Owner(None, "anon-tok"), "read")

    row = session.query(DocumentAccessLog).filter_by(document_id="doc-1").one()
    assert row.actor_kind == "anon"
    assert row.actor_ref == "anon-tok"


# --- Listing a conversation's documents (Plan 6 Task 18) --------------------


def test_listing_returns_metadata_with_no_content_field_at_all() -> None:
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    document = store_document(
        session, owner, conversation.id, "lease.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    listed = list_conversation_documents(session, conversation.id, owner)

    assert len(listed) == 1
    assert listed[0].id == document.id
    assert listed[0].filename == "lease.txt"
    assert listed[0].page_count == document.page_count
    # The type itself cannot carry plaintext, ciphertext, nonce or key id --
    # that is the protection, not a convention at the call site.
    assert set(vars(listed[0])) == {"id", "filename", "page_count", "expires_at"}


def test_listing_never_writes_an_audit_row() -> None:
    """`document_access_log` records reads of CONTENT. A metadata listing
    decrypts nothing, and logging it as a read would dilute the trail."""
    session = _session()
    user = _user(session)
    owner = Owner(user.id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )
    session.query(DocumentAccessLog).delete()

    list_conversation_documents(session, conversation.id, owner)

    assert session.query(DocumentAccessLog).count() == 0


def test_listing_a_conversation_another_subject_owns_is_empty() -> None:
    session = _session()
    owner_a = Owner(_user(session, "a@example.com").id, None)
    owner_b = Owner(_user(session, "b@example.com").id, None)
    settings = _settings()
    conversation = _conversation(session, owner_a, settings)
    store_document(
        session, owner_a, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert list_conversation_documents(session, conversation.id, owner_b) == ()


def test_listing_a_conversation_another_anonymous_token_owns_is_empty() -> None:
    session = _session()
    settings = _settings()
    owner_a = Owner(None, "token-a")
    conversation = _conversation(session, owner_a, settings)
    store_document(
        session, owner_a, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert list_conversation_documents(session, conversation.id, Owner(None, "token-b")) == ()


def test_listing_an_unknown_conversation_is_empty() -> None:
    session = _session()
    owner = Owner(_user(session).id, None)

    assert list_conversation_documents(session, "no-such-conversation", owner) == ()


def test_listing_carries_the_anonymous_expiry_and_omits_it_when_authenticated() -> None:
    session = _session()
    settings = _settings()
    anonymous = Owner(None, "token-a")
    anon_conversation = _conversation(session, anonymous, settings)
    store_document(
        session, anonymous, anon_conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )
    signed_in = Owner(_user(session).id, None)
    signed_in_conversation = _conversation(session, signed_in, settings)
    store_document(
        session, signed_in, signed_in_conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert list_conversation_documents(session, anon_conversation.id, anonymous)[0].expires_at
    assert (
        list_conversation_documents(session, signed_in_conversation.id, signed_in)[0].expires_at
        is None
    )


def test_a_deleted_document_leaves_the_listing() -> None:
    session = _session()
    owner = Owner(_user(session).id, None)
    settings = _settings()
    conversation = _conversation(session, owner, settings)
    first = store_document(
        session, owner, conversation.id, "a.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )
    second = store_document(
        session, owner, conversation.id, "b.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    assert delete_document(session, first.id, owner) is True

    assert [entry.id for entry in list_conversation_documents(session, conversation.id, owner)] == [
        second.id
    ]


# --- LOW-2: verdicts without text, for the uploads kill switch --------------


def test_load_document_crisis_kinds_of_no_documents_is_empty() -> None:
    assert load_document_crisis_kinds(_session(), (), Owner(None, "t")) == ()


def test_load_document_crisis_kinds_returns_the_stored_verdict_only() -> None:
    """With uploads disabled the route reads verdicts and no text. This needs
    no `Settings` at all, which is the structural half of that: a function
    that takes no keyring cannot decrypt an excerpt."""
    session = _session()
    settings = _settings()
    owner = Owner(None, "token-a")
    conversation = _conversation(session, owner, settings)
    order = b"FIR copy: the complainant states that her husband beats her daily."
    document = store_document(
        session, owner, conversation.id, "fir.txt", _TEXT_TYPE, order, settings
    )

    kinds = load_document_crisis_kinds(session, (document.id,), owner)

    assert kinds == ("domestic_violence",)


def test_load_document_crisis_kinds_refuses_another_owners_document() -> None:
    session = _session()
    settings = _settings()
    owner = Owner(None, "token-a")
    conversation = _conversation(session, owner, settings)
    document = store_document(
        session, owner, conversation.id, "f.txt", _TEXT_TYPE, _NOTICE_TEXT, settings
    )

    with pytest.raises(AccessDenied):
        load_document_crisis_kinds(session, (document.id,), Owner(None, "token-b"))
