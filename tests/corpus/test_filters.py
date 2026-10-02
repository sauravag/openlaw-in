from openlaw.corpus.filters import deduplicate, english_only, is_english, summarise
from openlaw.corpus.schema import JudgmentChunk


def _chunk(
    case_id: str, index: int, language: str, chunk_id: str | None = None
) -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": chunk_id or f"{case_id}_{index:03d}_{language}",
            "case_id": case_id,
            "chunk_index": index,
            "text": "text",
            "text_original": "text",
            "court_code": "SCI",
            "judges": [],
            "decision_date": "2016-12-15",
            "language_code": language,
        },
        corpus_version="v2026.08",
    )


def test_recognises_english_codes() -> None:
    assert is_english(_chunk("2016_INSC_1", 0, "en")) is True
    assert is_english(_chunk("2016_INSC_1", 0, "eng")) is True


def test_rejects_regional_language_codes() -> None:
    """Punjabi and Hindi are the two largest non-English blocks in the SC corpus."""
    for code in ("pun", "hin", "guj", "tam", "mal", "mar", "ben", "tel", "kan"):
        assert is_english(_chunk("2016_INSC_1", 0, code)) is False


def test_english_only_drops_translations_of_the_same_case() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en"),
        _chunk("2016_INSC_1", 0, "pun"),
        _chunk("2016_INSC_1", 0, "hin"),
    ]
    kept = list(english_only(chunks))

    assert len(kept) == 1
    assert kept[0].language_code == "en"


def test_deduplicate_removes_repeated_chunk_ids() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en", chunk_id="dup"),
        _chunk("2016_INSC_1", 0, "en", chunk_id="dup"),
        _chunk("2016_INSC_1", 1, "en", chunk_id="unique"),
    ]
    assert [chunk.chunk_id for chunk in deduplicate(chunks)] == ["dup", "unique"]


def test_deduplicate_preserves_first_occurrence_order() -> None:
    chunks = [
        _chunk("2016_INSC_2", 0, "en", chunk_id="b"),
        _chunk("2016_INSC_1", 0, "en", chunk_id="a"),
        _chunk("2016_INSC_2", 0, "en", chunk_id="b"),
    ]
    assert [chunk.chunk_id for chunk in deduplicate(chunks)] == ["b", "a"]


def test_summarise_reports_english_fraction_and_case_count() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en"),
        _chunk("2016_INSC_1", 0, "pun"),
        _chunk("2016_INSC_2", 0, "en"),
        _chunk("2016_INSC_2", 0, "hin"),
    ]
    stats = summarise(chunks)

    assert stats.total == 4
    assert stats.english == 2
    assert stats.unique_cases == 2
    assert stats.english_fraction == 0.5


def test_summarise_of_empty_input_has_zero_fraction() -> None:
    stats = summarise([])
    assert stats.total == 0
    assert stats.english_fraction == 0.0
