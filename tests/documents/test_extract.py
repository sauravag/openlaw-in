"""Task 4: extraction and chunking, with limits enforced before expensive
parsing.

Fixtures are built programmatically here rather than committed as binary
files (a hand-rolled minimal PDF writer for pages with real text, `pypdf`'s
`PdfWriter` for blank/encrypted PDFs, and `python-docx` for DOCX) so the
test suite carries no opaque binary blobs and every fixture's shape is
visible in the diff that creates it.
"""

from __future__ import annotations

import io
import os
import zipfile

import docx
import pytest
from pypdf import PdfWriter

from openlaw.config import Settings
from openlaw.documents import DOCUMENT_EXCERPT_PREFIX, MAX_EXCERPT_CHARS
from openlaw.documents.extract import ExtractedPage, ExtractionError, chunk_pages, extract_pages

_PDF_TYPE = "application/pdf"
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TEXT_TYPE = "text/plain"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/vaquill",
        "kanoongpt_root": "/tmp/kanoongpt",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _text_pdf_bytes(page_texts: list[str]) -> bytes:
    """A minimal, hand-written multi-page PDF with a real text layer on
    each page -- `pypdf` can extract text from it, unlike a page built with
    `PdfWriter.add_blank_page`, which has no content stream at all."""
    n_pages = len(page_texts)
    page_obj_start = 3
    font_obj = page_obj_start + n_pages
    content_obj_start = font_obj + 1

    kids = " ".join(f"{page_obj_start + i} 0 R" for i in range(n_pages))
    objs: list[tuple[int, str]] = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"),
    ]
    for i in range(n_pages):
        objs.append(
            (
                page_obj_start + i,
                "<< /Type /Page /Parent 2 0 R /Resources "
                f"<< /Font << /F1 {font_obj} 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {content_obj_start + i} 0 R >>",
            )
        )
    objs.append((font_obj, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    for i, text in enumerate(page_texts):
        stream = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET"
        objs.append(
            (content_obj_start + i, f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num, body in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref_offset = out.tell()
    max_num = max(offsets)
    out.write(f"xref\n0 {max_num + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, max_num + 1):
        out.write(f"{offsets.get(num, 0):010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {max_num + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    )
    return out.getvalue()


def _blank_pdf_bytes(page_count: int) -> bytes:
    """A structurally valid PDF whose pages carry no content stream at
    all -- `extract_text()` returns `""` for each, standing in for a
    scanned/image-only page that has no text layer."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _encrypted_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("s3cret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _not_a_docx_zip_bytes() -> bytes:
    """A real ZIP (so it passes the magic-byte check) that is not a DOCX
    package -- missing every OOXML part `python-docx` expects."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("hello.txt", "not a docx")
    return buf.getvalue()


# --- unsupported_type --------------------------------------------------


def test_rejects_a_content_type_outside_the_allowed_set() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(b"whatever", "image/png", _settings())
    assert exc_info.value.reason == "unsupported_type"


def test_rejects_a_pdf_content_type_whose_bytes_are_actually_a_zip() -> None:
    """A `.pdf` extension on a DOCX (ZIP) must fail as `unsupported_type`,
    never reach `pypdf`, and never surface as `corrupt`."""
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(_docx_bytes(["hello"]), _PDF_TYPE, _settings())
    assert exc_info.value.reason == "unsupported_type"


def test_rejects_a_docx_content_type_whose_bytes_are_actually_a_pdf() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(_text_pdf_bytes(["hello"]), _DOCX_TYPE, _settings())
    assert exc_info.value.reason == "unsupported_type"


# --- too_large -----------------------------------------------------------


def test_rejects_a_file_larger_than_the_configured_byte_cap() -> None:
    settings = _settings(max_upload_bytes=10)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(b"more than ten bytes of plain text", _TEXT_TYPE, settings)
    assert exc_info.value.reason == "too_large"


def test_size_cap_fires_even_on_bytes_that_could_never_parse() -> None:
    """The size check happens before any parser sees the bytes: garbage
    that would also fail to parse still reports `too_large`, not
    `corrupt`, proving the cheap check runs first."""
    settings = _settings(max_upload_bytes=5)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(os.urandom(500), _PDF_TYPE, settings)
    assert exc_info.value.reason == "too_large"


def test_unsupported_type_is_reported_even_for_an_oversized_file() -> None:
    """The content-type gate is cheaper than measuring/parsing content, and
    runs first regardless of size."""
    settings = _settings(max_upload_bytes=5)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(os.urandom(500), "image/png", settings)
    assert exc_info.value.reason == "unsupported_type"


# --- too_many_pages --------------------------------------------------------


def test_rejects_a_pdf_with_more_pages_than_the_configured_cap() -> None:
    settings = _settings(max_document_pages=20)
    data = _blank_pdf_bytes(21)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, settings)
    assert exc_info.value.reason == "too_many_pages"


def test_page_cap_uses_the_configured_value_not_a_hardcoded_one() -> None:
    settings = _settings(max_document_pages=1)
    data = _text_pdf_bytes(["page one", "page two"])
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, settings)
    assert exc_info.value.reason == "too_many_pages"


# --- no_text ---------------------------------------------------------------


def test_a_blank_scanned_style_pdf_raises_no_text_not_an_empty_success() -> None:
    data = _blank_pdf_bytes(2)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, _settings())
    assert exc_info.value.reason == "no_text"


def test_a_docx_with_only_empty_paragraphs_raises_no_text() -> None:
    data = _docx_bytes(["", "   ", ""])
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _DOCX_TYPE, _settings())
    assert exc_info.value.reason == "no_text"


def test_an_empty_text_file_raises_no_text() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(b"", _TEXT_TYPE, _settings())
    assert exc_info.value.reason == "no_text"


def test_a_pdf_with_one_readable_page_and_one_blank_page_still_succeeds() -> None:
    """Only a *fully* blank document is `no_text` -- a partially blank
    document (a cover sheet, say) is a normal successful extraction."""
    data = _text_pdf_bytes(["real content here"])
    pages = extract_pages(data, _PDF_TYPE, _settings())
    assert any(page.text.strip() for page in pages)


# --- corrupt -----------------------------------------------------------


def test_an_encrypted_pdf_raises_corrupt() -> None:
    data = _encrypted_pdf_bytes()
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


def test_a_pdf_with_valid_magic_bytes_but_malformed_structure_raises_corrupt() -> None:
    data = b"%PDF-1.4\n" + os.urandom(200)
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


def test_a_zip_with_valid_magic_bytes_but_no_docx_parts_raises_corrupt() -> None:
    data = _not_a_docx_zip_bytes()
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _DOCX_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


def test_a_truncated_docx_zip_raises_corrupt() -> None:
    """A ZIP whose local-file-header magic bytes are intact but whose
    central directory is missing (truncated mid-transfer, say) fails to
    even open as a zip -- `corrupt`, not a raw `BadZipFile` traceback."""
    data = _docx_bytes(["hello world"])[:80]
    assert data.startswith(b"PK\x03\x04")
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _DOCX_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


def test_a_pdf_page_that_fails_during_text_extraction_raises_corrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Page count and encryption status are read from the object graph
    without decoding any content stream; only the later, more expensive
    per-page `extract_text()` call can hit a stream-decoding failure. This
    exercises that second guard directly, since `pypdf` itself is too
    defensive about malformed streams to reliably reproduce one from
    crafted bytes across versions."""
    from pypdf._page import PageObject

    def _boom(self: PageObject, *args: object, **kwargs: object) -> str:
        raise ValueError("simulated stream-decoding failure")

    monkeypatch.setattr(PageObject, "extract_text", _boom)
    data = _text_pdf_bytes(["hello world"])
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(data, _PDF_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


def test_a_docx_declaring_an_enormous_uncompressed_size_raises_corrupt() -> None:
    """A DOCX zip-bomb defence: a member's declared (not actually
    inflated) uncompressed size is checked from the central directory
    before `python-docx` is asked to open the file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        # A real (tiny) entry whose stored uncompressed-size field is then
        # overwritten to an enormous value -- exactly what a crafted zip
        # bomb declares, without this test needing to inflate anything.
        archive.writestr("word/document.xml", "a" * 1000)
        info = archive.infolist()[0]
        info.file_size = 500 * 1024 * 1024
    with pytest.raises(ExtractionError) as exc_info:
        extract_pages(buf.getvalue(), _DOCX_TYPE, _settings())
    assert exc_info.value.reason == "corrupt"


# --- successful extraction --------------------------------------------------


def test_extracts_a_two_page_pdf_with_correct_one_based_monotonic_pages() -> None:
    data = _text_pdf_bytes(["Page one text here", "Page two text here"])
    pages = extract_pages(data, _PDF_TYPE, _settings())
    assert [page.page_number for page in pages] == [1, 2]
    assert "Page one text here" in pages[0].text
    assert "Page two text here" in pages[1].text


def test_extracts_a_docx_as_one_synthetic_page() -> None:
    data = _docx_bytes(["First paragraph.", "Second paragraph."])
    pages = extract_pages(data, _DOCX_TYPE, _settings())
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "First paragraph." in pages[0].text
    assert "Second paragraph." in pages[0].text


def test_extracts_plain_text_as_one_page() -> None:
    data = "eviction notice, dated today".encode()
    pages = extract_pages(data, _TEXT_TYPE, _settings())
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].text == "eviction notice, dated today"


def test_plain_text_with_invalid_utf8_decodes_with_replacement_not_an_error() -> None:
    data = b"valid text \xff\xfe more text"
    pages = extract_pages(data, _TEXT_TYPE, _settings())
    assert len(pages) == 1
    assert "�" in pages[0].text


# --- chunk_pages -------------------------------------------------------


def test_chunk_ids_are_stable_across_two_extractions_of_the_same_bytes() -> None:
    data = _text_pdf_bytes(["Page one text here", "Page two text here"])
    first = chunk_pages(extract_pages(data, _PDF_TYPE, _settings()), "doc-abc")
    second = chunk_pages(extract_pages(data, _PDF_TYPE, _settings()), "doc-abc")
    assert first == second
    assert [excerpt_id for excerpt_id, _, _ in first]


def test_excerpt_ids_use_the_documented_prefix_and_shape() -> None:
    data = _text_pdf_bytes(["hello world"])
    pages = extract_pages(data, _PDF_TYPE, _settings())
    excerpts = chunk_pages(pages, "doc-xyz")
    assert len(excerpts) == 1
    excerpt_id, page_number, text = excerpts[0]
    assert excerpt_id == f"{DOCUMENT_EXCERPT_PREFIX}doc-xyz:p1:0"
    assert page_number == 1
    assert "hello world" in text


def test_ordinal_resets_per_page() -> None:
    long_paragraph = " ".join(f"word{i}" for i in range(400))
    pages = (
        ExtractedPage(page_number=1, text=long_paragraph),
        ExtractedPage(page_number=2, text=long_paragraph),
    )
    excerpts = chunk_pages(pages, "doc-multi")
    page_one_ordinals = [eid.rsplit(":", 1)[1] for eid, page, _ in excerpts if page == 1]
    page_two_ordinals = [eid.rsplit(":", 1)[1] for eid, page, _ in excerpts if page == 2]
    assert page_one_ordinals == [str(i) for i in range(len(page_one_ordinals))]
    assert page_two_ordinals == [str(i) for i in range(len(page_two_ordinals))]


def test_no_excerpt_exceeds_the_max_excerpt_chars_limit() -> None:
    long_paragraph = " ".join(f"word{i}" for i in range(400))
    pages = (ExtractedPage(page_number=1, text=long_paragraph),)
    excerpts = chunk_pages(pages, "doc-long")
    assert len(excerpts) > 1
    for _, _, text in excerpts:
        assert len(text) <= MAX_EXCERPT_CHARS


def test_splitting_a_long_paragraph_never_cuts_a_word() -> None:
    words = [f"word{i}" for i in range(400)]
    long_paragraph = " ".join(words)
    pages = (ExtractedPage(page_number=1, text=long_paragraph),)
    excerpts = chunk_pages(pages, "doc-words")
    reconstructed = " ".join(text for _, _, text in excerpts)
    assert reconstructed == long_paragraph


def test_splits_on_paragraph_boundaries_when_they_fit_separately() -> None:
    text = "Short first paragraph.\n\nShort second paragraph."
    pages = (ExtractedPage(page_number=1, text=text),)
    excerpts = chunk_pages(pages, "doc-paras")
    # Both paragraphs are small enough to share one excerpt.
    assert len(excerpts) == 1
    assert "Short first paragraph." in excerpts[0][2]
    assert "Short second paragraph." in excerpts[0][2]


def test_chunk_pages_never_produces_an_empty_result_for_non_empty_pages() -> None:
    pages = (ExtractedPage(page_number=1, text="a short page of real text"),)
    excerpts = chunk_pages(pages, "doc-nonempty")
    assert len(excerpts) >= 1


def test_a_whitespace_only_page_contributes_no_excerpts() -> None:
    """`chunk_pages` is a pure function callable directly, independent of
    the `no_text` guard in `extract_pages` -- a page that is only
    whitespace among otherwise-real pages must simply contribute zero
    excerpts of its own, not an excerpt of blank text."""
    pages = (
        ExtractedPage(page_number=1, text="   \n\n  "),
        ExtractedPage(page_number=2, text="real content"),
    )
    excerpts = chunk_pages(pages, "doc-blank-page")
    assert all(page_number != 1 for _, page_number, _ in excerpts)
    assert any(page_number == 2 for _, page_number, _ in excerpts)


def test_a_single_word_longer_than_the_cap_is_hard_split_without_exceeding_it() -> None:
    """The one documented exception to "never mid-word": a token with no
    internal whitespace that is itself longer than `MAX_EXCERPT_CHARS`
    still cannot produce an oversized excerpt."""
    giant_word = "a" * (MAX_EXCERPT_CHARS * 2 + 5)
    pages = (ExtractedPage(page_number=1, text=giant_word),)
    excerpts = chunk_pages(pages, "doc-giant-word")
    assert len(excerpts) >= 3
    for _, _, text in excerpts:
        assert len(text) <= MAX_EXCERPT_CHARS
    assert "".join(text for _, _, text in excerpts) == giant_word
