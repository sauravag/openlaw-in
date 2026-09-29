"""Task 1: every constant in `openlaw.documents` is a named value with an
exact, pinned default -- not a magic number re-derived somewhere else."""

from openlaw.documents import (
    ALLOWED_CONTENT_TYPES,
    DOCUMENT_EXCERPT_PREFIX,
    MAX_CARRIED_CHUNKS,
    MAX_DOCUMENT_CONTEXT_CHARS,
    MAX_DOCUMENT_EXCERPTS,
    MAX_EXCERPT_CHARS,
    MAX_HISTORY_TURNS,
    PAGES_PER_QUOTA_UNIT,
)


def test_allowed_content_types_matches_the_pinned_set() -> None:
    assert ALLOWED_CONTENT_TYPES == frozenset(
        {
            "application/pdf",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )


def test_allowed_content_types_excludes_images() -> None:
    """No OCR in this plan (see Task 4) -- an image-only upload must be
    refused, not silently accepted."""
    assert "image/jpeg" not in ALLOWED_CONTENT_TYPES
    assert "image/png" not in ALLOWED_CONTENT_TYPES


def test_document_excerpt_prefix_is_pinned() -> None:
    assert DOCUMENT_EXCERPT_PREFIX == "doc:"


def test_max_excerpt_chars_is_pinned() -> None:
    assert MAX_EXCERPT_CHARS == 1_200


def test_max_document_excerpts_is_pinned() -> None:
    assert MAX_DOCUMENT_EXCERPTS == 6


def test_max_document_context_chars_is_pinned() -> None:
    assert MAX_DOCUMENT_CONTEXT_CHARS == 12_000


def test_max_history_turns_is_pinned() -> None:
    assert MAX_HISTORY_TURNS == 6


def test_max_carried_chunks_is_pinned() -> None:
    assert MAX_CARRIED_CHUNKS == 8


def test_pages_per_quota_unit_is_pinned() -> None:
    assert PAGES_PER_QUOTA_UNIT == 5
