"""End-to-end regression test for the product's core mechanism.

A vaquill judgment chunk carries an EMPTY `source_url` — the government PDF URL
comes only from joining to a KanoonGPT record on the normalised case identifier.
Without a nonzero number of chunks resolving through that join, no citation in
the corpus is verifiable. This test runs the real committed fixtures (not
synthetic data) through the exact production path: `build_metadata_index` over
the KanoonGPT fixture, `read_judgment_chunks` + `english_only` over the vaquill
Supreme Court fixture, then `resolve_judgment_url` for each chunk.
"""

from pathlib import Path

from openlaw.corpus.filters import english_only
from openlaw.corpus.join import build_metadata_index
from openlaw.corpus.schema import JudgmentChunk
from openlaw.corpus.sources import resolve_judgment_url
from openlaw.datasets.vaquill import read_judgment_chunks

CORPUS_VERSION = "v2026.08"


def test_real_fixture_judgment_chunks_resolve_to_government_urls(
    kanoongpt_fixture_dir: Path, vaquill_fixture_dir: Path
) -> None:
    """A nonzero number of real SC fixture chunks must resolve via the join."""
    index = build_metadata_index(kanoongpt_fixture_dir)

    chunks = english_only(
        read_judgment_chunks(
            vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
            corpus_version=CORPUS_VERSION,
        )
    )

    resolved_urls = [
        url
        for chunk in chunks
        if (url := resolve_judgment_url(chunk, index)) is not None
    ]

    assert len(resolved_urls) > 0, (
        "no fixture chunk resolved to a government URL; the KanoonGPT and "
        "vaquill SC fixtures no longer overlap on case_id"
    )
    for url in resolved_urls:
        assert url.startswith("https://")


def test_unmatched_chunk_resolves_to_none(kanoongpt_fixture_dir: Path) -> None:
    """A chunk whose case has no KanoonGPT record must resolve to None, not raise.

    The real KanoonGPT fixture happens to carry a matching record for every
    case_id in the real vaquill SC fixture (that's the point of Finding 1's
    fix), so there is no real fixture chunk left to exercise the "not in the
    index" branch. A case_id guaranteed absent from any year the fixture was
    built from stands in for it instead, against the real, unmodified index.
    """
    index = build_metadata_index(kanoongpt_fixture_dir)

    unmatched_chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "1900_INSC_1_000", "case_id": "1900_INSC_1",
            "chunk_index": 0, "text": "text", "text_original": "text",
            "court_code": "SCI", "judges": [], "decision_date": "1900-01-01",
            "language_code": "en",
        },
        corpus_version=CORPUS_VERSION,
    )
    assert index.lookup(unmatched_chunk.join_key) is None

    assert resolve_judgment_url(unmatched_chunk, index) is None
