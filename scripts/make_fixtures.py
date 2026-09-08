"""Sample the real datasets into small Parquet fixtures for tests.

Run once: python scripts/make_fixtures.py
Fixtures are committed so the test suite does not depend on the 60 GB corpus.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from openlaw.config import get_settings

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
ROWS_PER_FILE = 400


def sample_parquet(source: Path, destination: Path, rows: int) -> int:
    """Write the first `rows` rows of `source` to `destination`. Returns row count."""
    reader = pq.ParquetFile(source)
    batch = next(reader.iter_batches(batch_size=rows))
    table = pa.Table.from_batches([batch])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination)
    return table.num_rows


def main() -> None:
    settings = get_settings()

    sources: dict[str, Path] = {
        "vaquill/in_supreme-court_judgments.parquet":
            settings.vaquill_root / "in_supreme-court_judgments.parquet",
        "vaquill/in_delhi_judgments.parquet":
            settings.vaquill_root / "in_delhi_judgments.parquet",
        "vaquill/in_central_legislation.parquet":
            settings.vaquill_root / "in_central_legislation.parquet",
    }

    kanoon_2019 = sorted((settings.kanoongpt_root / "year=2019").glob("*.parquet"))
    if kanoon_2019:
        sources["kanoongpt/year=2019.parquet"] = kanoon_2019[0]

    for relative, source in sources.items():
        written = sample_parquet(source, FIXTURE_DIR / relative, ROWS_PER_FILE)
        print(f"[Fixture] {relative}: {written} rows")


if __name__ == "__main__":
    main()
