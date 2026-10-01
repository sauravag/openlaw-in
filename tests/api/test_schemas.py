"""Tests for `openlaw.api.schemas` -- request validation and response shaping.

The launch-language gate (final whole-plan review, H4) belongs here: Plan 4's
review restricted launch to English and Hindi because only Hindi has every
glossary term verified by a native speaker
(`scripts/review_translations.py`). Before this fix that restriction existed
ONLY in `web/lib/languages.ts` -- a UI affordance a plain `fetch` to `/ask`
could simply bypass, e.g. `{"language": "ta"}`, and receive Tamil legal prose
translated with zero pinned terminology, wearing a real government citation.
`AskRequest.language`'s validator (`schemas.LAUNCH_LANGUAGES`) is the actual
enforcement; `test_frontend_language_list_matches_the_launch_gate` below is
the boundary test that keeps the UI list from silently drifting wider than
what the server will actually accept -- the mirror image of
`tests/answers/test_fallback_helpline_consistency.py`, which does the same
kind of Python-reads-TypeScript check for the helpline fallback data.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from openlaw.answers.schema import Claim, DocumentClaim
from openlaw.answers.validate import DocumentCitation, Source, ValidatedAnswer
from openlaw.api.schemas import (
    LAUNCH_LANGUAGES,
    AskRequest,
    AskResponse,
    SourceCard,
    to_response,
)
from openlaw.language.pipeline import LocalisedAnswer
from openlaw.retrieval.context import ChunkKind

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LANGUAGES_TS_PATH = _REPO_ROOT / "web" / "lib" / "languages.ts"

#: Matches `{ code: 'xx', label: '...' }` entries inside `LANGUAGES`'s array
#: literal in `languages.ts`.
_CODE_PATTERN = re.compile(r"code:\s*'([a-z]{2})'")


def _frontend_language_codes() -> frozenset[str]:
    source = _LANGUAGES_TS_PATH.read_text(encoding="utf-8")
    # The declaration is `export const LANGUAGES: readonly Language[] = [...]`
    # -- `Language[]` itself contains a `[]` pair, so the match must anchor on
    # the `=` that starts the actual array literal, not merely "the first
    # `[...]` after LANGUAGES".
    match = re.search(r"LANGUAGES[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match is not None, (
        f"could not find LANGUAGES's array literal in {_LANGUAGES_TS_PATH} "
        "-- did its declaration shape change?"
    )
    return frozenset(_CODE_PATTERN.findall(match.group(1)))


def test_languages_ts_file_is_where_this_test_expects_it() -> None:
    assert _LANGUAGES_TS_PATH.is_file()


def test_frontend_language_list_matches_the_launch_gate() -> None:
    """The UI's language picker must offer exactly the languages the server
    will accept -- no more (an unglossaried language reachable through the
    UI, H4's exact failure mode) and no less (a language the server accepts
    but the UI never offers, a silently unreachable feature)."""
    assert _frontend_language_codes() == LAUNCH_LANGUAGES


@pytest.mark.parametrize("language", sorted(LAUNCH_LANGUAGES))
def test_ask_request_accepts_every_launch_language(language: str) -> None:
    AskRequest(question="q", language=language)


def test_ask_request_rejects_a_glossary_incomplete_language() -> None:
    """Reproduces the H4 finding directly: a plain request for Tamil -- no
    auth, no special client -- must be rejected at this boundary, not routed
    to a translator with no pinned terminology."""
    with pytest.raises(ValidationError, match="language must be one of"):
        AskRequest(question="q", language="ta")


def test_ask_request_rejects_every_non_launch_supported_language() -> None:
    """Every one of `openlaw.language.detect.SUPPORTED_LANGUAGES` other than
    the launch set must still be rejected here -- the gate is the API
    boundary, not `SUPPORTED_LANGUAGES` itself (which the translation
    pipeline's detector still recognises for its own, separate purposes)."""
    from openlaw.language.detect import SUPPORTED_LANGUAGES

    for language in SUPPORTED_LANGUAGES:
        if language in LAUNCH_LANGUAGES:
            continue
        with pytest.raises(ValidationError):
            AskRequest(question="q", language=language)


# --- what a citation carries onto the wire ---------------------------------
#
# Plan 6 Task 7. `SourceCard` mirrored `Source`'s three fields, so `/ask`
# gave the browser no `kind`, no `case_id`, no `decision_date`, no `judges`
# and no `headnote`: every judgment arrived as a null title above a raw
# tarball URL, and `web/lib/sourceKind.ts` had to guess the citation's kind
# from its hostname. The server has always known all of it.


def _localised_with(source: Source) -> LocalisedAnswer:
    answer = ValidatedAnswer(
        unverified_summary="unverified model prose -- must never render",
        claims=(Claim(text="Notice is required.", chunk_ids=(source.chunk_id,)),),
        sources=(source,),
        is_answerable=True,
    )
    return LocalisedAnswer(
        answer=answer,
        english_answer=answer,
        language="en",
        english_query="q",
        crisis=None,
        footer="NALSA: 15100",
    )


def test_a_judgment_reaches_the_client_with_everything_needed_to_cite_it() -> None:
    source = Source(
        chunk_id="1963_INSC_244_c3",
        source_url="https://example.s3.amazonaws.com/data/tar/1963_INSC_244.tar",
        kind=ChunkKind.JUDGMENT,
        case_id="1963_INSC_244",
        court_code="SC",
        decision_date="1963-04-05",
        judges=("K Subba Rao",),
        headnote="Whether notice was required.",
    )

    card = to_response(_localised_with(source), "v1", 5).sources[0]

    assert card.kind is ChunkKind.JUDGMENT
    assert card.case_id == "1963_INSC_244"
    assert card.court_code == "SC"
    assert card.decision_date == "1963-04-05"
    assert card.judges == ("K Subba Rao",)
    assert card.headnote == "Whether notice was required."


def test_a_provision_card_leaves_judgment_fields_absent_rather_than_invented() -> None:
    source = Source(
        chunk_id="IND_central_2385_388",
        source_url="https://www.indiacode.nic.in/handle/123456789/2385",
        kind=ChunkKind.PROVISION,
        title="The Indian Succession Act, 1925",
    )

    card = to_response(_localised_with(source), "v1", 5).sources[0]

    assert card.kind is ChunkKind.PROVISION
    assert card.case_id is None
    assert card.judges == ()


def test_the_source_card_has_no_field_a_document_excerpt_could_fill() -> None:
    """`/ask` publishes citations of the LAW. There is no `excerpt_id`,
    `filename` or `page_number` on this shape, so no route exists by which a
    user's uploaded document could be serialised into the source list a
    reader treats as legal provenance."""
    for field in ("excerpt_id", "document_id", "filename", "page_number"):
        assert field not in SourceCard.model_fields


def test_the_response_never_carries_the_unverified_summary() -> None:
    source = Source(
        chunk_id="a",
        source_url="https://www.indiacode.nic.in/handle/1",
        kind=ChunkKind.PROVISION,
    )

    response = to_response(_localised_with(source), "v1", 5)

    assert "unverified_summary" not in AskResponse.model_fields
    assert "must never render" not in response.model_dump_json()


def test_the_kind_reaches_the_browser_as_a_plain_string() -> None:
    """`web/lib/sourceKind.ts` compares it against string literals, so the
    wire value -- not the Python enum's repr -- is what has to be right."""
    source = Source(
        chunk_id="c",
        source_url="https://example.s3.amazonaws.com/x.tar",
        kind=ChunkKind.JUDGMENT,
    )

    payload = to_response(_localised_with(source), "v1", 5).model_dump(mode="json")

    assert payload["sources"][0]["kind"] == "judgment"


def test_document_claims_cannot_make_a_response_answerable() -> None:
    """Security review gap: `is_answerable` is computed in exactly two
    places -- `validate_answer` (legal claims only) and here, where it is
    copied -- and nothing pinned the second. A turn whose only surviving
    content is the user's own paperwork must render the fixed refusal, not
    an answer, and must not leak the document prose into `answer`.
    """
    answer = ValidatedAnswer(
        unverified_summary=None,
        document_claims=(
            DocumentClaim(text="Your notice is dated 4 March.", excerpt_ids=("doc:a:p1:0",)),
        ),
        document_citations=(
            DocumentCitation(
                excerpt_id="doc:a:p1:0",
                document_id="a",
                filename="notice.pdf",
                page_number=1,
            ),
        ),
        is_answerable=False,
    )
    localised = LocalisedAnswer(
        answer=answer,
        english_answer=answer,
        language="en",
        english_query="q",
        crisis=None,
        footer="NALSA: 15100",
    )

    response = to_response(localised, "v1", 5)

    assert response.is_answerable is False
    assert response.answer.startswith("We could not find a verifiable answer")
    assert response.sources == []
    assert "4 March" not in response.model_dump_json()
