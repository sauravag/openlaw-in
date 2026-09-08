"""Streaming reader for the KanoonGPT/indian-case-laws Parquet files.

The structured release is partitioned by year across 77 files holding 17,070,574
judgment-level records. This module supplies the citation URLs, headnotes and
quality flags that the vaquill judgment corpus lacks.
"""

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import ValidationError

from openlaw.corpus.schema import CaseMetadata

_COLUMNS: list[str] = [
    "court_code", "neutral_citation", "cnr_number",
    "source_pdf_s3_url", "headnote_text", "quality_json",
]


def iter_parquet_files(root: Path) -> list[Path]:
    """Return every Parquet file under `root`, including year partitions, sorted."""
    return sorted(root.rglob("*.parquet"))


def read_case_metadata(root: Path, batch_size: int = 50_000) -> Iterator[CaseMetadata]:
    """Yield validated case metadata from every Parquet file under `root`.

    Records without any usable join key fail validation and are skipped.
    """
    for path in iter_parquet_files(root):
        reader = pq.ParquetFile(path)
        for batch in reader.iter_batches(batch_size=batch_size, columns=_COLUMNS):
            for row in batch.to_pylist():
                try:
                    yield CaseMetadata.from_row(row)
                except ValidationError:
                    continue
