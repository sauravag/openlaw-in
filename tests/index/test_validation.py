from openlaw.index.validation import validate_payloads


def _point(chunk_id: str, url: str | None, version: str | None = "v2026.08") -> dict:
    return {
        "id": chunk_id,
        "payload": {
            "chunk_id": chunk_id,
            "source_url": url,
            "corpus_version": version,
        },
    }


def test_clean_when_every_point_has_a_url_and_version() -> None:
    report = validate_payloads(
        [_point("a", "https://x.gov.in/a.pdf"), _point("b", "https://x.gov.in/b.pdf")]
    )

    assert report.checked == 2
    assert report.is_clean is True
    assert report.missing_url == []


def test_flags_a_point_without_a_source_url() -> None:
    report = validate_payloads(
        [_point("a", "https://x.gov.in/a.pdf"), _point("b", None)]
    )

    assert report.is_clean is False
    assert report.missing_url == ["b"]


def test_flags_a_point_with_a_blank_source_url() -> None:
    report = validate_payloads([_point("a", "   ")])
    assert report.missing_url == ["a"]


def test_flags_a_point_missing_the_corpus_version() -> None:
    report = validate_payloads([_point("a", "https://x.gov.in/a.pdf", version=None)])

    assert report.is_clean is False
    assert report.missing_version == ["a"]


def test_empty_input_is_clean() -> None:
    report = validate_payloads([])
    assert report.checked == 0
    assert report.is_clean is True
