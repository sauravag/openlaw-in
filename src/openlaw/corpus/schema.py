"""Validated record models — the boundary where bad rows are rejected.

Every model is frozen: records are created, never mutated. `from_row` classmethods
adapt raw Parquet dictionaries, applying the normalisation and quality filters.
"""

import json
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openlaw.corpus.dates import parse_decision_date
from openlaw.corpus.identifiers import normalise_identifier
from openlaw.corpus.quality import clean_judge_names
from openlaw.corpus.subjects import parse_legal_subject


def _as_tuple(value: object) -> tuple[str, ...]:
    """Return `value` as a tuple of strings, treating a bare string as absent.

    Guards against a malformed row where a list column arrives as a string:
    iterating it would silently yield one entry per character.
    """
    if not value or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _as_int(value: object, default: int = 0) -> int:
    """Return `value` coerced to an int, falling back to `default` on failure.

    A malformed chunk index should not raise before pydantic gets a chance to
    validate the row — that would abort an ingest of hundreds of thousands of
    rows on a single bad value instead of letting the row fail closed.
    """
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def _has_parser_errors(quality_json: str | None) -> bool:
    """Return True when KanoonGPT's quality payload reports a parser failure."""
    if not quality_json:
        return False
    try:
        quality = json.loads(quality_json)
    except (ValueError, TypeError):
        return True
    flags = quality.get("flags", {})
    return bool(
        flags.get("has_parser_failed")
        or flags.get("has_errors")
        or int(quality.get("error_count") or 0) > 0
    )


class CaseMetadata(BaseModel):
    """Judgment-level metadata from KanoonGPT — the source of citation URLs."""

    model_config = ConfigDict(frozen=True)

    join_key: str = Field(min_length=1)
    court_code: str
    neutral_citation: str | None = None
    cnr_number: str | None = None
    source_pdf_url: str | None = None
    headnote: str | None = None
    has_parser_errors: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CaseMetadata":
        """Build metadata from a raw KanoonGPT Parquet row.

        The join key prefers the neutral citation (Supreme Court) and falls back
        to the CNR (High Courts).
        """
        neutral = normalise_identifier(row.get("neutral_citation"))
        cnr = normalise_identifier(row.get("cnr_number"))
        return cls(
            join_key=neutral or cnr,
            court_code=str(row.get("court_code") or ""),
            neutral_citation=neutral or None,
            cnr_number=cnr or None,
            source_pdf_url=row.get("source_pdf_s3_url") or None,
            headnote=row.get("headnote_text") or None,
            has_parser_errors=_has_parser_errors(row.get("quality_json")),
        )


class JudgmentChunk(BaseModel):
    """One chunk of a judgment from vaquill, ready for indexing."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    join_key: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_original: str
    court_code: str
    judges: tuple[str, ...] = ()
    decision_date: date | None = None
    language_code: str
    corpus_version: str

    @classmethod
    def from_row(cls, row: dict[str, Any], corpus_version: str) -> "JudgmentChunk":
        """Build a chunk from a raw vaquill Parquet row."""
        case_id = str(row.get("case_id") or "")
        return cls(
            chunk_id=str(row.get("chunk_id") or "").strip(),
            case_id=case_id,
            join_key=normalise_identifier(case_id),
            chunk_index=_as_int(row.get("chunk_index")),
            text=str(row.get("text") or "").strip(),
            text_original=str(row.get("text_original") or row.get("text") or ""),
            court_code=str(row.get("court_code") or ""),
            judges=clean_judge_names(_as_tuple(row.get("judges"))),
            decision_date=parse_decision_date(row.get("decision_date")),
            language_code=str(row.get("language_code") or ""),
            corpus_version=corpus_version,
        )


class Provision(BaseModel):
    """One statutory or regulatory provision from vaquill."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    act_id: str = Field(min_length=1)
    title: str
    section_number: str
    text: str = Field(min_length=1)
    jurisdiction: str
    in_force: bool
    legal_subjects: tuple[str, ...] = ()
    acts_referenced: tuple[str, ...] = ()
    defined_terms: tuple[str, ...] = ()
    source_url: str = Field(min_length=1)
    corpus_version: str

    @classmethod
    def from_row(cls, row: dict[str, Any], corpus_version: str) -> "Provision":
        """Build a provision from a raw vaquill Parquet row.

        `source_url` has `min_length=1`, so a provision without a government URL
        fails validation rather than being indexed.
        """
        return cls(
            chunk_id=str(row.get("chunk_id") or "").strip(),
            act_id=str(row.get("act_id") or "").strip(),
            title=str(row.get("title") or ""),
            section_number=str(row.get("section_number") or ""),
            text=str(row.get("text") or "").strip(),
            jurisdiction=str(row.get("jurisdiction") or ""),
            in_force=bool(row.get("in_force")),
            legal_subjects=parse_legal_subject(row.get("legal_subject")),
            acts_referenced=_as_tuple(row.get("acts_referenced")),
            defined_terms=_as_tuple(row.get("defined_terms")),
            source_url=str(row.get("source_url") or "").strip(),
            corpus_version=corpus_version,
        )
