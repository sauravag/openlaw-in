import pytest

from openlaw.corpus.identifiers import normalise_identifier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2016_INSC_1151", "2016INSC1151"),  # vaquill Supreme Court case_id
        ("2019INSC398", "2019INSC398"),  # KanoonGPT neutral_citation
        ("2019 INSC 398", "2019INSC398"),
        ("dlhc010517432014", "DLHC010517432014"),
        ("  ESCR010005442019  ", "ESCR010005442019"),
    ],
)
def test_normalise_identifier_strips_separators_and_upcases(
    raw: str, expected: str
) -> None:
    assert normalise_identifier(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "___"])
def test_normalise_identifier_returns_empty_for_no_content(raw: str | None) -> None:
    assert normalise_identifier(raw) == ""


def test_vaquill_and_kanoongpt_supreme_court_ids_normalise_to_the_same_key() -> None:
    """This equality is what makes the 100% Supreme Court join work."""
    assert normalise_identifier("2019_INSC_398") == normalise_identifier("2019INSC398")
