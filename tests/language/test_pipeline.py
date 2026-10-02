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

import inspect
import json
from pathlib import Path
from typing import Any

from openlaw.answers.escalation import CrisisKind, crisis_response, nalsa_helpline
from openlaw.documents.extract import ExtractedPage, scan_for_crisis
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


# --- Plan 6 Task 10: crisis routing on EVERY turn, and into documents -----
#
# A person may reveal danger on the fourth turn, not the first -- or never
# say it at all, and instead upload a protection order, a medical report, or
# an FIR copy that makes it plain. Both cases are missed if crisis routing
# only ever inspects the current question on the first turn.
#
# `answer_in_language` is called once per turn, so "turn N" here means the
# Nth call. The stored, regex-only verdict of each attached document arrives
# as `document_crisis_kinds` -- the `Document.crisis_kind` column value,
# verbatim, read from the row and NEVER recomputed from document text (see
# `openlaw.documents.extract.scan_for_crisis`, which runs once at upload).

#: What `Document.crisis_kind` holds for an uploaded FIR the upload-time scan
#: found domestic violence in. A column value, not an enum -- that is exactly
#: the shape the turn assembly will hand over.
STORED_DV = CrisisKind.DOMESTIC_VIOLENCE.value

#: One attached document the scan cleared (`crisis_kind` IS NULL).
NO_DOCUMENT_VERDICT: tuple[str | None, ...] = (None,)


def test_a_document_verdict_rides_alongside_the_answer_on_turn_five() -> None:
    """The headline case, as ruling 1 settles it: the question is about
    rent, the attached FIR is about violence, and it is the fifth turn. She
    gets her legal answer AND the helpline card -- not one instead of the
    other. A crisis phrase inside an uploaded file is weaker evidence than a
    person saying it: it may be historical, incidental, or about someone
    else, so it must not replace the question she actually asked."""
    retriever = _Retriever()
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()] * 5
    )
    for _ in range(5):
        result = answer_in_language(
            "my landlord kept my deposit",
            retriever,
            adapter,
            ModelRegistry(),
            _glossary(),
            document_crisis_kinds=(STORED_DV,),
        )

    # The legal answer survives intact...
    assert result.answer.claims[0].text == "A refund is required."
    assert result.answer.is_answerable is True
    # ...and the helpline rides alongside it.
    assert result.document_crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert "181" in result.footer
    assert "181" in {helpline.number for helpline in result.helplines}
    # `crisis` stays None: the turn was NOT short-circuited, and
    # `to_response` branches on that to decide whether to render the answer.
    assert result.crisis is None


def test_a_document_verdict_never_suppresses_the_legal_answer_on_any_turn() -> None:
    """Every turn, not only the first and not only the fifth: the verdict is
    re-read from the stored column and re-attached, and the answer is
    returned every time."""
    retriever = _Retriever()
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()] * 4
    )
    results = [
        answer_in_language(
            "my landlord kept my deposit",
            retriever,
            adapter,
            ModelRegistry(),
            _glossary(),
            document_crisis_kinds=(STORED_DV,),
        )
        for _ in range(4)
    ]

    assert [r.document_crisis for r in results] == [CrisisKind.DOMESTIC_VIOLENCE] * 4
    assert all(r.answer.claims[0].text == "A refund is required." for r in results)
    assert len(retriever.queries) == 4


def test_the_escalation_footer_still_follows_the_document_helpline() -> None:
    """Footer order matches the short-circuit's: the category-specific
    helpline leads, the standard free-legal-aid routing follows. Never the
    standard footer alone."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.footer.index("181") < result.footer.index("15100")
    assert result.helplines[-1].number == "15100"


def test_a_question_crisis_short_circuits_even_with_a_document_attached() -> None:
    """Ruling 1's other half: when the question itself signals crisis, the
    short-circuit is exactly what it always was -- free, no retrieval, no
    generation, which is why it survives a broken API key. A document
    attached to that same turn changes nothing about it."""
    adapter = StubAdapter([])
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert result.answer.is_answerable is False
    assert len(adapter.calls) == 0


def test_both_categories_surface_when_question_and_document_differ() -> None:
    """Ruling 1, revised. A woman asking about domestic violence whose
    attached document indicates self-harm risk must get the DV helplines AND
    Tele-MANAS. The turn is already short-circuited, so the second category
    costs nothing -- and self-harm is at the top of the project's own
    severity table precisely because it is the most time-critical.

    Her question stays primary: its prose leads and its numbers come first,
    so the response still speaks to what she actually said."""
    adapter = StubAdapter([])
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(CrisisKind.SELF_HARM.value,),
    )

    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE  # primary
    assert result.document_crisis is CrisisKind.SELF_HARM  # surfaced too
    # Both categories' prose, hers first.
    assert result.footer.index("181") < result.footer.index("14416")
    # Both categories' numbers, tap-to-call.
    numbers = [helpline.number for helpline in result.helplines]
    assert "181" in numbers and "14416" in numbers
    # ...and it still costs nothing.
    assert len(adapter.calls) == 0


def test_the_question_category_leads_the_prose_even_when_less_urgent() -> None:
    """`most_urgent_crisis` is NOT what orders these two: evidence strength
    is. The document may be two years old or about someone else, so it never
    takes over the message, however urgent its category."""
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        StubAdapter([]),
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(CrisisKind.SELF_HARM.value,),
    )

    assert result.footer.startswith(crisis_response(CrisisKind.DOMESTIC_VIOLENCE))
    assert result.helplines[0].number == "112"  # first of the DV set


def test_the_same_category_in_question_and_document_is_not_repeated() -> None:
    result = answer_in_language(
        "my husband beats me",
        _RaisingRetriever(),
        StubAdapter([]),
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.footer.count(crisis_response(CrisisKind.DOMESTIC_VIOLENCE)) == 1
    numbers = [helpline.number for helpline in result.helplines]
    assert len(numbers) == len(set(numbers))


def test_a_two_category_crisis_turn_never_shows_a_number_twice() -> None:
    """112 is in three of the four category sets, and the custodial set
    contains the NALSA number itself -- so overlap is the normal case. A
    duplicated tap-to-call row reads as a rendering bug exactly where trust
    matters most."""
    result = answer_in_language(
        "I was beaten by the police",
        _RaisingRetriever(),
        StubAdapter([]),
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.crisis is CrisisKind.CUSTODIAL_ABUSE
    numbers = [helpline.number for helpline in result.helplines]
    assert numbers.count("112") == 1
    assert numbers.count("15100") == 1  # in the custodial set AND the footer's
    assert len(numbers) == len(set(numbers))


def test_a_single_category_custodial_turn_also_shows_nalsa_once() -> None:
    """Pre-existing duplicate, fixed by the same dedupe: before it,
    `crisis_helplines(CUSTODIAL_ABUSE) + (nalsa,)` rendered 15100 twice on
    every custodial crisis turn, with no document involved at all."""
    result = answer_in_language(
        "I was beaten by the police",
        _RaisingRetriever(),
        StubAdapter([]),
        ModelRegistry(),
        _glossary(),
    )

    numbers = [helpline.number for helpline in result.helplines]
    assert numbers.count("15100") == 1


def test_a_document_crisis_alongside_an_answer_never_repeats_nalsa() -> None:
    """Same overlap on the alongside path: a custodial document verdict
    contributes 15100, and the ordinary answer's footer already cites it."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(CrisisKind.CUSTODIAL_ABUSE.value,),
    )

    numbers = [helpline.number for helpline in result.helplines]
    assert numbers.count("15100") == 1
    assert result.answer.claims[0].text == "A refund is required."


def test_several_attached_documents_resolve_to_the_most_urgent_verdict() -> None:
    """Documents are weighed against EACH OTHER by severity, so two
    documents never produce an arbitrary winner."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(None, STORED_DV, CrisisKind.TRAFFICKING.value),
    )

    assert result.document_crisis is CrisisKind.TRAFFICKING
    assert result.answer.claims[0].text == "A refund is required."


def test_a_cleared_document_does_not_suppress_an_ordinary_answer() -> None:
    """The ordinary case must stay ordinary: an attached document whose
    stored verdict is NULL changes nothing about the turn -- no helpline
    card, no extra numbers, no extra model call."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=NO_DOCUMENT_VERDICT,
    )

    assert result.crisis is None
    assert result.document_crisis is None
    assert result.answer.claims[0].text == "A refund is required."
    assert result.helplines == (nalsa_helpline("en"),)
    assert len(adapter.calls) == 3


def test_an_unrecognised_stored_verdict_never_breaks_the_turn(caplog) -> None:
    """A corrupted or future-dated `crisis_kind` value cannot be turned into
    a helpline (no category can be fabricated from an unknown string), so
    routing proceeds -- loudly logged, never silently."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "my landlord kept my deposit", _generation()]
    )
    with caplog.at_level("ERROR"):
        result = answer_in_language(
            "my landlord kept my deposit",
            _Retriever(),
            adapter,
            ModelRegistry(),
            _glossary(),
            document_crisis_kinds=("not_a_crisis_kind",),
        )

    assert result.crisis is None
    assert result.document_crisis is None
    assert result.answer.is_answerable is True
    assert "not_a_crisis_kind" in caplog.text


def test_the_pipeline_is_handed_stored_verdicts_and_never_document_text() -> None:
    """Structural guarantee for "read from the DB row, never recomputed":
    there is no parameter here through which document text could arrive, so
    this module cannot re-scan (or re-classify) a document per turn."""
    parameters = inspect.signature(answer_in_language).parameters

    assert "document_crisis_kinds" in parameters
    assert not {
        "document_context",
        "document_excerpts",
        "excerpts",
        "pages",
        "document_text",
    } & set(parameters)


# --- Hindi is not second-class on any of these paths ----------------------


def test_hindi_crisis_question_on_turn_four_with_a_document_attached() -> None:
    """The case this project has produced four English-only defects on. The
    question is Devanagari (which the regex layer cannot match at all), it
    is the fourth turn, and a cleared document is attached. The paid
    classifier -- which does read Devanagari -- fires, and the helpline
    prose comes back in Hindi, not English."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "english query", _generation(), TRANSLATED] * 3
        + [_classifier_response(CrisisKind.DOMESTIC_VIOLENCE)]
    )
    retriever = _Retriever()
    for _ in range(3):
        answer_in_language(
            "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
            retriever,
            adapter,
            ModelRegistry(),
            _glossary(),
            document_crisis_kinds=NO_DOCUMENT_VERDICT,
        )

    result = answer_in_language(
        "मुझे बहुत डर लगता है",
        _RaisingRetriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=NO_DOCUMENT_VERDICT,
    )

    assert result.language == "hi"
    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert "महिला हेल्पलाइन" in result.footer
    assert "181" in result.footer


def test_a_hindi_question_about_rent_with_a_protection_order_attached() -> None:
    """The self-review case, exactly: a woman uploads a protection order and
    asks only about rent arrears, in Hindi, on turn three. Under ruling 1
    she gets BOTH -- her rent answer in Hindi and the Hindi helpline card.
    The document layer must not be an English-only capability just because
    the layer that produced the verdict reads English text."""
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "english query", _generation(), TRANSLATED] * 3
    )
    retriever = _Retriever()
    for _ in range(2):
        answer_in_language(
            "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
            retriever,
            adapter,
            ModelRegistry(),
            _glossary(),
        )

    result = answer_in_language(
        "किराया बकाया के बारे में क्या नियम है",
        retriever,
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.language == "hi"
    assert result.document_crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert "महिला हेल्पलाइन" in result.footer
    assert "181" in result.footer
    # Her actual question is still answered, in Hindi.
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"
    labels = {helpline.number: helpline.label for helpline in result.helplines}
    assert labels["181"] == "महिला हेल्पलाइन"


def test_an_explicit_language_still_localises_the_document_helpline() -> None:
    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "english query", _generation(), TRANSLATED]
    )
    result = answer_in_language(
        "what are the rules on rent arrears",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        language="hi",
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.language == "hi"
    assert "महिला हेल्पलाइन" in result.footer


def test_a_devanagari_document_verdict_now_reaches_the_pipeline() -> None:
    """Ruling 2, end to end from the scan: a Hindi-script protection order
    produces a stored verdict that the free layer used to miss entirely,
    and it rides alongside the Hindi answer."""
    verdict = scan_for_crisis(
        (ExtractedPage(page_number=1, text="संरक्षण आदेश: पति मुझे मारता है।"),)
    )
    assert verdict is CrisisKind.DOMESTIC_VIOLENCE

    adapter = StubAdapter(
        [_SAFE_CLASSIFIER_RESPONSE, "english query", _generation(), TRANSLATED]
    )
    result = answer_in_language(
        "किराया बकाया के बारे में क्या नियम है",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(verdict.value,),
    )

    assert result.document_crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert result.answer.claims[0].text == "वापसी आवश्यक है।"


# --- a classifier outage stays countable, even on a document crisis turn --


def test_classifier_failure_during_a_document_crisis_turn_is_still_recorded() -> None:
    """Plan 3's ruling, re-asserted on the new path: the classifier outage
    happened, so it must stay countable -- a document verdict arriving in
    the same turn must not swallow the `ClassifierFailureEvent`."""
    adapter = _ClassifierFailsAdapter(
        ["my landlord kept my deposit", _generation()]
    )
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
        document_crisis_kinds=(STORED_DV,),
    )

    assert result.document_crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert len(result.classifier_failures) == 1
    assert result.classifier_failures[0].question == "my landlord kept my deposit"


def test_a_classifier_outage_across_four_turns_records_one_event_per_turn(
    tmp_path: Path,
) -> None:
    """A sustained outage must be countable turn by turn, not collapsed into
    one record -- and the document verdict keeps attaching its helpline on
    every one of those turns while the paid layer is down."""
    settings = Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        hallucination_log_path=tmp_path / "hallucinations.jsonl",
        classifier_failure_log_path=tmp_path / "classifier_failures.jsonl",
    )
    for turn in range(4):
        result = answer_in_language(
            f"question number {turn}",
            _Retriever(),
            _ClassifierFailsAdapter([f"question number {turn}", _generation()]),
            ModelRegistry(),
            _glossary(),
            document_crisis_kinds=(STORED_DV,),
        )
        assert result.document_crisis is CrisisKind.DOMESTIC_VIOLENCE
        persist_answer_events(result, settings)

    lines = (
        (tmp_path / "classifier_failures.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(lines) == 4
