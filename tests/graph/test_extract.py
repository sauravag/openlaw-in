from openlaw.graph.extract import (
    SectionReference,
    citation_key_for,
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


def test_extracts_section_via_lowercase_s_abbreviation() -> None:
    refs = extract_section_references("liable under s. 138")
    assert refs[0].number == "138"


def test_extracts_section_via_uppercase_s_abbreviation() -> None:
    refs = extract_section_references("liable under S. 138")
    assert refs[0].number == "138"


def test_extracts_section_via_ss_abbreviation_lowercase() -> None:
    refs = extract_section_references("punishable under ss. 34 and 149")
    numbers = {ref.number for ref in refs}
    assert numbers == {"34", "149"}


def test_extracts_section_via_ss_abbreviation_uppercase() -> None:
    refs = extract_section_references("charged under Ss. 302 and 149 IPC")
    numbers = {ref.number for ref in refs}
    assert "302" in numbers
    assert "149" in numbers


def test_extracts_section_via_secs_abbreviation() -> None:
    refs = extract_section_references("liable under Secs. 34 and 149")
    numbers = {ref.number for ref in refs}
    assert numbers == {"34", "149"}


def test_does_not_extract_rupees_abbreviation_as_section() -> None:
    assert extract_section_references("the fine of Rs. 500 was imposed") == ()


def test_does_not_extract_exhibits_abbreviation_as_section() -> None:
    assert extract_section_references("marked as Exs. P1 in evidence") == ()


def test_does_not_extract_messrs_abbreviation_as_section() -> None:
    assert extract_section_references("notice issued to Messrs. Kumar") == ()


def test_does_not_extract_messrs_abbreviation_even_when_a_number_follows() -> None:
    """Stronger form of the Messrs. check: a digit right after must not help."""
    text = "notice issued to Messrs. 138 & Sons, the respondents"
    assert extract_section_references(text) == ()


def test_does_not_extract_number_across_sentence_boundary() -> None:
    text = "he counted the words. 138 of them were relevant"
    assert extract_section_references(text) == ()


# --- Task 2 whole-plan review: two verified-unmatched real-world forms ---------


def test_section_reference_matches_the_u_slash_s_abbreviation() -> None:
    """'u/s 302' (under section 302) is extremely common in Indian judgments
    and was matched by nothing before — the period-less form is the point."""
    assert extract_section_references("convicted u/s 302 IPC") == (
        SectionReference(number="302", act_hint=None),
    )


def test_section_reference_matches_the_plural_u_slash_ss_abbreviation() -> None:
    numbers = [ref.number for ref in extract_section_references("charged u/ss 302 and 34")]
    assert numbers == ["302", "34"]


def test_section_reference_matches_u_slash_s_with_a_trailing_period() -> None:
    assert [ref.number for ref in extract_section_references("u/s. 138")] == ["138"]


def test_u_slash_s_still_carries_an_act_hint_when_the_text_gives_one() -> None:
    refs = extract_section_references("u/s 138 of the Negotiable Instruments Act")
    assert refs == (SectionReference(number="138", act_hint="Negotiable Instruments Act"),)


def test_a_bare_slash_s_is_not_a_section_reference() -> None:
    """'M/s 302' (messieurs) must not become a section reference."""
    assert extract_section_references("M/s 302 Traders Pvt Ltd") == ()


def test_scc_citation_matches_the_parenthesised_volume_form() -> None:
    """'1979 (2) SCC 82' — the year-first, bracketed-volume form. SCC was 166
    of 629 citations in a real 400-chunk sample, so a missed form costs real
    PageRank signal."""
    assert extract_case_citations("see 1979 (2) SCC 82") == ("1979SCC82",)


def test_scc_citation_still_matches_the_leading_bracket_form() -> None:
    assert extract_case_citations("(2016) 8 SCC 984") == ("2016SCC984",)


def test_scc_citation_still_matches_the_bare_form() -> None:
    assert extract_case_citations("2016 SCC 984") == ("2016SCC984",)


def test_both_scc_spellings_of_one_reference_collapse_to_one_key() -> None:
    """The whole point of normalisation: the printed forms of one reference
    must produce a single node key, not two."""
    assert extract_case_citations("1979 (2) SCC 82 and (1979) 2 SCC 82") == (
        "1979SCC82",
    )


# --- a judgment's own citation, normalised the same way ----------------------


def test_citation_key_for_extracts_the_normalised_reporter_citation() -> None:
    """Case 1979_INSC_59's own citation '[1979] 3 S.C.R. 453' must normalise
    to '1979SCR453' — the same form CITES edges from other judgments target."""
    assert citation_key_for("[1979] 3 S.C.R. 453") == "1979SCR453"


def test_citation_key_for_none_when_citation_is_absent() -> None:
    assert citation_key_for(None) is None


def test_citation_key_for_none_when_citation_does_not_match_a_known_reporter() -> None:
    assert citation_key_for("an unparseable citation string") is None
