import pytest

from openlaw.corpus.subjects import SUBSTANTIVE_SUBJECTS, parse_legal_subject


def test_parses_string_encoded_list() -> None:
    """legal_subject arrives as a string that looks like a list, not a list."""
    assert parse_legal_subject("['tax_law', 'family_law']") == (
        "tax_law",
        "family_law",
    )


def test_accepts_a_real_list_unchanged() -> None:
    assert parse_legal_subject(["criminal_law"]) == ("criminal_law",)


def test_parses_single_general_tag() -> None:
    assert parse_legal_subject("['general']") == ("general",)


@pytest.mark.parametrize("raw", [None, "", "   ", "[]"])
def test_returns_empty_tuple_when_absent(raw: str | None) -> None:
    assert parse_legal_subject(raw) == ()


def test_malformed_input_is_treated_as_a_single_tag_not_characters() -> None:
    """Guards the bug where a string is iterated character by character."""
    assert parse_legal_subject("tax_law") == ("tax_law",)


def test_general_is_not_a_substantive_subject() -> None:
    """336,224 of 544,078 provisions are tagged 'general' — useless for routing."""
    assert "general" not in SUBSTANTIVE_SUBJECTS
    assert "family_law" in SUBSTANTIVE_SUBJECTS


def test_consumer_law_is_absent_from_the_corpus_vocabulary() -> None:
    """Documents a known gap: no consumer-law tag exists (spec 3.4.5)."""
    assert "consumer_law" not in SUBSTANTIVE_SUBJECTS
