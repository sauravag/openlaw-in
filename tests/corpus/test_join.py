import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from openlaw.corpus.join import MetadataIndex, build_metadata_index, join_rate
from openlaw.corpus.schema import CaseMetadata


def _metadata(join_key: str, court_code: str = "SCI") -> CaseMetadata:
    return CaseMetadata(
        join_key=join_key,
        court_code=court_code,
        source_pdf_url=f"https://example.gov.in/{join_key}.pdf",
    )


def _kanoongpt_row(
    neutral_citation: str,
    source_pdf_s3_url: str,
    has_parser_errors: bool,
    court_code: str = "SCI",
) -> dict[str, Any]:
    """A raw KanoonGPT row, dirty or clean, in the shape `read_case_metadata` reads."""
    quality = {"flags": {"has_parser_failed": has_parser_errors}}
    return {
        "court_code": court_code,
        "neutral_citation": neutral_citation,
        "cnr_number": None,
        "source_pdf_s3_url": source_pdf_s3_url,
        "headnote_text": None,
        "quality_json": json.dumps(quality),
    }


def _write_kanoongpt_fixture(root: Path, rows: list[dict[str, Any]]) -> Path:
    """Write `rows` (in order) as a single KanoonGPT Parquet file under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, root / "records.parquet")
    return root


def test_index_looks_up_by_normalised_key() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})

    found = index.lookup("2016INSC1151")
    assert found is not None
    assert found.join_key == "2016INSC1151"


def test_index_lookup_normalises_the_query_key() -> None:
    """A vaquill case_id must find its KanoonGPT record despite separators."""
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert index.lookup("2016_INSC_1151") is not None


def test_index_returns_none_for_missing_key() -> None:
    assert MetadataIndex({}).lookup("2016INSC1151") is None


def test_index_reports_its_size() -> None:
    index = MetadataIndex({"A1": _metadata("A1"), "B2": _metadata("B2")})

    assert len(index) == 2
    assert index.join_keys == frozenset({"A1", "B2"})


def test_join_rate_all_matched() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert join_rate({"2016INSC1151"}, index) == 1.0


def test_join_rate_partial() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert join_rate({"2016INSC1151", "1999INSC1"}, index) == 0.5


def test_join_rate_of_empty_input_is_zero() -> None:
    assert join_rate(set(), MetadataIndex({})) == 0.0


def test_build_metadata_index_prefers_clean_record_when_dirty_seen_first(
    tmp_path: Path,
) -> None:
    """The tie-break must favour the clean record regardless of file order."""
    root = _write_kanoongpt_fixture(
        tmp_path,
        [
            _kanoongpt_row("2016_INSC_1151", "https://dirty.gov.in", has_parser_errors=True),
            _kanoongpt_row("2016_INSC_1151", "https://clean.gov.in", has_parser_errors=False),
        ],
    )

    index = build_metadata_index(root)

    record = index.lookup("2016_INSC_1151")
    assert record is not None
    assert record.has_parser_errors is False
    assert record.source_pdf_url == "https://clean.gov.in"


# --- Critical 3: an optional court filter, so the index needn't hold 17M records --


def test_build_metadata_index_with_no_filter_keeps_every_court(tmp_path: Path) -> None:
    root = _write_kanoongpt_fixture(
        tmp_path,
        [
            _kanoongpt_row("2016_INSC_1151", "https://sci.gov.in", False, court_code="SCI"),
            _kanoongpt_row("DLHC0105174", "https://dlhc.gov.in", False, court_code="7_26"),
        ],
    )

    index = build_metadata_index(root)

    assert len(index) == 2


def test_build_metadata_index_keeps_only_matching_court_codes(tmp_path: Path) -> None:
    root = _write_kanoongpt_fixture(
        tmp_path,
        [
            _kanoongpt_row("2016_INSC_1151", "https://sci.gov.in", False, court_code="SCI"),
            _kanoongpt_row("DLHC0105174", "https://dlhc.gov.in", False, court_code="7_26"),
        ],
    )

    index = build_metadata_index(root, court_codes=frozenset({"SCI"}))

    assert len(index) == 1
    kept = index.lookup("2016_INSC_1151")
    assert kept is not None
    assert kept.court_code == "SCI"
    assert index.lookup("DLHC0105174") is None


def test_build_metadata_index_court_filter_can_drop_everything(tmp_path: Path) -> None:
    root = _write_kanoongpt_fixture(
        tmp_path,
        [_kanoongpt_row("2016_INSC_1151", "https://sci.gov.in", False, court_code="SCI")],
    )

    index = build_metadata_index(root, court_codes=frozenset({"NO_SUCH_COURT"}))

    assert len(index) == 0


def test_build_metadata_index_keeps_clean_record_when_dirty_seen_second(
    tmp_path: Path,
) -> None:
    """A later dirty record for the same key must not displace an earlier clean one."""
    root = _write_kanoongpt_fixture(
        tmp_path,
        [
            _kanoongpt_row("2016_INSC_1151", "https://clean.gov.in", has_parser_errors=False),
            _kanoongpt_row("2016_INSC_1151", "https://dirty.gov.in", has_parser_errors=True),
        ],
    )

    index = build_metadata_index(root)

    record = index.lookup("2016_INSC_1151")
    assert record is not None
    assert record.has_parser_errors is False
    assert record.source_pdf_url == "https://clean.gov.in"
