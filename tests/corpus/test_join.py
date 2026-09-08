from openlaw.corpus.join import MetadataIndex, join_rate
from openlaw.corpus.schema import CaseMetadata


def _metadata(join_key: str, court_code: str = "SCI") -> CaseMetadata:
    return CaseMetadata(
        join_key=join_key,
        court_code=court_code,
        source_pdf_url=f"https://example.gov.in/{join_key}.pdf",
    )


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
