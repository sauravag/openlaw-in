from datetime import date

import pytest
from pydantic import ValidationError

from openlaw.corpus.schema import CaseMetadata, JudgmentChunk, Provision


def test_judgment_chunk_from_vaquill_row() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is G dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR", "vide"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )

    assert chunk.join_key == "2016INSC1151"
    assert chunk.judges == ("T.S. THAKUR",)          # noise dropped
    assert chunk.decision_date == date(2016, 12, 15)
    assert chunk.corpus_version == "v2026.08"


def test_judgment_chunk_handles_high_court_date_format() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "DLHC010517432014_000",
            "case_id": "DLHC010517432014",
            "chunk_index": 0,
            "text": "petition allowed",
            "text_original": "petition allowed",
            "court_code": "7_26",
            "judges": [],
            "decision_date": "2016-05-31 00:00:00",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )

    assert chunk.join_key == "DLHC010517432014"
    assert chunk.decision_date == date(2016, 5, 31)
    assert chunk.judges == ()


def test_judgment_chunk_is_immutable() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "c1", "case_id": "2016_INSC_1151", "chunk_index": 0,
            "text": "t", "text_original": "t", "court_code": "SCI",
            "judges": [], "decision_date": "2016-12-15", "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    with pytest.raises(ValidationError):
        chunk.text = "mutated"


def test_judgment_chunk_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        JudgmentChunk.from_row(
            {
                "chunk_id": "c1", "case_id": "2016_INSC_1151", "chunk_index": 0,
                "text": "", "text_original": "", "court_code": "SCI",
                "judges": [], "decision_date": "2016-12-15", "language_code": "en",
            },
            corpus_version="v2026.08",
        )


def test_provision_from_vaquill_row_parses_encoded_subjects() -> None:
    provision = Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388",
            "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925",
            "section_number": "388",
            "text": "An order of a District Judge on an appeal ...",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['tax_law', 'family_law']",
            "acts_referenced": ["Indian Succession Act, 1925"],
            "defined_terms": [],
            "source_url": (
                "https://www.indiacode.nic.in/handle/123456789/2385?view_type=browse"
            ),
        },
        corpus_version="v2026.08",
    )

    assert provision.legal_subjects == ("tax_law", "family_law")
    assert provision.acts_referenced == ("Indian Succession Act, 1925",)
    assert provision.source_url.startswith("https://www.indiacode.nic.in/")


def test_provision_requires_a_source_url() -> None:
    """Enforces the core guarantee: no citation without a government URL."""
    with pytest.raises(ValidationError):
        Provision.from_row(
            {
                "chunk_id": "x", "act_id": "IND_central_1", "title": "An Act",
                "section_number": "1", "text": "text", "jurisdiction": "central",
                "in_force": True, "legal_subject": "['general']",
                "acts_referenced": [], "defined_terms": [], "source_url": "",
            },
            corpus_version="v2026.08",
        )


def test_case_metadata_from_kanoongpt_row() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "SCI",
            "neutral_citation": "2019INSC398",
            "cnr_number": "ESCR010005442019",
            "source_pdf_s3_url": (
                "https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/"
                "year=2019/english/english.tar#member=2019_2_1042_1046.pdf"
            ),
            "headnote_text": "Land laws: Resumption of allotted land ...",
            "quality_json": '{"flags":{"has_parser_failed":false},"error_count":0}',
        }
    )

    assert metadata.join_key == "2019INSC398"
    assert metadata.has_parser_errors is False
    assert metadata.headnote is not None


def test_case_metadata_prefers_neutral_citation_then_falls_back_to_cnr() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "10~8",
            "neutral_citation": None,
            "cnr_number": "BRHC010004832019",
            "source_pdf_s3_url": "https://example.gov.in/x.pdf",
            "headnote_text": None,
            "quality_json": "{}",
        }
    )
    assert metadata.join_key == "BRHC010004832019"


def test_case_metadata_detects_parser_failure_flag() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "SCI", "neutral_citation": "2019INSC398",
            "cnr_number": None, "source_pdf_s3_url": None, "headnote_text": None,
            "quality_json": '{"flags":{"has_parser_failed":true},"error_count":3}',
        }
    )
    assert metadata.has_parser_errors is True
