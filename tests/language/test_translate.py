"""Tests for query and answer translation.

Unit tests use `StubAdapter` -- no network, no API key, matching the normal
state of this environment. The `@pytest.mark.integration` test talks to the
real Anthropic API and skips cleanly when no key is configured.

**Query translation deliberately does not gate on the detected language**
(a binding correction to the original task brief -- see the module
docstring in `translate.py`). Language detection has one measured, honestly
reported blind spot: Latin-script transliteration of an Indian language
(Hinglish, e.g. "mera pati mujhe maarta hai") carries no Indic Unicode
signal, and `langdetect` has no romanised-Hindi model, so such text silently
resolves to `"en"` (see `openlaw.language.detect`'s module docstring). If
query translation were skipped whenever the detector says `"en"`, every
Hinglish question would skip translation and hit an English-only corpus --
exactly the users least served by English-language legal help. So
`translate_query` always calls the translator for non-empty text; the system
prompt instructs it to return already-English text unchanged, making an
actually-English question cost one cheap no-op call rather than being
silently mis-served. `test_hinglish_query_is_translated_despite_being_mis_detected_as_english`
and `test_genuinely_english_query_costs_a_noop_translator_call` below cover
both halves of that decision explicitly.
"""

import json

import pytest

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.models import ModelRegistry, ModelRole, StubAdapter
from openlaw.answers.schema import Claim, DocumentClaim
from openlaw.answers.validate import (
    DocumentCitation,
    DroppedClaim,
    Source,
    ValidatedAnswer,
)
from openlaw.config import get_settings
from openlaw.language.glossary import Glossary, GlossaryTerm
from openlaw.language.translate import translate_answer, translate_query
from openlaw.retrieval.context import ChunkKind

TRANSLATED = json.dumps(
    {
        "summary": "जमा राशि वापस करनी होगी।",
        "claims": [{"id": 0, "text": "वापसी आवश्यक है।"}],
    },
    ensure_ascii=False,
)


def _glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत"},
            ),
        )
    )


def _answer() -> ValidatedAnswer:
    return ValidatedAnswer(
        unverified_summary="The deposit must be refunded within one month.",
        claims=(Claim(text="A refund is required.", chunk_ids=("a",)),),
        sources=(
            Source(
                chunk_id="a",
                source_url="https://www.indiacode.nic.in/handle/123456789/1",
                title="Model Tenancy Act",
                kind=ChunkKind.PROVISION,
            ),
        ),
        is_answerable=True,
    )


def _two_claim_answer() -> ValidatedAnswer:
    """An answer with two distinctly-cited claims, for reassembly tests where
    a single claim can't distinguish "correct by luck" from "correct by id"."""
    return ValidatedAnswer(
        unverified_summary="Two things are true.",
        claims=(
            Claim(text="A refund is required.", chunk_ids=("a",)),
            Claim(text="Notice must be given first.", chunk_ids=("b",)),
        ),
        sources=(
            Source(
                chunk_id="a",
                source_url="https://example.gov.in/a",
                kind=ChunkKind.PROVISION,
            ),
            Source(
                chunk_id="b",
                source_url="https://example.gov.in/b",
                kind=ChunkKind.PROVISION,
            ),
        ),
        is_answerable=True,
    )


# --- query translation ------------------------------------------------------


def test_empty_query_is_returned_unchanged_without_a_model_call() -> None:
    adapter = StubAdapter([])
    result = translate_query("   ", "en", adapter, ModelRegistry())

    assert result == "   "
    assert adapter.calls == []


def test_genuinely_english_query_costs_a_noop_translator_call() -> None:
    """A real English question is still sent through -- the model is told to
    return it unchanged. See the module docstring: gating on the detector's
    "en" verdict would also skip Hinglish, which is mis-detected as "en"."""
    text = "my landlord kept my deposit"
    adapter = StubAdapter([text])

    result = translate_query(text, "en", adapter, ModelRegistry())

    assert result == text
    assert len(adapter.calls) == 1


def test_non_english_query_is_translated() -> None:
    adapter = StubAdapter(["my landlord is not returning my deposit"])
    result = translate_query(
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा", "hi", adapter, ModelRegistry()
    )
    assert result == "my landlord is not returning my deposit"


def test_hinglish_query_is_translated_despite_being_mis_detected_as_english() -> None:
    """The core regression this correction guards against: Hinglish carries no
    Indic Unicode signal, so `detect_language` reports "en" for it. Because
    `translate_query` no longer gates on that verdict, the translator still
    sees -- and translates -- the text."""
    hinglish = "mera pati mujhe maarta hai"
    adapter = StubAdapter(["my husband beats me"])

    result = translate_query(hinglish, "en", adapter, ModelRegistry())

    assert result == "my husband beats me"
    assert len(adapter.calls) == 1


def test_query_translation_uses_the_translator_role_not_the_generator() -> None:
    """Translation must not burn frontier-model budget."""
    adapter = StubAdapter(["translated"])
    translate_query("प्रश्न", "hi", adapter, ModelRegistry())
    assert adapter.calls[0]["model"] == "claude-haiku-4-5"


def test_query_translation_honours_a_per_language_model_override() -> None:
    adapter = StubAdapter(["translated"])
    registry = ModelRegistry(
        language_overrides={(ModelRole.TRANSLATOR, "ta"): "indic-specialist-model"}
    )
    translate_query("கேள்வி", "ta", adapter, registry)
    assert adapter.calls[0]["model"] == "indic-specialist-model"


def test_failed_query_translation_falls_back_to_the_original() -> None:
    """A translation failure must not lose the user's question."""
    adapter = StubAdapter([""])
    assert translate_query("मूल प्रश्न", "hi", adapter, ModelRegistry()) == "मूल प्रश्न"


# --- answer translation ------------------------------------------------------


def test_english_answer_is_returned_unchanged() -> None:
    adapter = StubAdapter([])
    result = translate_answer(_answer(), "en", adapter, ModelRegistry(), _glossary())

    assert result.unverified_summary == _answer().unverified_summary
    assert adapter.calls == []


def test_answer_summary_and_claims_are_translated() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.unverified_summary == "जमा राशि वापस करनी होगी।"
    assert result.claims[0].text == "वापसी आवश्यक है।"


def test_translation_preserves_chunk_ids_exactly() -> None:
    """chunk_ids are identifiers -- translating one would break the citation."""
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.claims[0].chunk_ids == ("a",)


def test_translation_preserves_source_urls_exactly() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.sources[0].source_url == (
        "https://www.indiacode.nic.in/handle/123456789/1"
    )


def test_translation_preserves_answerability() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.is_answerable is True


def test_glossary_instruction_reaches_the_translator() -> None:
    adapter = StubAdapter([TRANSLATED])
    translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())
    assert "anticipatory bail" in adapter.calls[0]["system"]


def test_failed_answer_translation_falls_back_to_english() -> None:
    """Better an English answer than a mangled one."""
    result = translate_answer(
        _answer(), "hi", StubAdapter(["not json"]), ModelRegistry(), _glossary()
    )
    assert result.unverified_summary == _answer().unverified_summary


def test_claim_count_mismatch_falls_back_to_english() -> None:
    """A translator returning the wrong number of claims is a missing id (the
    required id 0 never appears) and cannot be reassembled."""
    adapter = StubAdapter(['{"summary": "अनुवादित", "claims": []}'])
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())
    assert result.unverified_summary == _answer().unverified_summary


def test_reordered_claims_reassemble_onto_their_own_chunk_ids() -> None:
    """The important case: a translator that returns claims out of order must
    still pair each translated text with the `chunk_ids` of the claim it was
    actually given -- not with whichever claim occupies that slot in the
    reply. Written so it would FAIL under position-based (zip-by-index)
    pairing: translated text for id 1 ("b"'s claim) is listed first in the
    response, and translated text for id 0 ("a"'s claim) second. Under naive
    positional pairing, response[0] ("id 1's" translated text) would be
    zipped onto original claim 0 (chunk_ids=("a",)) -- attaching a claim to
    a citation that does not support it."""
    reordered = json.dumps(
        {
            "summary": "दो बातें सत्य हैं।",
            "claims": [
                {"id": 1, "text": "पहले सूचना देनी होगी।"},
                {"id": 0, "text": "वापसी आवश्यक है।"},
            ],
        },
        ensure_ascii=False,
    )
    adapter = StubAdapter([reordered])

    result = translate_answer(
        _two_claim_answer(), "hi", adapter, ModelRegistry(), _glossary()
    )

    assert result.claims[0].chunk_ids == ("a",)
    assert result.claims[0].text == "वापसी आवश्यक है।"
    assert result.claims[1].chunk_ids == ("b",)
    assert result.claims[1].text == "पहले सूचना देनी होगी।"


def test_missing_claim_id_falls_back_to_english() -> None:
    """A response that drops one of the required ids cannot be reassembled --
    there is no translated text to pair with that claim's chunk_ids."""
    missing_one = json.dumps(
        {
            "summary": "दो बातें सत्य हैं।",
            "claims": [{"id": 0, "text": "वापसी आवश्यक है।"}],
        },
        ensure_ascii=False,
    )
    adapter = StubAdapter([missing_one])

    result = translate_answer(
        _two_claim_answer(), "hi", adapter, ModelRegistry(), _glossary()
    )

    assert result.unverified_summary == _two_claim_answer().unverified_summary
    assert result.claims == _two_claim_answer().claims


def test_invented_claim_id_falls_back_to_english() -> None:
    """A response carrying an id that was never sent cannot be trusted --
    accepting it could silently attach translated text to an id the caller
    never asked about."""
    invented = json.dumps(
        {
            "summary": "दो बातें सत्य हैं।",
            "claims": [
                {"id": 0, "text": "वापसी आवश्यक है।"},
                {"id": 7, "text": "पहले सूचना देनी होगी।"},
            ],
        },
        ensure_ascii=False,
    )
    adapter = StubAdapter([invented])

    result = translate_answer(
        _two_claim_answer(), "hi", adapter, ModelRegistry(), _glossary()
    )

    assert result.unverified_summary == _two_claim_answer().unverified_summary
    assert result.claims == _two_claim_answer().claims


def test_duplicated_claim_id_falls_back_to_english() -> None:
    """Two claims echoing the same id leave no way to tell which text is
    authoritative for the claim that id maps to."""
    duplicated = json.dumps(
        {
            "summary": "दो बातें सत्य हैं।",
            "claims": [
                {"id": 0, "text": "वापसी आवश्यक है।"},
                {"id": 0, "text": "एक अलग अनुवाद।"},
            ],
        },
        ensure_ascii=False,
    )
    adapter = StubAdapter([duplicated])

    result = translate_answer(
        _two_claim_answer(), "hi", adapter, ModelRegistry(), _glossary()
    )

    assert result.unverified_summary == _two_claim_answer().unverified_summary
    assert result.claims == _two_claim_answer().claims


def test_answer_translation_never_touches_dropped_claims_or_the_answerable_flag() -> (
    None
):
    """`dropped` carries only chunk ids, never prose -- nothing here for the
    translator to touch -- and it must survive translation unchanged, same
    as `is_answerable`."""
    answer = ValidatedAnswer(
        unverified_summary=_answer().unverified_summary,
        claims=_answer().claims,
        sources=_answer().sources,
        dropped=(DroppedClaim(unresolvable_chunk_ids=("ghost",)),),
        is_answerable=True,
    )
    result = translate_answer(
        answer, "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.dropped == answer.dropped
    assert result.is_answerable == answer.is_answerable


def test_translation_preserves_none_unverified_summary_when_not_answerable() -> None:
    """Final review, H2: `unverified_summary` must be `None` whenever
    `is_answerable` is False, on the TRANSLATED path too -- not just the
    English one `validate_answer` already enforces.

    Reachable case: a draft with surviving claims but `sufficient=false`
    gives `is_answerable=False` with a non-empty `claims` tuple
    (`openlaw.answers.validate.validate_answer`), so the `not answer.claims`
    guard at the top of `translate_answer` does NOT short-circuit -- the
    translator is called and returns a `summary`. Before the fix, that
    `summary` was written onto the result unconditionally, reintroducing
    unchecked model prose on a not-answerable answer for exactly the
    languages this pipeline translates into.
    """
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(Claim(text="A refund is required.", chunk_ids=("a",)),),
        sources=(Source(chunk_id="a", source_url="https://example.gov.in/a", kind=ChunkKind.PROVISION),),
        is_answerable=False,
    )
    result = translate_answer(
        answer, "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.unverified_summary is None


# --- integration: the real model actually honours the no-op instruction ----


@pytest.mark.integration
def test_translator_returns_hinglish_translated_and_english_unchanged_live() -> None:
    """Skips cleanly -- no Anthropic API key is configured in this environment."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    registry = ModelRegistry()

    hinglish_result = translate_query(
        "mera pati mujhe maarta hai", "en", adapter, registry
    )
    assert "beat" in hinglish_result.lower() or "hit" in hinglish_result.lower()

    english_text = "what documents do I need to register a rent agreement"
    english_result = translate_query(english_text, "en", adapter, registry)
    assert english_result.strip().lower() == english_text.lower()


# --- claim text must be a genuine, non-empty string --------------------------


def test_empty_translated_text_falls_back_to_english() -> None:
    """A lazy translator that returns `{"id": 0, "text": ""}` used to build
    `Claim(text="")`, which raises `ValidationError` -- an exception escaping
    both `translate_answer` and `answer_in_language` and turning an answered
    question into a crash. Empty text is a parse failure, handled exactly
    like a missing id: the English answer is returned untouched."""
    adapter = StubAdapter(['{"summary": "अनुवादित", "claims": [{"id": 0, "text": ""}]}'])
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())

    assert result == _answer()


def test_whitespace_only_translated_text_falls_back_to_english() -> None:
    adapter = StubAdapter(
        ['{"summary": "अनुवादित", "claims": [{"id": 0, "text": "   "}]}']
    )
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())

    assert result == _answer()


def test_non_string_translated_text_falls_back_to_english() -> None:
    """`str(item["text"])` coerced any JSON value into claim text: a response
    carrying `{"x": 1}` produced the claim text `"{'x': 1}"` bound to real,
    validated `chunk_ids` -- text nobody wrote or checked, wearing a real
    citation. Require a genuine string instead."""
    adapter = StubAdapter(
        ['{"summary": "अनुवादित", "claims": [{"id": 0, "text": {"x": 1}}]}']
    )
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())

    assert result == _answer()
    assert result.claims[0].text == "A refund is required."


def test_missing_translated_text_key_falls_back_to_english() -> None:
    adapter = StubAdapter(['{"summary": "अनुवादित", "claims": [{"id": 0}]}'])
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())

    assert result == _answer()


def test_one_empty_claim_rejects_the_whole_translation() -> None:
    """Partial acceptance would leave a Hindi answer with one silently blank
    claim; the whole translation is rejected instead."""
    adapter = StubAdapter(
        [
            json.dumps(
                {
                    "summary": "अनुवादित",
                    "claims": [{"id": 0, "text": "ठीक है।"}, {"id": 1, "text": ""}],
                },
                ensure_ascii=False,
            )
        ]
    )
    result = translate_answer(
        _two_claim_answer(), "hi", adapter, ModelRegistry(), _glossary()
    )

    assert result == _two_claim_answer()


# --- quoted statutory text stays in English ----------------------------------


def test_answer_prompt_keeps_quoted_statutory_text_in_English() -> None:
    """Spec §7: a translated statute is not the statute. The prompt must say
    so about QUOTED statutory text, not only about statute names and section
    numbers."""
    adapter = StubAdapter([TRANSLATED])
    translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())
    system = adapter.calls[0]["system"].lower()

    assert "quote" in system
    assert "english" in system


# --- document claims survive translation on EVERY return path --------------
#
# Plan 6 Task 13, ruling B. Document claims used to be DROPPED on every
# non-English return: a Hindi speaker who attached a lease was told nothing
# at all about their own paperwork. They are now translated by their own
# call, and on any failure of that call they degrade to the ENGLISH document
# claim -- never to nothing. `translate_answer` has three returns and the
# parametrisation below walks all of them, in both the
# document-translation-works and document-translation-fails directions,
# because this project has produced five defects where a property held in
# English and failed in translation.

_DOCUMENT_CLAIM = DocumentClaim(
    text="Your lease says 11 months.", excerpt_ids=("doc:a:p1:0",)
)
_DOCUMENT_CITATION = DocumentCitation(
    excerpt_id="doc:a:p1:0", document_id="a", filename="lease.pdf", page_number=1
)

#: A well-formed translation naming an id that was never sent -- one of the
#: three ways a parse returns None (the others being a refusal and malformed
#: JSON), all of which share the one "keep the English text" fallback.
_INVENTED_CLAIM_ID = json.dumps(
    {"summary": "अनुवादित", "claims": [{"id": 7, "text": "आविष्कृत"}]},
    ensure_ascii=False,
)

#: The document pass's own translated reply. No "summary" key: there is
#: deliberately no document-flavoured equivalent of `unverified_summary`.
_TRANSLATED_DOCUMENT = json.dumps(
    {"claims": [{"id": 0, "text": "आपका पट्टा 11 महीने का है।"}]}, ensure_ascii=False
)


def _answer_with_document_claims(*, has_legal_claims: bool) -> ValidatedAnswer:
    """An answer carrying an English document addendum, with or without the
    legal claims that decide which return path `translate_answer` takes.

    `has_legal_claims=False` is not a contrived case: it is the shape
    `validate_answer` produces for every document-only turn, because
    surviving document claims never make an answer answerable (rule 5).
    """
    base = _answer()
    return ValidatedAnswer(
        unverified_summary=base.unverified_summary if has_legal_claims else None,
        claims=base.claims if has_legal_claims else (),
        sources=base.sources if has_legal_claims else (),
        document_claims=(_DOCUMENT_CLAIM,),
        document_citations=(_DOCUMENT_CITATION,),
        is_answerable=has_legal_claims,
    )


def _queued(has_legal_claims: bool, legal_reply: str, document_reply: str) -> list[str]:
    """The adapter queue for one `translate_answer` call: the legal call
    first, and only when there are legal claims to translate, then the
    document call."""
    return ([legal_reply] if has_legal_claims else []) + [document_reply]


@pytest.mark.parametrize(
    ("has_legal_claims", "legal_reply"),
    [
        # The success path: both halves translated.
        pytest.param(True, TRANSLATED, id="successful-translation"),
        # `legal is None` because there were no claims -- the document-only
        # turn, which is every document-only turn (rule 5).
        pytest.param(False, TRANSLATED, id="no-legal-claims-short-circuit"),
        # `legal is None` because the reply was malformed.
        pytest.param(True, "not json at all", id="malformed-json-fallback"),
        # `legal is None` because the reply named an id never sent.
        pytest.param(True, _INVENTED_CLAIM_ID, id="invented-claim-id-fallback"),
    ],
)
def test_every_return_path_carries_the_translated_document_claims(
    has_legal_claims: bool, legal_reply: str
) -> None:
    """Ruling B: a Hindi answer must not lose the document addendum on any
    path, including the short-circuit and the error fallbacks. A failure on
    the LEGAL side must not cost the document side its translation -- they
    are separate calls for exactly that reason."""
    answer = _answer_with_document_claims(has_legal_claims=has_legal_claims)
    adapter = StubAdapter(_queued(has_legal_claims, legal_reply, _TRANSLATED_DOCUMENT))

    result = translate_answer(answer, "hi", adapter, ModelRegistry(), _glossary())

    assert result.document_claims == (
        DocumentClaim(text="आपका पट्टा 11 महीने का है।", excerpt_ids=("doc:a:p1:0",)),
    )
    assert result.document_citations == (_DOCUMENT_CITATION,)


@pytest.mark.parametrize(
    ("has_legal_claims", "legal_reply"),
    [
        pytest.param(True, TRANSLATED, id="successful-legal-translation"),
        pytest.param(False, TRANSLATED, id="no-legal-claims-short-circuit"),
        pytest.param(True, "not json at all", id="malformed-json-fallback"),
        pytest.param(True, _INVENTED_CLAIM_ID, id="invented-claim-id-fallback"),
    ],
)
def test_a_failed_document_translation_degrades_to_english_never_to_nothing(
    has_legal_claims: bool, legal_reply: str
) -> None:
    answer = _answer_with_document_claims(has_legal_claims=has_legal_claims)
    adapter = StubAdapter(_queued(has_legal_claims, legal_reply, "not json at all"))

    result = translate_answer(answer, "hi", adapter, ModelRegistry(), _glossary())

    assert result.document_claims == (_DOCUMENT_CLAIM,)
    assert result.document_citations == (_DOCUMENT_CITATION,)


def test_a_failed_legal_translation_still_keeps_the_english_legal_claims() -> None:
    """The other half of "degrades to English, never to nothing"."""
    answer = _answer_with_document_claims(has_legal_claims=True)
    adapter = StubAdapter(["not json at all", _TRANSLATED_DOCUMENT])

    result = translate_answer(answer, "hi", adapter, ModelRegistry(), _glossary())

    assert result.claims == _answer().claims
    assert result.sources == _answer().sources
    assert result.document_claims[0].text == "आपका पट्टा 11 महीने का है।"


def test_the_document_translation_is_a_separate_call_from_the_legal_one() -> None:
    """Structural (ruling A, at the translation edge): a document claim's
    text is the one piece of prose an injected document can influence, so it
    must never share a prompt with the legal claims whose translation it
    could otherwise steer."""
    answer = _answer_with_document_claims(has_legal_claims=True)
    adapter = StubAdapter([TRANSLATED, _TRANSLATED_DOCUMENT])

    translate_answer(answer, "hi", adapter, ModelRegistry(), _glossary())

    assert len(adapter.calls) == 2
    legal_call, document_call = adapter.calls
    assert _DOCUMENT_CLAIM.text not in json.dumps(legal_call["messages"])
    assert "A refund is required." not in json.dumps(document_call["messages"])
    assert legal_call["system"] != document_call["system"]


def test_the_document_translator_prompt_says_these_are_not_the_law() -> None:
    answer = _answer_with_document_claims(has_legal_claims=False)
    adapter = StubAdapter([_TRANSLATED_DOCUMENT])

    translate_answer(answer, "hi", adapter, ModelRegistry(), _glossary())
    system = adapter.calls[0]["system"].lower()

    assert "not statements of law" in system
    assert "excerpt" not in system or "id" in system


def test_an_answer_with_no_document_claims_costs_no_document_call() -> None:
    adapter = StubAdapter([TRANSLATED])

    translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())

    assert len(adapter.calls) == 1


def test_an_english_answer_keeps_its_document_claims_untranslated() -> None:
    """An English answer needs no translation at all, so neither call is
    made and the addendum survives untouched."""
    answer = _answer_with_document_claims(has_legal_claims=True)
    adapter = StubAdapter([])

    result = translate_answer(answer, "en", adapter, ModelRegistry(), _glossary())

    assert adapter.calls == []
    assert result.document_claims == (_DOCUMENT_CLAIM,)
    assert result.document_citations == (_DOCUMENT_CITATION,)
