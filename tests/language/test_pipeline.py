"""Tests for the localised end-to-end answer pipeline.

Binding corrections over the original task-4 brief (see task-4-report.md for
the full accounting):

  * The brief's illustrative `answer_in_language` called the public
    `generate_answer(question, ...)` with the ORIGINAL, untranslated
    question, and only after `retriever.retrieve` had already run. That
    means crisis routing there would have happened AFTER retrieval, not
    before it -- exactly backwards from what
    `openlaw.answers.generate`'s module docstring requires. This module
    instead runs `detect_crisis_with_classifier` itself, twice (original
    text, then the English translation), both times before the retriever is
    ever touched, and only then calls `generate._generate_from_context`
    directly (mirroring `generate.answer_question`'s own trick) so the
    already-cleared `english_query` is not classified a third time.
  * `translate_query` no longer skips its model call when the detected
    language is `"en"` (see `openlaw.language.translate`'s module
    docstring) -- so every non-crisis turn queues one translator call even
    for a genuinely English question. Every StubAdapter response list below
    accounts for that.
  * `ValidatedAnswer` has no `.summary` attribute -- it is
    `.unverified_summary`, and it is never safe to render (see
    `openlaw.answers.validate`'s module docstring). Tests here assert on
    `.claims[].text`, never on summary text, matching the "compose the
    displayed answer from claims and sources only" binding requirement.
"""

import json

from openlaw.answers.escalation import CrisisKind
from openlaw.answers.models import ModelRegistry, StubAdapter
from openlaw.language.glossary import Glossary, GlossaryTerm
from openlaw.language.pipeline import answer_in_language
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk

TRANSLATED = json.dumps(
    {
        "summary": "जमा राशि वापस करनी होगी।",
        "claims": [{"id": 0, "text": "वापसी आवश्यक है।"}],
    },
    ensure_ascii=False,
)


def _classifier_response(kind: CrisisKind | None = None) -> str:
    """The classifier's JSON reply -- "none" unless `kind` is given."""
    return json.dumps({"crisis": kind.value if kind is not None else "none"})


#: Queued ahead of every crisis check below whose input the regex is silent
#: on -- mirrors `tests/answers/test_generate.py`'s `_SAFE_CLASSIFIER_RESPONSE`.
_SAFE_CLASSIFIER_RESPONSE = _classifier_response()


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


class _Retriever:
    """Records the query it was given and returns a fixed context."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, english_query: str) -> RetrievalContext:
        self.queries.append(english_query)
        return RetrievalContext(
            query=english_query,
            chunks=(
                RetrievedChunk(
                    chunk_id="a",
                    text="A landlord shall refund the deposit.",
                    source_url="https://www.indiacode.nic.in/handle/123456789/1",
                    score=0.9,
                    kind=ChunkKind.PROVISION,
                ),
            ),
        )


class _RaisingRetriever:
    """A retriever that fails the test if it is ever called.

    Used to prove that a crisis short-circuit never reaches retrieval --
    see the module docstring's binding correction.
    """

    def retrieve(self, english_query: str) -> RetrievalContext:
        raise AssertionError(
            "retriever.retrieve must not be called once a crisis is detected"
        )


def _generation() -> str:
    return json.dumps(
        {
            "summary": "The deposit must be refunded.",
            "claims": [{"text": "A refund is required.", "chunk_ids": ["a"]}],
            "sufficient": True,
        }
    )


def _insufficient_generation() -> str:
    return json.dumps({"summary": "No answer.", "claims": [], "sufficient": False})


# --- English, no crisis ------------------------------------------------


def test_english_question_answer_is_not_translated() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,  # crisis check on the original text
            "my landlord kept my deposit",  # translate_query (no-op echo)
            _SAFE_CLASSIFIER_RESPONSE,  # crisis check on the translated text
            _generation(),
        ]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.language == "en"
    assert result.crisis is None
    assert result.answer.claims[0].text == "A refund is required."


def test_footer_is_present_in_every_localised_answer() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
        ]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )
    assert "15100" in result.footer


def test_unanswerable_result_never_carries_unverified_summary_prose() -> None:
    """Binding requirement: the displayed answer must come from claims and
    sources only. An unanswerable result must carry no renderable prose,
    translated or not."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _SAFE_CLASSIFIER_RESPONSE,
            _insufficient_generation(),
        ]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.answer.is_answerable is False
    assert result.answer.unverified_summary is None
    assert result.answer.claims == ()


# --- Hindi: translated before retrieval, validated before translation --


def test_hindi_question_is_translated_before_retrieval() -> None:
    retriever = _Retriever()
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord is not returning my deposit",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
        retriever,
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.language == "hi"
    assert retriever.queries == ["my landlord is not returning my deposit"]


def test_retrieval_always_receives_english() -> None:
    retriever = _Retriever()
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    answer_in_language("हिंदी प्रश्न", retriever, adapter, ModelRegistry(), _glossary())
    assert retriever.queries[0] == "english query"


def test_answer_is_returned_in_the_users_language() -> None:
    """The displayed claim text is translated -- never the unverified summary
    (see `ValidatedAnswer.unverified_summary`'s docstring: nothing renders it)."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"


def test_citations_survive_the_round_trip() -> None:
    """The whole point: translation must not disturb the verified citation."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )

    assert result.answer.claims[0].chunk_ids == ("a",)
    assert result.answer.sources[0].source_url.startswith("https://")


def test_the_english_query_is_reported_for_debugging() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.english_query == "english query"


def test_a_broken_translation_falls_back_to_the_english_answer() -> None:
    """Binding requirement: an id problem in the translated answer fails
    closed to the untouched English answer, never a mangled or partial one."""
    broken = json.dumps({"summary": "x", "claims": [{"id": 99, "text": "y"}]})
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            broken,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.answer.claims[0].text == "A refund is required."


def test_glossary_is_wired_into_the_answer_translation_call() -> None:
    """Binding requirement: pinned legal terminology (and, by the same
    mechanism, preserved-English statutory names) reaches the translator."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _SAFE_CLASSIFIER_RESPONSE,
            _generation(),
            TRANSLATED,
        ]
    )
    answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    translation_call = adapter.calls[-1]
    assert "anticipatory bail" in translation_call["system"]


# --- Crisis routing: first, and on both the original and translated text --


def test_regex_crisis_on_original_text_short_circuits_before_any_adapter_call() -> (
    None
):
    adapter = StubAdapter([])
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert len(adapter.calls) == 0
    assert "181" in result.footer


def test_classifier_crisis_on_original_text_short_circuits_before_retrieval() -> None:
    adapter = StubAdapter([_classifier_response(CrisisKind.SELF_HARM)])
    result = answer_in_language(
        "I want to end it all",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.crisis is CrisisKind.SELF_HARM
    assert "14416" in result.footer
    assert len(adapter.calls) == 1


def test_crisis_found_only_after_translation_short_circuits_before_retrieval() -> (
    None
):
    """The cheapest way to cover a script the regex cannot match: translate,
    then give the regex layer a second, free chance on the English result."""
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, "he beats me"])
    result = answer_in_language(
        "कुछ और सवाल",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert len(adapter.calls) == 2
