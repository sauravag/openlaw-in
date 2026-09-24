"""Tests for the localised end-to-end answer pipeline.

Binding corrections over the original task-4 brief (see task-4-report.md for
the full accounting):

  * The brief's illustrative `answer_in_language` called the public
    `generate_answer(question, ...)` with the ORIGINAL, untranslated
    question, and only after `retriever.retrieve` had already run. That
    means crisis routing there would have happened AFTER retrieval, not
    before it -- exactly backwards from what
    `openlaw.answers.generate`'s module docstring requires.
  * Crisis routing runs twice, but only the FIRST check (on the original
    text) uses the full `detect_crisis_with_classifier`. The second check
    (on the English translation) is the bare, free `detect_crisis` regex --
    the CLASSIFIER-role model already covers non-Latin scripts on the first
    pass (see its own module docstring), so asking it again on a
    translation of text it already read buys no new coverage and doubles
    cost. An ordinary, non-crisis question therefore costs at most one
    classifier call per turn (plus one translator call for the query, and
    one generator call).
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
  * `LocalisedAnswer.classifier_failures` carries any failure the
    original-text classifier call recorded through to the caller, matching
    the "build now, persist later" split `generate_answer` uses for the
    same event type.
"""

import json
from pathlib import Path
from typing import Any

from openlaw.answers.escalation import CrisisKind
from openlaw.answers.generate import persist_answer_events
from openlaw.answers.models import Completion, ModelRegistry, ModelRole, StubAdapter
from openlaw.config import Settings
from openlaw.language.detect import detect_language
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


class _ClassifierFailsAdapter:
    """Simulates a classifier outage: any crisis-classifier call raises;
    every other call pops the next response off a queue, in order. Mirrors
    `tests/answers/test_generate.py`'s
    `_ClassifierFailsThenGeneratorSucceedsAdapter`.

    Distinguished by the system prompt, not the model name: the
    TRANSLATOR and CLASSIFIER roles share the same default model
    (`claude-haiku-4-5`), so `kwargs["model"]` alone cannot tell them apart.
    `CLASSIFIER_SYSTEM_PROMPT` is the only system prompt containing "safety
    classifier".
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> Completion:
        self.calls.append(kwargs)
        if "safety classifier" in kwargs["system"]:
            raise RuntimeError("simulated classifier outage")
        return Completion(text=self._responses.pop(0))


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


def _generation_with_a_ghost_citation() -> str:
    """A generation citing a chunk_id the retriever never returned -- the
    validator drops that claim and a `HallucinationEvent` is built for it."""
    return json.dumps(
        {
            "summary": "The deposit must be refunded.",
            "claims": [
                {"text": "A refund is required.", "chunk_ids": ["a"]},
                {"text": "The landlord goes to prison.", "chunk_ids": ["ghost"]},
            ],
            "sufficient": True,
        }
    )


# --- English, no crisis ------------------------------------------------


def test_english_question_answer_is_not_translated() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,  # crisis check on the original text
            "my landlord kept my deposit",  # translate_query (no-op echo)
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


def test_an_ordinary_question_costs_at_most_one_classifier_call() -> None:
    """The second, translated-text crisis check is regex-only (free) -- it
    must not consume an adapter call, so an ordinary turn needs exactly 3
    calls: classifier, translator (query), generator."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _generation(),
        ]
    )
    answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )
    assert len(adapter.calls) == 3


def test_footer_is_present_in_every_localised_answer() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
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


def test_classifier_failure_on_original_text_survives_into_the_result() -> None:
    """Binding requirement: a classifier outage must be discoverable and
    countable after the fact -- dropping the failure here would silently
    defeat `openlaw.answers.generate.persist_answer_events`'s whole purpose."""
    adapter = _ClassifierFailsAdapter(["my landlord kept my deposit", _generation()])
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.crisis is None
    assert result.answer.is_answerable is True
    assert len(result.classifier_failures) == 1
    assert result.classifier_failures[0].question == "my landlord kept my deposit"


# --- Hindi: translated before retrieval, validated before translation --


def test_hindi_question_is_translated_before_retrieval() -> None:
    retriever = _Retriever()
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord is not returning my deposit",
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
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"


def test_english_answer_carries_the_untranslated_claim_alongside_the_translation() -> None:
    """`english_answer` exists for `scripts/review_translations.py`: a
    native-speaker reviewer must see the untranslated English claim next to
    the translated one to judge whether the legal meaning survived."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.english_answer.claims[0].text == "A refund is required."
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"


def test_english_answer_equals_answer_for_an_english_question() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _generation(),
        ]
    )
    result = answer_in_language(
        "my landlord kept my deposit", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.english_answer == result.answer


def test_english_answer_is_unanswerable_on_a_crisis_short_circuit() -> None:
    adapter = StubAdapter([_classifier_response(CrisisKind.SELF_HARM)])
    result = answer_in_language(
        "I want to end it all", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.english_answer.is_answerable is False
    assert result.english_answer.claims == ()


def test_citations_survive_the_round_trip() -> None:
    """The whole point: translation must not disturb the verified citation."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
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
    then give the regex layer a second, free chance on the English result.
    Only 2 adapter calls total -- the second check never touches the model."""
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


# --- explicit language: the caller's choice, not only the detector's -------


MARATHI_TRANSLATED = json.dumps(
    {
        "summary": "ठेव परत करावी लागेल.",
        "claims": [{"id": 0, "text": "परतावा आवश्यक आहे."}],
    },
    ensure_ascii=False,
)

HINDI_TRANSLATED = TRANSLATED


def _multilingual_glossary() -> Glossary:
    """A glossary pinning one term in both Hindi and Marathi, so a Marathi
    request can be told apart from a Hindi one by the instruction text."""
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत", "mr": "अटकपूर्व जामीन"},
            ),
        )
    )


def test_explicit_marathi_produces_marathi_output_and_marathi_glossary_rules() -> None:
    """`detect_by_script` maps all Devanagari to Hindi, so without an explicit
    choice a Marathi speaker gets Hindi output and the Marathi glossary
    entries can never be used at all. The caller supplies the choice."""
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _generation(),
            MARATHI_TRANSLATED,
        ]
    )
    result = answer_in_language(
        "माझ्या घरमालकाने ठेव परत केली नाही",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _multilingual_glossary(),
        language="mr",
    )

    assert result.language == "mr"
    assert result.answer.claims[0].text == "परतावा आवश्यक आहे."
    translation_system = adapter.calls[-1]["system"]
    assert "Marathi" in translation_system
    assert "अटकपूर्व जामीन" in translation_system
    assert "अग्रिम जमानत" not in translation_system


def test_explicit_hindi_answers_a_hinglish_question_in_hindi() -> None:
    """Hinglish detects as English (no Indic characters, no romanised-Hindi
    model), so detection alone answers a Hinglish speaker in English. An
    explicit request for Hindi must translate the answer too, not just the
    query."""
    question = "mera makan malik deposit wapas nahi kar raha"
    assert detect_language(question) == "en"

    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord is not returning my deposit",
            _generation(),
            HINDI_TRANSLATED,
        ]
    )
    result = answer_in_language(
        question,
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        language="hi",
    )

    assert result.language == "hi"
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"


def test_detection_remains_the_default_when_no_language_is_given() -> None:
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "english query",
            _generation(),
            TRANSLATED,
        ]
    )
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.language == "hi"


def test_explicit_language_is_reported_on_a_crisis_short_circuit() -> None:
    adapter = StubAdapter([])
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        language="mr",
    )
    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert result.language == "mr"


def test_explicit_language_routes_the_query_translator_model_too() -> None:
    """The per-language model override must follow the caller's choice, not
    the detector's guess -- an Indic-specialist translator model configured
    for Marathi is useless if the query call is still routed as Hindi."""
    registry = ModelRegistry(language_overrides={(ModelRole.TRANSLATOR, "mr"): "mr-1"})
    adapter = StubAdapter(
        [
            _SAFE_CLASSIFIER_RESPONSE,
            "my landlord kept my deposit",
            _generation(),
            MARATHI_TRANSLATED,
        ]
    )
    answer_in_language(
        "माझ्या घरमालकाने ठेव परत केली नाही",
        _Retriever(),
        adapter,
        registry,
        _multilingual_glossary(),
        language="mr",
    )

    assert adapter.calls[1]["model"] == "mr-1"  # query translation
    assert adapter.calls[-1]["model"] == "mr-1"  # answer translation


# --- events built here are persistable at the boundary --------------------


def test_localised_answer_events_reach_persist_answer_events(tmp_path: Path) -> None:
    """Binding requirement (Plan 3, re-asserted here): a sustained classifier
    outage or a run of hallucinations must be discoverable after the fact.
    The multilingual path builds both event kinds; the boundary persists
    them through the same `persist_answer_events` the English path uses."""
    adapter = _ClassifierFailsAdapter(
        ["my landlord kept my deposit", _generation_with_a_ghost_citation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )
    assert result.events
    assert result.classifier_failures

    settings = Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        hallucination_log_path=tmp_path / "hallucinations.jsonl",
        classifier_failure_log_path=tmp_path / "classifier_failures.jsonl",
    )
    persist_answer_events(result, settings)

    assert (tmp_path / "hallucinations.jsonl").read_text(encoding="utf-8").strip()
    assert (tmp_path / "classifier_failures.jsonl").read_text(encoding="utf-8").strip()
