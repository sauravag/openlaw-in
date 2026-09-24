from openlaw.graph.extract import (
    SectionReference,
    extract_case_citations,
    extract_section_references,
)


def test_extracts_supreme_court_reports_citation() -> None:
    """The SCR form seen throughout the corpus, e.g. '[1979] 3 S.C.R. 453'."""
    text = "Case: UNION OF INDIA versus K. G. KHOSLA [[1979] 3 S.C.R. 453] (1979)"
    assert "1979SCR453" in extract_case_citations(text)


def test_extracts_neutral_citation() -> None:
    assert "2019INSC398" in extract_case_citations("as held in 2019 INSC 398")


def test_extracts_scc_citation() -> None:
    assert "2016SCC984" in extract_case_citations("reported at (2016) 8 SCC 984")


def test_extracts_air_citation() -> None:
    assert "1979AIR123" in extract_case_citations("see AIR 1979 SC 123")


def test_deduplicates_repeated_citations() -> None:
    text = "see [1979] 3 S.C.R. 453 and again [1979] 3 S.C.R. 453"
    assert extract_case_citations(text).count("1979SCR453") == 1


def test_preserves_first_appearance_order() -> None:
    text = "first 2019 INSC 398 then AIR 1979 SC 123"
    citations = extract_case_citations(text)
    assert citations.index("2019INSC398") < citations.index("1979AIR123")


def test_returns_empty_when_no_citation_present() -> None:
    assert extract_case_citations("the appeal is dismissed with costs") == ()


def test_handles_empty_text() -> None:
    assert extract_case_citations("") == ()


def test_extracts_single_section_reference() -> None:
    refs = extract_section_references("punishable under Section 302 of the Code")
    assert SectionReference(number="302", act_hint=None) in refs


def test_extracts_section_with_letter_suffix() -> None:
    refs = extract_section_references("an offence under Section 304B IPC")
    assert refs[0].number == "304B"


def test_captures_the_act_hint_when_named() -> None:
    refs = extract_section_references(
        "under Section 138 of the Negotiable Instruments Act"
    )
    assert refs[0].act_hint is not None
    assert "Negotiable Instruments" in refs[0].act_hint


def test_extracts_plural_section_references() -> None:
    refs = extract_section_references("under Sections 138 and 141")
    numbers = {ref.number for ref in refs}
    assert "138" in numbers
    assert "141" in numbers


def test_deduplicates_repeated_section_references() -> None:
    refs = extract_section_references("Section 302 ... again Section 302")
    assert len([ref for ref in refs if ref.number == "302"]) == 1


def test_returns_empty_when_no_section_reference() -> None:
    assert extract_section_references("the appeal is dismissed") == ()


def test_section_references_handles_empty_text() -> None:
    assert extract_section_references("") == ()
