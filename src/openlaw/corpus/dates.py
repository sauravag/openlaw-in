"""Parsing of judgment decision dates.

The two datasets disagree on format. Supreme Court rows carry ``2019-03-26``;
Delhi High Court rows carry ``2016-05-31 00:00:00``. Both must reduce to a date.
"""

from datetime import date, datetime

_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")


def parse_decision_date(raw: str | None) -> date | None:
    """Return the date in `raw`, or None when it is absent or unparseable."""
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return None
    text = raw.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
