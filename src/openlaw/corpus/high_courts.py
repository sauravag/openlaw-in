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

# Every remaining High Court in the vaquill dataset, ordered by size. Codes
# were read from each file's own `court_code` column rather than transcribed,
# and the KanoonGPT spelling is the same code with `~` for `_` -- the rule
# documented in this module's docstring, spot-checked against the KanoonGPT
# metadata for Bombay (27~1) and Kerala (32~4) before registering.
#
# Registering a court makes it queryable: `openlaw.retrieval.search` derives
# its jurisdiction allowlist from HIGH_COURTS. A registered court with no
# indexed points simply returns nothing, so registration ahead of its build
# is safe -- it cannot leak another court's authority into an answer.
_REMAINING: tuple[tuple[str, str, str, str], ...] = (
    ("bombay", "in_bombay_judgments.parquet", "27_1", "27~1"),  # 2,788,384 rows
    ("kerala", "in_kerala_judgments.parquet", "32_4", "32~4"),  # 2,777,738 rows
    ("telangana", "in_telangana_judgments.parquet", "36_29", "36~29"),  # 2,732,387 rows
    ("rajasthan", "in_rajasthan_judgments.parquet", "8_9", "8~9"),  # 2,676,195 rows
    ("madras", "in_madras_judgments.parquet", "33_10", "33~10"),  # 2,610,596 rows
    ("patna", "in_patna_judgments.parquet", "10_8", "10~8"),  # 2,562,269 rows
    ("allahabad", "in_allahabad_judgments.parquet", "9_13", "9~13"),  # 2,007,927 rows
    ("chhattisgarh", "in_chhattisgarh_judgments.parquet", "22_18", "22~18"),  # 1,744,438 rows
    ("karnataka", "in_karnataka_judgments.parquet", "29_3", "29~3"),  # 1,471,128 rows
    ("gujarat", "in_gujarat_judgments.parquet", "24_17", "24~17"),  # 1,400,249 rows
    ("madhya-pradesh", "in_madhya-pradesh_judgments.parquet", "23_23", "23~23"),  # 970,908 rows
    ("orissa", "in_orissa_judgments.parquet", "21_11", "21~11"),  # 872,833 rows
    ("jharkhand", "in_jharkhand_judgments.parquet", "20_7", "20~7"),  # 777,734 rows
    ("gauhati", "in_gauhati_judgments.parquet", "18_6", "18~6"),  # 663,094 rows
    ("andhra-pradesh", "in_andhra-pradesh_judgments.parquet", "28_2", "28~2"),  # 661,353 rows
    ("himachal-pradesh", "in_himachal-pradesh_judgments.parquet", "2_5", "2~5"),  # 619,919 rows
    ("calcutta", "in_calcutta_judgments.parquet", "19_16", "19~16"),  # 535,453 rows
    ("uttarakhand", "in_uttarakhand_judgments.parquet", "5_15", "5~15"),  # 307,144 rows
    ("jammu-and-kashmir", "in_jammu-and-kashmir_judgments.parquet", "1_12", "1~12"),  # 181,221 rows
    ("tripura", "in_tripura_judgments.parquet", "16_20", "16~20"),  # 112,120 rows
    ("manipur", "in_manipur_judgments.parquet", "14_25", "14~25"),  # 39,507 rows
    ("meghalaya", "in_meghalaya_judgments.parquet", "17_21", "17~21"),  # 25,558 rows
    ("sikkim", "in_sikkim_judgments.parquet", "11_24", "11~24"),  # 11,389 rows
)

for _slug, _file, _vaquill, _kanoongpt in _REMAINING:
    HIGH_COURTS[_slug] = HighCourt(
        slug=_slug,
        source_file=_file,
        vaquill_court_code=_vaquill,
        kanoongpt_court_code=_kanoongpt,
    )
