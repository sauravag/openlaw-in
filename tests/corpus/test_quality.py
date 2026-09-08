import pytest

from openlaw.corpus.quality import (
    clean_judge_names,
    is_plausible_judge_name,
    scrub_margin_letters,
)


@pytest.mark.parametrize(
    "name",
    ["Y.V. CHANDRACHUD", "SH. ANIL KUMAR SISODIA", "T.S. THAKUR", "DINESH MAHESHWARI"],
)
def test_accepts_real_judge_names(name: str) -> None:
    assert is_plausible_judge_name(name) is True


@pytest.mark.parametrize("name", ["which", "vide", "and", "the", "", "   ", None, "A"])
def test_rejects_observed_parser_noise(name: str | None) -> None:
    """'which' and 'vide' are real values found in vaquill Delhi HC judges lists."""
    assert is_plausible_judge_name(name) is False


def test_clean_judge_names_drops_noise_and_keeps_names() -> None:
    assert clean_judge_names(["SH. ANIL KUMAR SISODIA", "vide"]) == (
        "SH. ANIL KUMAR SISODIA",
    )


def test_clean_judge_names_on_all_noise_returns_empty() -> None:
    assert clean_judge_names(["which"]) == ()


@pytest.mark.parametrize("names", [None, []])
def test_clean_judge_names_handles_absent_input(names: list[str] | None) -> None:
    assert clean_judge_names(names) == ()


def test_scrubs_isolated_scr_margin_letter() -> None:
    text = (
        "the respondent was in perfect condition of health and never really "
        "required H admission"
    )
    assert scrub_margin_letters(text) == (
        "the respondent was in perfect condition of health and never really "
        "required admission"
    )


def test_preserves_statutory_section_letters() -> None:
    """'Section 302 A' must survive — it is a real provision reference."""
    text = "punishable under Section 302 A of the Code"
    assert scrub_margin_letters(text) == text


def test_preserves_exhibit_and_annexure_letters() -> None:
    for text in ("marked as Exhibit A by the court", "filed at Annexure B herein"):
        assert scrub_margin_letters(text) == text


def test_preserves_letters_outside_the_margin_range() -> None:
    """Only A-H are SCR margin markers; other capitals are not scrubbed."""
    text = "the witness identified accused Z during the parade"
    assert scrub_margin_letters(text) == text


def test_scrubbing_is_idempotent() -> None:
    text = "the appeal was G dismissed by this Court"
    once = scrub_margin_letters(text)
    assert scrub_margin_letters(once) == once
