"""Plan 6 Task 10: the crisis scan over an uploaded document's text.

A person may never say the dangerous thing out loud. They upload a medical
report, a protection order, or an FIR copy and ask about rent arrears. If
crisis routing only ever reads the question, that person is answered with
tenancy law.

`scan_for_crisis` is the layer that closes it, and the tests here pin down
three decisions that are easy to get wrong and expensive to get wrong:

  * **It is the FREE regex layer, never the paid classifier.** No adapter,
    no registry, no model call -- asserted structurally below
    (`test_scan_for_crisis_takes_no_model_adapter`), not just by convention.
    Document text is attacker-controlled and up to `max_upload_bytes` long;
    feeding it to a classifier would both multiply cost with document
    length and hand an attacker a prompt-injection channel into the one
    decision that routes a person in danger. A regex cannot be talked out
    of a match.
  * **It runs once, at upload, over every page** -- not once per turn and
    not over page 1 only.
  * **Its language coverage is a stated limit, not a hidden one.** English,
    transliterated Hinglish, and (ruling 2) a narrow, precision-tuned
    Devanagari set are covered; every other Indic script is not. The
    compensating control is that the question path still runs the paid
    classifier, which reads every script, on EVERY turn -- so a Tamil
    speaker is never left with no crisis layer at all, only without the
    document one.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from openlaw.answers.escalation import CrisisKind, crisis_kind_from_value
from openlaw.documents.extract import ExtractedPage, scan_for_crisis

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_DIRS = (_REPO_ROOT / "src", _REPO_ROOT / "scripts")
#: The modules that DEFINE `scan_for_crisis` and `document_crisis_kinds`.
#: Excluded from the wiring guard below so a definition cannot be mistaken
#: for a call site.
_DEFINITIONS = frozenset(
    {
        _REPO_ROOT / "src" / "openlaw" / "documents" / "extract.py",
        _REPO_ROOT / "src" / "openlaw" / "language" / "pipeline.py",
    }
)


def _pages(*texts: str) -> tuple[ExtractedPage, ...]:
    return tuple(
        ExtractedPage(page_number=index + 1, text=text)
        for index, text in enumerate(texts)
    )


# --- the point of the feature ---------------------------------------------


def test_an_fir_copy_naming_domestic_violence_produces_the_dv_verdict() -> None:
    pages = _pages(
        "FIRST INFORMATION REPORT\nPolice Station: Saket\n",
        "The complainant states that her husband beats her and that she is "
        "afraid of my husband.",
    )

    assert scan_for_crisis(pages) is CrisisKind.DOMESTIC_VIOLENCE


def test_an_ordinary_tenancy_notice_produces_no_verdict() -> None:
    pages = _pages(
        "NOTICE TO QUIT\nYou are in arrears of rent for three months. "
        "Please vacate the premises within 15 days."
    )

    assert scan_for_crisis(pages) is None


def test_the_scan_reads_every_page_not_only_the_first() -> None:
    """A 20-page FIR can carry the only dangerous sentence on page 12."""
    pages = _pages(*(["Page of routine procedural text."] * 11 + ["domestic violence"]))

    assert scan_for_crisis(pages) is CrisisKind.DOMESTIC_VIOLENCE


def test_the_most_urgent_category_wins_across_pages() -> None:
    """Fail-safe, same rule as `detect_crisis`: resolve to whichever
    response gets help fastest, never to whichever page came first."""
    pages = _pages(
        "her husband beats her",
        "Her written statement says: there is no point in living.",
    )

    assert scan_for_crisis(pages) is CrisisKind.SELF_HARM


# --- cost and injection: which layer is allowed to read document text -----


def test_scan_for_crisis_takes_no_model_adapter() -> None:
    """The cost/injection decision, enforced by the signature rather than by
    a comment: there is nowhere to pass an `LLMAdapter` or a
    `ModelRegistry`, so no revision of this function can quietly start
    paying per document (or per turn, per document) without changing its
    interface and tripping this test."""
    parameters = inspect.signature(scan_for_crisis).parameters

    assert list(parameters) == ["pages"]


def test_the_verdict_is_storable_and_reads_back_as_the_same_kind() -> None:
    """The verdict is computed once and stored on `Document.crisis_kind`;
    every turn thereafter reads the column, never the text. That round trip
    has to be lossless or the stored verdict silently becomes no verdict."""
    verdict = scan_for_crisis(_pages("my husband keeps hitting me"))

    assert verdict is not None
    assert crisis_kind_from_value(verdict.value) is verdict


# --- extracted text is not typed text: it wraps ---------------------------


def test_a_phrase_broken_by_a_pdf_line_wrap_still_matches() -> None:
    """`pypdf` hard-wraps extracted text, and the regex layer's
    subject-to-verb gap is `.{0,20}`, which does not cross a newline. An FIR
    whose line happens to break between "husband" and "beats" is the same
    FIR; whitespace is normalised before matching so the layer behaves on
    extracted text the way it does on a typed question."""
    pages = _pages("The complainant states that her husband\nbeats her daily.")

    assert scan_for_crisis(pages) is CrisisKind.DOMESTIC_VIOLENCE


def test_empty_and_whitespace_only_pages_produce_no_verdict() -> None:
    assert scan_for_crisis(()) is None
    assert scan_for_crisis(_pages("", "   \n\n  ")) is None


# --- the wiring guard: this layer must not be able to land inert ----------
#
# Nothing in `src/` writes a `Document` row yet (the upload path is a later
# task), so today this scan has no production caller and the pipeline is
# always handed an empty `document_crisis_kinds`. That is the exact shape of
# the plan's carry-forward #3 -- "an unused fence protects nothing" -- and
# prose in a ledger is not what catches it. This is: the moment a production
# file constructs a `Document(...)`, both halves of the wiring must exist,
# or this test fails and says which half is missing.


def _production_sources() -> list[Path]:
    """Every shipped `.py` except the ORM table definitions themselves."""
    return [
        path
        for directory in _PRODUCTION_DIRS
        for path in directory.rglob("*.py")
        if path.name != "models.py"
    ]


def test_a_document_writer_calls_scan_for_crisis() -> None:
    """A crude grep, deliberately -- the same guard
    `tests/db/test_models.py::test_reserved_unused_tables_have_no_production
    _writer` uses, pointed at the safety obligation instead of the
    documentation one. (`\\bDocument\\(` does not match `python-docx`'s
    aliased `_DocxDocument(` in `extract.py`, for the same reason it does
    not there.)

    Plan 6 Task 14 lands the first `Document(...)` writer
    (`openlaw.documents.store.store_document`), which calls
    `scan_for_crisis(pages)` before constructing the row -- this half of the
    original combined guard now holds for real, not just by the absence of
    a writer. See `test_a_document_writer_also_threads_document_crisis_kinds`
    below for the other half, which does not yet hold.
    """
    texts = {path: path.read_text(encoding="utf-8") for path in _production_sources()}
    writers = [path for path, text in texts.items() if re.search(r"\bDocument\(", text)]
    assert writers, "expected a Document(...) writer to exist as of Plan 6 Task 14"

    body = "\n".join(text for path, text in texts.items() if path not in _DEFINITIONS)
    named = ", ".join(str(path.relative_to(_REPO_ROOT)) for path in writers)
    assert "scan_for_crisis(" in body, (
        f"{named} writes Document rows but nothing calls scan_for_crisis -- "
        "a document's crisis verdict would never be computed"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known, carried-forward gap (plan progress.md carry-forward #24): "
        "Task 14 lands the Document(...) writer and computes/stores "
        "crisis_kind at upload (openlaw.documents.store.store_document), "
        "but no production route yet threads it back into a live turn via "
        "answer_in_language(document_crisis_kinds=...) -- openlaw.api.deps "
        "is still single-turn. That wiring is Task 15's (conversation-aware "
        "endpoint). This marker must be removed, not left behind, the "
        "moment that call site lands -- an unexpected pass (XPASS) here "
        "means it already has."
    ),
)
def test_a_document_writer_also_threads_document_crisis_kinds() -> None:
    """The other half of the original combined guard. See
    `test_a_document_writer_calls_scan_for_crisis` above and this test's
    `xfail` reason for why this one is expected to fail today."""
    texts = {path: path.read_text(encoding="utf-8") for path in _production_sources()}
    writers = [path for path, text in texts.items() if re.search(r"\bDocument\(", text)]
    assert writers, "expected a Document(...) writer to exist as of Plan 6 Task 14"

    body = "\n".join(text for path, text in texts.items() if path not in _DEFINITIONS)
    named = ", ".join(str(path.relative_to(_REPO_ROOT)) for path in writers)
    assert "document_crisis_kinds=" in body, (
        f"{named} writes Document rows but nothing passes document_crisis_kinds "
        "to answer_in_language -- the stored verdict would never reach a turn"
    )


# --- language: what the document layer does and does not cover ------------


def test_transliterated_hinglish_document_text_matches() -> None:
    """Romanised Hindi is the script a great many Indian statements are
    actually typed in, and the regex layer does cover it -- so the document
    layer is not English-only."""
    pages = _pages("Bayan: mera pati mujhe roz maarta hai.")

    assert scan_for_crisis(pages) is CrisisKind.DOMESTIC_VIOLENCE


def test_a_devanagari_protection_order_is_now_seen() -> None:
    """Ruling 2. This is the case the first round of this task could not
    answer: a Hindi-script protection order attached to an innocuous
    question was invisible to every layer at once, because the paid
    classifier never reads document text and the free layer had no
    Devanagari patterns."""
    assert (
        scan_for_crisis(_pages("संरक्षण आदेश: पति मुझे मारता है।"))
        is CrisisKind.DOMESTIC_VIOLENCE
    )
    assert (
        scan_for_crisis(_pages("बयान: मैं अब जीना नहीं चाहती"))
        is CrisisKind.SELF_HARM
    )
    assert (
        scan_for_crisis(_pages("घरेलू हिंसा से महिलाओं का संरक्षण अधिनियम के तहत आदेश"))
        is CrisisKind.DOMESTIC_VIOLENCE
    )


@pytest.mark.parametrize(
    "text",
    [
        "किराया बकाया के बारे में क्या नियम है",
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
        "किरायेदार परिसर खाली करेगा। किराया हर माह की 5 तारीख तक देय है।",
        "मैंने पुलिस स्टेशन में शिकायत दर्ज कराई है",
        "तलाक की प्रक्रिया क्या है",
        "श्रम कानून के तहत मजदूरी का भुगतान",
        "यह पट्टा विलेख दिनांक 5 मई 2024 को निष्पादित किया गया",
    ],
)
def test_ordinary_hindi_legal_documents_do_not_trigger_a_verdict(text: str) -> None:
    """Precision over recall, which is the whole constraint on ruling 2's
    pattern set. A card that fires on every Hindi lease and rent notice is
    a card people learn to ignore, and under ruling 1 it would be appended
    to perfectly good legal answers all day."""
    assert scan_for_crisis(_pages(text)) is None


def test_other_indic_scripts_remain_a_documented_blind_spot() -> None:
    """The limit that ruling 2 narrowed but did not close, asserted so it
    cannot rot into a surprise. Devanagari is covered; Tamil, Bengali,
    Telugu and the rest are not, and neither is a Devanagari phrasing
    outside the deliberately narrow set (the Marathi line below is
    Devanagari script but not a Hindi crisis phrase).

    What is NOT done here is `openlaw.api.refusal`'s script hedge: firing on
    "this document is in an Indic script" would attach a helpline card to
    every document written in one, which is exactly the signal-destroying
    outcome the precision constraint exists to prevent. That hedge stays
    where it belongs, on the refusal path.
    """
    assert scan_for_crisis(_pages("என் கணவன் என்னை அடிக்கிறான்")) is None  # Tamil
    assert scan_for_crisis(_pages("আমার স্বামী আমাকে মারে")) is None  # Bengali
    assert scan_for_crisis(_pages("मला खूप भीती वाटते")) is None  # Marathi
    assert "Indic script" in (scan_for_crisis.__doc__ or "")
