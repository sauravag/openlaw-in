from pathlib import Path

from openlaw.datasets import vaquill


def test_reads_supreme_court_chunks_from_fixture(vaquill_fixture_dir: Path) -> None:
    chunks = list(
        vaquill.read_judgment_chunks(
            vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
            corpus_version="v2026.08",
        )
    )

    assert len(chunks) > 0
    first = chunks[0]
    assert first.court_code == "SCI"
    assert first.join_key != ""
    assert first.corpus_version == "v2026.08"


def test_judgment_chunks_carry_normalised_join_keys(vaquill_fixture_dir: Path) -> None:
    chunks = list(
        vaquill.read_judgment_chunks(
            vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
            corpus_version="v2026.08",
        )
    )
    for chunk in chunks:
        assert "_" not in chunk.join_key
        assert chunk.join_key == chunk.join_key.upper()


def test_reads_provisions_from_fixture(vaquill_fixture_dir: Path) -> None:
    provisions = list(
        vaquill.read_provisions(
            vaquill_fixture_dir / "in_central_legislation.parquet",
            corpus_version="v2026.08",
        )
    )

    assert len(provisions) > 0
    for provision in provisions:
        # The core guarantee, enforced at read time.
        assert provision.source_url.startswith("http")


def test_reader_streams_rather_than_materialising(vaquill_fixture_dir: Path) -> None:
    """A generator, so 4.9 GB files are never loaded whole."""
    stream = vaquill.read_judgment_chunks(
        vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
        corpus_version="v2026.08",
    )
    assert next(stream) is not None
    stream.close()
