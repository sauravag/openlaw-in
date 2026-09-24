"""Streaming readers for the vaquill/open-india-law Parquet files.

All pyarrow use is confined to this module. Files reach 4.9 GB, so readers yield
in batches and never materialise a whole file. Rows failing validation are skipped
so one bad record cannot abort an ingest.
"""

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import ValidationError

from openlaw.corpus.schema import JudgmentChunk, Provision

_JUDGMENT_COLUMNS: list[str] = [
    "chunk_id", "case_id", "chunk_index", "text", "text_original",
    "court_code", "judges", "decision_date", "language_code", "citation",
]
_PROVISION_COLUMNS: list[str] = [
    "chunk_id", "act_id", "title", "section_number", "text", "jurisdiction",
    "in_force", "legal_subject", "acts_referenced", "defined_terms", "source_url",
]


def read_judgment_chunks(
    path: Path, corpus_version: str, batch_size: int = 10_000
) -> Iterator[JudgmentChunk]:
    """Yield validated judgment chunks from the Parquet file at `path`."""
    reader = pq.ParquetFile(path)
    for batch in reader.iter_batches(batch_size=batch_size, columns=_JUDGMENT_COLUMNS):
        for row in batch.to_pylist():
            try:
                yield JudgmentChunk.from_row(row, corpus_version=corpus_version)
            except ValidationError:
                continue


def read_provisions(
    path: Path, corpus_version: str, batch_size: int = 10_000
) -> Iterator[Provision]:
    """Yield validated provisions from the Parquet file at `path`."""
    reader = pq.ParquetFile(path)
    for batch in reader.iter_batches(batch_size=batch_size, columns=_PROVISION_COLUMNS):
        for row in batch.to_pylist():
            try:
                yield Provision.from_row(row, corpus_version=corpus_version)
            except ValidationError:
                continue
