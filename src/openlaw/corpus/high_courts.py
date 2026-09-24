"""Registry of High Courts onboarded into the shared `hc_judgments_v1` collection.

Each entry pairs the vaquill source file and its vaquill `court_code` with the
matching KanoonGPT `court_code` -- the two datasets spell the same court's code
differently. vaquill uses `_` as a separator (e.g. Punjab & Haryana is `3_22`,
Delhi is `7_26`); KanoonGPT uses `~` for the same court (`3~22`, `7~26`).
`openlaw.corpus.identifiers` strips both when building the join key, so the
join itself does not care, but `openlaw.corpus.join.build_metadata_index`'s
`court_codes` filter compares raw, un-normalised `court_code` strings -- so the
KanoonGPT spelling must be passed there exactly.

Every registered court shares ONE Qdrant collection
(`openlaw.index.hc_upsert.HC_COLLECTION`), distinguished at query time by the
`court_code` payload field -- see `openlaw.retrieval.search`'s jurisdiction
gating. A build (`scripts/build_hc_index.py`) and its `--restart` are always
scoped to one court's points via that same field
(`openlaw.index.hc_upsert.delete_hc_court_points`), so onboarding a new court
here can never disturb another already-indexed court's points.
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

# Territorial jurisdiction: the NCT of Delhi -- its judgments bind there and
# are only persuasive elsewhere, which is why `court_code` payload filtering
# and `openlaw.retrieval.search`'s jurisdiction gating matter. Measured
# against the real data: 1,064,459 rows, 100% English, average 3,273
# characters/chunk. The vaquill `case_id` is the CNR (e.g.
# `DLHC010517432014`); a spot check across six random row groups found 100%
# of matched CNRs carry `source_pdf_s3_url`, so Delhi judgments are citable
# exactly as Punjab & Haryana's are.
DELHI = HighCourt(
    slug="delhi",
    source_file="in_delhi_judgments.parquet",
    vaquill_court_code="7_26",
    kanoongpt_court_code="7~26",
)

HIGH_COURTS: dict[str, HighCourt] = {
    PUNJAB_AND_HARYANA.slug: PUNJAB_AND_HARYANA,
    DELHI.slug: DELHI,
}


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
