"""Shared HTTP-surface fixtures for the conversation and document endpoints.

`tests/api/test_routes.py` keeps its own in-memory `_client` factory
deliberately -- it predates this file and its `StaticPool` single-connection
trick is load-bearing for the anonymous-cookie tests there. The factory here
is file-backed instead (`tmp_path`), for one reason the new endpoints depend
on: Plan 6 Task 15 makes every `document_access_log` write land on its OWN
session, committed independently of the request's transaction, so a denied
cross-owner access still leaves a trace after the request rolls back. Two
genuinely independent sessions cannot be simulated on a `StaticPool` sharing
one DBAPI connection -- a commit on either would commit the other's pending
work -- so a file database is what makes that property testable at all
rather than passing for the wrong reason.

No live Supabase project, Anthropic key, Qdrant or model is reachable from
here: `answer_fn` is always a stub and tokens are minted with a throwaway
secret, exactly as `tests/api/test_auth.py` does.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.answers.schema import Claim, DocumentClaim
from openlaw.answers.validate import DocumentCitation, Source, ValidatedAnswer
from openlaw.api.app import create_app
from openlaw.config import Settings
from openlaw.db.models import Base
from openlaw.language.pipeline import LocalisedAnswer
from openlaw.retrieval.context import ChunkKind

JWT_SECRET = "test-secret-at-least-32-bytes-long!!"
#: A real 32-byte AES key, base64-encoded -- the same throwaway value
#: `tests/documents/test_store.py` uses. Never a production key.
DOCUMENT_KEY = base64.b64encode(b"0" * 32).decode()

TEXT_TYPE = "text/plain"
NOTICE_TEXT = (
    b"Your landlord must refund the security deposit within thirty days of "
    b"vacating the rented premises. The notice to quit is dated 1 March."
)


def api_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings for an HTTP test: uploads on, quota on, plain-HTTP cookies."""
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
        "database_url": f"sqlite:///{tmp_path / 'openlaw.sqlite'}",
        "quota_enabled": True,
        "anonymous_quota": 10,
        "monthly_quota": 10,
        "supabase_jwt_secret": JWT_SECRET,
        # See `tests/api/test_routes.py::_settings` -- TestClient speaks
        # plain HTTP, so a `Secure` cookie would never round-trip.
        "cookie_secure": False,
        "uploads_enabled": True,
        "document_encryption_key": DOCUMENT_KEY,
    }
    return Settings(**{**defaults, **overrides})


def validated_answer(
    claim_text: str = "The deposit must be refunded within thirty days.",
    document_claim_text: str | None = None,
    excerpt_id: str = "doc:d1:p1:0",
    filename: str = "notice.txt",
) -> ValidatedAnswer:
    """A validated answer, optionally carrying one document claim.

    `unverified_summary` is deliberately a loud, recognisable string: every
    rendering test below asserts it never appears in any response body.
    """
    document_claims: tuple[DocumentClaim, ...] = ()
    document_citations: tuple[DocumentCitation, ...] = ()
    if document_claim_text is not None:
        document_claims = (
            DocumentClaim(text=document_claim_text, excerpt_ids=(excerpt_id,)),
        )
        document_citations = (
            DocumentCitation(
                excerpt_id=excerpt_id,
                document_id="d1",
                filename=filename,
                page_number=1,
            ),
        )
    return ValidatedAnswer(
        unverified_summary="UNVERIFIED MODEL PROSE -- MUST NEVER RENDER",
        claims=(Claim(text=claim_text, chunk_ids=("chunk-a",)),),
        sources=(
            Source(
                chunk_id="chunk-a",
                source_url="https://www.indiacode.nic.in/handle/123456789/1",
                title="Model Tenancy Act",
                kind=ChunkKind.PROVISION,
            ),
        ),
        document_claims=document_claims,
        document_citations=document_citations,
        is_answerable=True,
    )


def localised(
    answer: ValidatedAnswer | None = None,
    turn_index: int = 0,
    language: str = "en",
    **extras: object,
) -> LocalisedAnswer:
    resolved = answer if answer is not None else validated_answer()
    return LocalisedAnswer(
        answer=resolved,
        english_answer=resolved,
        language=language,
        english_query="my landlord kept my deposit",
        footer="This is legal information, not legal advice. NALSA: 15100",
        turn_index=turn_index,
        **extras,
    )


class RecordingAnswerFn:
    """A stub `ConversationAnswerFn` that records every keyword it was given.

    Deliberately strict about its signature (no `**kwargs` catch-all on the
    recording path): Plan 5's H1 was exactly a closure that accepted an
    argument and silently dropped it, and a permissive stub is what let that
    ship. `calls` is appended to, never mutated in place by a caller.
    """

    def __init__(self, result: LocalisedAnswer | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        question: str,
        language: str | None,
        state: str | None,
        *,
        history: tuple = (),
        document_context: object = None,
        document_crisis_kinds: tuple[str | None, ...] = (),
    ) -> LocalisedAnswer:
        self.calls.append(
            {
                "question": question,
                "language": language,
                "state": state,
                "history": history,
                "document_context": document_context,
                "document_crisis_kinds": document_crisis_kinds,
            }
        )
        turn = len(self.calls) - 1
        return self._result or localised(turn_index=turn)


def build_client(
    tmp_path: Path,
    settings: Settings | None = None,
    answer_fn: Callable | None = None,
) -> TestClient:
    """Return a TestClient over a file-backed sqlite database.

    `session_factory` is a real `contextmanager` that commits on success and
    rolls back on failure -- the same shape `openlaw.db.session.session_scope`
    gives production -- because the audit-durability property under test is a
    property of transaction boundaries, not of a bare `Session`.
    """
    resolved_settings = settings or api_settings(tmp_path)
    engine = create_engine(resolved_settings.database_url)
    Base.metadata.create_all(engine)

    @contextmanager
    def session_factory() -> Iterator[Session]:
        session = Session(engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return TestClient(
        create_app(answer_fn or RecordingAnswerFn(), session_factory, resolved_settings),
        raise_server_exceptions=False,
    )


def bearer(subject: str = "user-1", email: str = "person@example.com") -> dict[str, str]:
    """Return an `Authorization` header for a freshly minted test token."""
    token = jwt.encode(
        {
            "sub": subject,
            "email": email,
            "aud": "authenticated",
            "role": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def upload(
    client: TestClient,
    content: bytes = NOTICE_TEXT,
    filename: str = "notice.txt",
    content_type: str = TEXT_TYPE,
    conversation_id: str | None = None,
    headers: dict[str, str] | None = None,
):
    """POST one document, returning the raw response."""
    data = {"conversation_id": conversation_id} if conversation_id else None
    return client.post(
        "/documents",
        files={"file": (filename, content, content_type)},
        data=data,
        headers=headers,
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return build_client(tmp_path)
