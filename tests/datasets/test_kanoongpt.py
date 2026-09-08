from pathlib import Path

from openlaw.datasets import kanoongpt


def test_reads_case_metadata_from_fixture(kanoongpt_fixture_dir: Path) -> None:
    records = list(kanoongpt.read_case_metadata(kanoongpt_fixture_dir))

    assert len(records) > 0
    for record in records:
        assert record.join_key != ""


def test_supreme_court_records_carry_pdf_urls(kanoongpt_fixture_dir: Path) -> None:
    """KanoonGPT supplies the citation URL that vaquill judgments lack."""
    supreme = [
        record
        for record in kanoongpt.read_case_metadata(kanoongpt_fixture_dir)
        if record.court_code == "SCI"
    ]

    assert supreme, "fixture should contain Supreme Court records"
    assert any(record.source_pdf_url for record in supreme)


def test_supreme_court_records_carry_headnotes(kanoongpt_fixture_dir: Path) -> None:
    """43,402 of 43,424 SCI records have headnotes (spec 3.3)."""
    supreme = [
        record
        for record in kanoongpt.read_case_metadata(kanoongpt_fixture_dir)
        if record.court_code == "SCI"
    ]
    assert any(record.headnote for record in supreme)


def test_iter_parquet_files_finds_year_partitions(kanoongpt_fixture_dir: Path) -> None:
    assert len(kanoongpt.iter_parquet_files(kanoongpt_fixture_dir)) > 0
