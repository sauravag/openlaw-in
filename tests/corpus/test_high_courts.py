import pytest

from openlaw.corpus.high_courts import (
    DELHI,
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


def test_delhi_carries_the_measured_court_codes() -> None:
    """vaquill spells the court `7_26`; KanoonGPT spells the same court `7~26`."""
    assert DELHI.slug == "delhi"
    assert DELHI.vaquill_court_code == "7_26"
    assert DELHI.kanoongpt_court_code == "7~26"
    assert DELHI.source_file == "in_delhi_judgments.parquet"


def test_delhi_is_registered() -> None:
    assert HIGH_COURTS["delhi"] == DELHI


def test_both_registered_courts_have_distinct_slugs_and_codes() -> None:
    """Two courts must never collide on slug or either court-code spelling --
    a collision would silently merge two jurisdictions' points/checkpoints."""
    assert PUNJAB_AND_HARYANA.slug != DELHI.slug
    assert PUNJAB_AND_HARYANA.vaquill_court_code != DELHI.vaquill_court_code
    assert PUNJAB_AND_HARYANA.kanoongpt_court_code != DELHI.kanoongpt_court_code


def test_get_high_court_returns_the_registered_entry() -> None:
    assert get_high_court("punjab-and-haryana") is PUNJAB_AND_HARYANA
    assert get_high_court("delhi") is DELHI


def test_get_high_court_raises_a_helpful_error_for_an_unknown_slug() -> None:
    # Was "bombay", which is now a registered court. The assertion is about
    # the error naming what IS available, so the example only has to be a
    # slug no court will ever have -- not merely one not yet onboarded.
    with pytest.raises(KeyError, match="punjab-and-haryana"):
        get_high_court("not-a-real-court")


def test_every_registered_court_has_unique_codes_and_a_matching_spelling() -> None:
    """The two datasets spell the same court differently; a wrong code does
    not fail loudly, it silently drops every row as unresolved."""
    slugs = [c.slug for c in HIGH_COURTS.values()]
    vaquill = [c.vaquill_court_code for c in HIGH_COURTS.values()]
    assert len(set(slugs)) == len(slugs)
    assert len(set(vaquill)) == len(vaquill)
    for court in HIGH_COURTS.values():
        assert court.kanoongpt_court_code == court.vaquill_court_code.replace("_", "~")
        assert court.source_file.endswith(".parquet")


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
