"""Sample the real datasets into small Parquet fixtures for tests.

Run once: python scripts/make_fixtures.py
Fixtures are committed so the test suite does not depend on the 60 GB corpus.

The KanoonGPT fixture is built to OVERLAP the vaquill Supreme Court fixture's
cases (rather than taking an arbitrary year partition), so the two committed
fixtures can actually exercise the join end to end. See
`tests/datasets/test_join_end_to_end.py`.
"""

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from openlaw.config import get_settings
from openlaw.corpus.identifiers import normalise_identifier

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
ROWS_PER_FILE = 400
KANOONGPT_BATCH_SIZE = 50_000
KANOONGPT_COLUMNS: list[str] = [
    "neutral_citation", "cnr_number", "court_code",
    "source_pdf_s3_url", "headnote_text", "quality_json",
]


def sample_parquet(source: Path, destination: Path, rows: int) -> int:
    """Write the first `rows` rows of `source` to `destination`. Returns row count."""
    reader = pq.ParquetFile(source)
    batch = next(reader.iter_batches(batch_size=rows))
    table = pa.Table.from_batches([batch])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination)
    return table.num_rows


def _read_case_ids(sc_fixture: Path) -> set[str]:
    """Return the distinct `case_id` values held by the written SC fixture."""
    table = pq.read_table(sc_fixture, columns=["case_id"])
    return set(table.column("case_id").to_pylist())


def _years_from_case_ids(case_ids: set[str]) -> list[str]:
    """Derive the leading 4-digit years present in `case_ids`, sorted."""
    return sorted({case_id[:4] for case_id in case_ids if case_id[:4].isdigit()})


def build_kanoongpt_fixture(
    kanoongpt_root: Path, case_ids: set[str], target_rows: int
) -> tuple[list[dict[str, Any]], int]:
    """Return combined KanoonGPT rows overlapping `case_ids`, and the match count.

    Streams only the `year=NNNN` partitions implied by `case_ids` (never a whole
    file at once). A row is kept as a match when its `neutral_citation`,
    normalised via `normalise_identifier`, equals the normalised `case_id` of
    some vaquill SC fixture chunk. Non-matching rows from the same partitions
    top up the fixture to roughly `target_rows` so the fixture still exercises
    the non-matching path.
    """
    normalised_case_ids = {normalise_identifier(case_id) for case_id in case_ids}
    years = _years_from_case_ids(case_ids)

    matched_rows: list[dict[str, Any]] = []
    filler_rows: list[dict[str, Any]] = []

    for year in years:
        year_dir = kanoongpt_root / f"year={year}"
        for path in sorted(year_dir.glob("*.parquet")):
            reader = pq.ParquetFile(path)
            batches = reader.iter_batches(
                batch_size=KANOONGPT_BATCH_SIZE, columns=KANOONGPT_COLUMNS
            )
            for batch in batches:
                for row in batch.to_pylist():
                    key = normalise_identifier(row.get("neutral_citation"))
                    if key and key in normalised_case_ids:
                        matched_rows.append(row)
                    elif len(filler_rows) < target_rows:
                        filler_rows.append(row)

    remaining = max(target_rows - len(matched_rows), 0)
    return matched_rows + filler_rows[:remaining], len(matched_rows)


def main() -> None:
    settings = get_settings()

    vaquill_sources: dict[str, Path] = {
        "vaquill/in_supreme-court_judgments.parquet":
            settings.vaquill_root / "in_supreme-court_judgments.parquet",
        "vaquill/in_delhi_judgments.parquet":
            settings.vaquill_root / "in_delhi_judgments.parquet",
        "vaquill/in_central_legislation.parquet":
            settings.vaquill_root / "in_central_legislation.parquet",
    }

    for relative, source in vaquill_sources.items():
        written = sample_parquet(source, FIXTURE_DIR / relative, ROWS_PER_FILE)
        print(f"[Fixture] {relative}: {written} rows")

    sc_fixture = FIXTURE_DIR / "vaquill" / "in_supreme-court_judgments.parquet"
    case_ids = _read_case_ids(sc_fixture)

    combined_rows, matched_count = build_kanoongpt_fixture(
        kanoongpt_root=settings.kanoongpt_root,
        case_ids=case_ids,
        target_rows=ROWS_PER_FILE,
    )

    print(f"[Fixture] kanoongpt rows matching the SC fixture's case_ids: {matched_count}")
    if matched_count == 0:
        raise SystemExit(
            "BLOCKED: no KanoonGPT rows matched the vaquill SC fixture's case_ids; "
            "the join still cannot be exercised end-to-end with these fixtures."
        )

    destination = FIXTURE_DIR / "kanoongpt" / "sc_matched.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(combined_rows), destination)
    print(f"[Fixture] kanoongpt/sc_matched.parquet: {len(combined_rows)} rows "
          f"({matched_count} matched, {len(combined_rows) - matched_count} filler)")


if __name__ == "__main__":
    main()
