"""Tests for `scripts/purge_retention.py` (final whole-plan review, H5; Plan 6
Task 16 extends it to `documents`, `conversations`, and
`document_access_log`).

`purge_stale_events` and `purge_stale_hallucination_log` were both correct,
unit-tested functions that nothing shipped ever called -- a written 90-day
retention promise (README, and both functions' own docstrings) with no
mechanism behind it. This is the boundary test for the fix: a stale,
verbatim-sensitive event planted in EITHER JSONL log or the database table
must not survive one run of this script, and a fresh one must.

Only sqlite is used here (`OPENLAW_DATABASE_URL=sqlite:///...`) -- no
Postgres connection is opened by this suite, consistent with this
environment's operational constraints.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openlaw.answers.crisis_classifier import (
    ClassifierFailureEvent,
    append_classifier_failure_events,
)
from openlaw.answers.escalation import HallucinationEvent, append_events
from openlaw.config import get_settings
from openlaw.db.models import (
    Base,
    Conversation,
    Document,
    DocumentAccessLog,
    HallucinationLog,
)
from scripts.purge_retention import main, run_purge


def _stale_timestamp() -> str:
    return (datetime.now(UTC) - timedelta(days=91)).isoformat()


def _fresh_timestamp() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def _configure(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point every retention-bound store at a throwaway location and clear
    the cached `Settings` singleton so this test's env vars actually apply."""
    paths = {
        "hallucination": tmp_path / "hallucinations.jsonl",
        "classifier": tmp_path / "classifier_failures.jsonl",
        "db": tmp_path / "app.db",
    }
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", str(tmp_path / "v"))
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", str(tmp_path / "k"))
    monkeypatch.setenv("OPENLAW_DATABASE_URL", f"sqlite:///{paths['db']}")
    monkeypatch.setenv("OPENLAW_HALLUCINATION_LOG_PATH", str(paths["hallucination"]))
    monkeypatch.setenv(
        "OPENLAW_CLASSIFIER_FAILURE_LOG_PATH", str(paths["classifier"])
    )
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{paths['db']}")
    Base.metadata.create_all(engine)
    return paths


def test_run_purge_removes_stale_events_and_keeps_fresh_ones(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _configure(monkeypatch, tmp_path)

    append_events(
        (
            HallucinationEvent(
                question="q-old",
                dropped_chunk_ids=("a",),
                retrieved_chunk_ids=("a", "b"),
                claim_text="stale claim",
                timestamp=_stale_timestamp(),
            ),
            HallucinationEvent(
                question="q-new",
                dropped_chunk_ids=("a",),
                retrieved_chunk_ids=("a", "b"),
                claim_text="fresh claim",
                timestamp=_fresh_timestamp(),
            ),
        ),
        paths["hallucination"],
    )
    append_classifier_failure_events(
        (
            ClassifierFailureEvent(
                question="q-old",
                error="TimeoutError: x",
                timestamp=_stale_timestamp(),
            ),
        ),
        paths["classifier"],
    )

    engine = create_engine(f"sqlite:///{paths['db']}")
    with Session(engine) as session:
        session.add(
            HallucinationLog(
                question="q-old",
                claim_text="stale row",
                dropped_chunk_ids=[],
                retrieved_chunk_ids=[],
                created_at=datetime.now(UTC) - timedelta(days=91),
            )
        )
        session.commit()

    run_purge()

    remaining = paths["hallucination"].read_text(encoding="utf-8").splitlines()
    assert len(remaining) == 1
    assert "q-new" in remaining[0]
    assert "q-old" not in paths["hallucination"].read_text(encoding="utf-8")

    assert paths["classifier"].read_text(encoding="utf-8").strip() == ""

    with Session(engine) as session:
        assert session.query(HallucinationLog).count() == 0


def test_run_purge_is_a_no_op_when_nothing_is_stale(
    monkeypatch, tmp_path: Path
) -> None:
    """A fresh self-host with no logs yet must not error."""
    _configure(monkeypatch, tmp_path)
    run_purge()  # must not raise


def test_main_returns_zero_on_success(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    assert main() == 0


def test_main_returns_one_and_reports_failure_on_stderr(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """The purge command must fail loudly (non-zero exit, message on stderr),
    never silently, e.g. when the database is unreachable -- a cron job that
    always exits 0 would hide exactly the outage an operator needs to see."""
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "OPENLAW_DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x"
    )
    get_settings.cache_clear()

    assert main() == 1
    assert "FAILED" in capsys.readouterr().err


# --- Plan 6 Task 16: documents, conversations, document_access_log ---------


def _expired() -> datetime:
    return datetime.now(UTC) - timedelta(hours=1)


def _document(conversation_id: str, **overrides: object) -> Document:
    defaults: dict = {
        "conversation_id": conversation_id,
        "filename": "notice.pdf",
        "content_type": "application/pdf",
        "byte_size": 1024,
        "page_count": 1,
        "ciphertext": b"cipher",
        "nonce": b"nonce-bytes-",
        "key_id": "0" * 16,
    }
    defaults.update(overrides)
    return Document(**defaults)


def test_run_purge_removes_expired_anonymous_documents_and_keeps_authenticated_ones(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _configure(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{paths['db']}")

    with Session(engine) as session:
        anon_conversation = Conversation(anon_token="anon-1")
        auth_conversation = Conversation(user_id="user-1")
        session.add_all([anon_conversation, auth_conversation])
        session.flush()
        expired_document = _document(
            anon_conversation.id, anon_token="anon-1", expires_at=_expired()
        )
        session.add(expired_document)
        session.add(_document(auth_conversation.id, user_id="user-1", expires_at=None))
        session.flush()
        session.add(
            DocumentAccessLog(
                document_id=expired_document.id,
                actor_kind="anon",
                actor_ref="anon-1",
                action="create",
            )
        )
        session.commit()

    run_purge()

    with Session(engine) as session:
        remaining = session.query(Document).all()
        assert len(remaining) == 1
        assert remaining[0].user_id == "user-1"
        # The expired document's audit trail must outlive it.
        assert session.query(DocumentAccessLog).count() == 1


def test_run_purge_removes_an_expired_anonymous_conversation_and_its_documents(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _configure(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{paths['db']}")

    with Session(engine) as session:
        conversation = Conversation(anon_token="anon-1", expires_at=_expired())
        session.add(conversation)
        session.flush()
        session.add(_document(conversation.id, anon_token="anon-1", expires_at=None))
        session.commit()

    run_purge()

    with Session(engine) as session:
        assert session.query(Conversation).count() == 0
        assert session.query(Document).count() == 0


def test_run_purge_is_idempotent_when_run_twice_in_a_row(
    monkeypatch, tmp_path: Path
) -> None:
    """A cron that overlaps itself must not error or double-count."""
    paths = _configure(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{paths['db']}")

    with Session(engine) as session:
        session.add(Conversation(anon_token="anon-1", expires_at=_expired()))
        session.commit()

    run_purge()
    run_purge()  # must not raise or re-delete anything

    with Session(engine) as session:
        assert session.query(Conversation).count() == 0


def test_dry_run_changes_nothing(monkeypatch, tmp_path: Path, capsys) -> None:
    paths = _configure(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{paths['db']}")

    with Session(engine) as session:
        conversation = Conversation(anon_token="anon-1", expires_at=_expired())
        session.add(conversation)
        session.flush()
        session.add(
            _document(conversation.id, anon_token="anon-1", expires_at=_expired())
        )
        session.commit()

    append_events(
        (
            HallucinationEvent(
                question="q-old",
                dropped_chunk_ids=("a",),
                retrieved_chunk_ids=("a", "b"),
                claim_text="stale claim",
                timestamp=_stale_timestamp(),
            ),
        ),
        paths["hallucination"],
    )
    before = paths["hallucination"].read_text(encoding="utf-8")

    run_purge(dry_run=True)

    assert paths["hallucination"].read_text(encoding="utf-8") == before
    with Session(engine) as session:
        assert session.query(Conversation).count() == 1
        assert session.query(Document).count() == 1

    assert "dry-run" in capsys.readouterr().out


def test_main_supports_the_dry_run_flag(monkeypatch, tmp_path: Path) -> None:
    paths = _configure(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{paths['db']}")
    with Session(engine) as session:
        session.add(Conversation(anon_token="anon-1", expires_at=_expired()))
        session.commit()

    assert main(["--dry-run"]) == 0

    with Session(engine) as session:
        assert session.query(Conversation).count() == 1
