from datetime import date

import pytest

from openlaw.corpus.dates import parse_decision_date


def test_parses_supreme_court_iso_date() -> None:
    assert parse_decision_date("2019-03-26") == date(2019, 3, 26)


def test_parses_high_court_date_with_time_component() -> None:
    """Delhi HC stores 'YYYY-MM-DD HH:MM:SS' where SC stores 'YYYY-MM-DD'."""
    assert parse_decision_date("2016-05-31 00:00:00") == date(2016, 5, 31)


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date", "0000-00-00"])
def test_returns_none_for_unparseable_input(raw: str | None) -> None:
    assert parse_decision_date(raw) is None
