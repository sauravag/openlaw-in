"""Registry of High Courts onboarded into the shared `hc_judgments_v1` collection.

Each entry pairs the vaquill source file and its vaquill `court_code` with the
matching KanoonGPT `court_code` -- the two datasets spell the same court's code
differently. vaquill uses `_` as a separator (e.g. Punjab & Haryana is `3_22`);
KanoonGPT uses `~` for the same court (`3~22`). `openlaw.corpus.identifiers`
strips both when building the join key, so the join itself does not care, but
`openlaw.corpus.join.build_metadata_index`'s `court_codes` filter compares raw,
un-normalised `court_code` strings -- so the KanoonGPT spelling must be passed
there exactly.

Delhi High Court is deliberately NOT registered here. That is an explicit
product decision, not an oversight -- do not add it without that decision
being revisited.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HighCourt:
    """One High Court onboarded into the shared `hc_judgments_v1` collection."""

    slug: str
    source_file: str
    vaquill_court_code: str
    kanoongpt_court_code: str


PUNJAB_AND_HARYANA = HighCourt(
    slug="punjab-and-haryana",
    source_file="in_punjab-and-haryana_judgments.parquet",
    vaquill_court_code="3_22",
    kanoongpt_court_code="3~22",
)

HIGH_COURTS: dict[str, HighCourt] = {PUNJAB_AND_HARYANA.slug: PUNJAB_AND_HARYANA}


def get_high_court(slug: str) -> HighCourt:
    """Return the registered `HighCourt` for `slug`.

    Raises `KeyError` naming the registered slugs when `slug` is unknown, rather
    than a bare `KeyError: 'slug'` that leaves the caller guessing what is valid.
    """
    try:
        return HIGH_COURTS[slug]
    except KeyError as exc:
        known = ", ".join(sorted(HIGH_COURTS)) or "(none registered)"
        raise KeyError(f"unknown High Court slug {slug!r}; registered: {known}") from exc
