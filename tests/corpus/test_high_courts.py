import pytest

from openlaw.corpus.high_courts import (
    HIGH_COURTS,
    PUNJAB_AND_HARYANA,
    HighCourt,
    get_high_court,
)


def test_punjab_and_haryana_carries_the_measured_court_codes() -> None:
    """vaquill spells the court `3_22`; KanoonGPT spells the same court `3~22`."""
    assert PUNJAB_AND_HARYANA.slug == "punjab-and-haryana"
    assert PUNJAB_AND_HARYANA.vaquill_court_code == "3_22"
    assert PUNJAB_AND_HARYANA.kanoongpt_court_code == "3~22"
    assert PUNJAB_AND_HARYANA.source_file == "in_punjab-and-haryana_judgments.parquet"


def test_punjab_and_haryana_is_registered() -> None:
    assert HIGH_COURTS["punjab-and-haryana"] == PUNJAB_AND_HARYANA


def test_delhi_high_court_is_not_registered() -> None:
    """Explicit product decision -- Delhi is deliberately excluded from v1."""
    assert "delhi" not in HIGH_COURTS
    assert all("delhi" not in slug for slug in HIGH_COURTS)


def test_get_high_court_returns_the_registered_entry() -> None:
    assert get_high_court("punjab-and-haryana") is PUNJAB_AND_HARYANA


def test_get_high_court_raises_a_helpful_error_for_an_unknown_slug() -> None:
    with pytest.raises(KeyError, match="punjab-and-haryana"):
        get_high_court("bombay")


def test_high_court_is_frozen() -> None:
    with pytest.raises(Exception):
        PUNJAB_AND_HARYANA.slug = "changed"  # type: ignore[misc]


def test_high_court_dataclass_holds_all_four_fields() -> None:
    court = HighCourt(
        slug="x", source_file="f.parquet", vaquill_court_code="1_1",
        kanoongpt_court_code="1~1",
    )
    assert (court.slug, court.source_file, court.vaquill_court_code, court.kanoongpt_court_code) == (
        "x", "f.parquet", "1_1", "1~1",
    )
