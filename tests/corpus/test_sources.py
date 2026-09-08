import pytest

from openlaw.corpus.join import MetadataIndex
from openlaw.corpus.schema import CaseMetadata, JudgmentChunk, Provision
from openlaw.corpus.sources import (
    CitationUnresolvable,
    require_url,
    resolve_judgment_url,
    resolve_provision_url,
)

PDF_URL = (
    "https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/"
    "year=2019/english/english.tar#member=2019_2_1042_1046.pdf"
)


def _chunk(case_id: str = "2019_INSC_398") -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": f"{case_id}_000", "case_id": case_id, "chunk_index": 0,
            "text": "text", "text_original": "text", "court_code": "SCI",
            "judges": [], "decision_date": "2019-03-26", "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def test_resolves_judgment_url_through_the_join() -> None:
    index = MetadataIndex(
        {
            "2019INSC398": CaseMetadata(
                join_key="2019INSC398", court_code="SCI", source_pdf_url=PDF_URL
            )
        }
    )
    assert resolve_judgment_url(_chunk(), index) == PDF_URL


def test_returns_none_when_the_case_is_not_in_the_index() -> None:
    assert resolve_judgment_url(_chunk(), MetadataIndex({})) is None


def test_returns_none_when_the_matched_record_has_no_pdf_url() -> None:
    index = MetadataIndex(
        {
            "2019INSC398": CaseMetadata(
                join_key="2019INSC398", court_code="SCI", source_pdf_url=None
            )
        }
    )
    assert resolve_judgment_url(_chunk(), index) is None


def test_resolves_provision_url_from_its_own_field() -> None:
    provision = Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388", "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925", "section_number": "388",
            "text": "text", "jurisdiction": "central", "in_force": True,
            "legal_subject": "['family_law']", "acts_referenced": [],
            "defined_terms": [],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )
    assert resolve_provision_url(provision) == (
        "https://www.indiacode.nic.in/handle/123456789/2385"
    )


def test_require_url_returns_the_url_when_present() -> None:
    assert require_url(PDF_URL, "chunk-1") == PDF_URL


@pytest.mark.parametrize("url", [None, "", "   "])
def test_require_url_raises_when_absent(url: str | None) -> None:
    """The core guarantee: an unresolvable citation must stop indexing."""
    with pytest.raises(CitationUnresolvable) as caught:
        require_url(url, "chunk-1")
    assert "chunk-1" in str(caught.value)
