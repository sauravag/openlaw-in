"""The dataset join — vaquill text to KanoonGPT citation metadata.

Measured join rates (spec 3.3): Supreme Court 100.0% on neutral citation
(37,014 / 37,014); Delhi High Court 98.6% on CNR (325,898 / 330,401).
"""

from collections.abc import Mapping
from pathlib import Path

from openlaw.corpus.identifiers import normalise_identifier
from openlaw.corpus.schema import CaseMetadata
from openlaw.datasets.kanoongpt import read_case_metadata


class MetadataIndex:
    """An immutable `join_key -> CaseMetadata` lookup."""

    def __init__(self, records: Mapping[str, CaseMetadata]) -> None:
        self._records: dict[str, CaseMetadata] = dict(records)

    def lookup(self, join_key: str) -> CaseMetadata | None:
        """Return the metadata for `join_key`, normalising it first."""
        return self._records.get(normalise_identifier(join_key))

    def __len__(self) -> int:
        return len(self._records)

    @property
    def join_keys(self) -> frozenset[str]:
        """Every join key held by this index."""
        return frozenset(self._records)


def build_metadata_index(
    root: Path, court_codes: frozenset[str] | None = None
) -> MetadataIndex:
    """Build a metadata index from every KanoonGPT Parquet file under `root`.

    Records reporting parser errors lose to clean records for the same key.

    `court_codes`, when given, restricts the index to records whose `court_code`
    is a member — the full 17,070,574-record KanoonGPT release (~12.7 GB plus
    ~1.7 GB of dict overhead) does not fit comfortably in memory when only one
    court's ~43,000 records are actually needed. `None` (the default) keeps
    every record, matching prior behaviour.
    """
    records: dict[str, CaseMetadata] = {}
    for metadata in read_case_metadata(root):
        if court_codes is not None and metadata.court_code not in court_codes:
            continue
        existing = records.get(metadata.join_key)
        if existing is None or (
            existing.has_parser_errors and not metadata.has_parser_errors
        ):
            records[metadata.join_key] = metadata
    return MetadataIndex(records)


def join_rate(chunk_keys: set[str], index: MetadataIndex) -> float:
    """Return the fraction of `chunk_keys` present in `index`. 0.0 when empty."""
    if not chunk_keys:
        return 0.0
    matched = sum(1 for key in chunk_keys if index.lookup(key) is not None)
    return matched / len(chunk_keys)
