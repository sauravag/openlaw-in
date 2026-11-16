"""Tests for the model-based crisis classification layer.

Unit tests use `StubAdapter` -- no network, no API key, matching the normal
state of this environment. The two `@pytest.mark.integration` tests talk to
the real Anthropic API and skip cleanly when no key is configured.

Revised ruling (see the module docstring in `crisis_classifier.py`): a
classifier failure must NOT declare a crisis -- it proceeds on the regex
verdict (already "no crisis" by the time the classifier is reached) since
every answer already carries the standard escalation footer regardless.
The failure must still be loud (logged at ERROR) and recorded (returned as
a `ClassifierFailureEvent`), never silently dropped.
"""

import json
from pathlib import Path

import pytest

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.crisis_classifier import (
    CLASSIFIER_SYSTEM_PROMPT,
    MAX_CLASSIFIER_TOKENS,
    ClassifierFailureEvent,
    append_classifier_failure_events,
    build_classifier_failure_event,
    classify_crisis,
    detect_crisis_with_classifier,
)
from openlaw.answers.escalation import CrisisKind, detect_crisis
from openlaw.answers.models import ModelRegistry, ModelRole, StubAdapter
from openlaw.config import get_settings


def _classifier_says(kind: CrisisKind | None) -> str:
    return json.dumps({"crisis": kind.value if kind is not None else "none"})


class _RaisingAdapter:
    """Simulates a network error or timeout: `.complete` always raises."""

    def complete(self, **kwargs: object) -> object:
        raise RuntimeError("simulated network failure")


#: The exact question that the live classifier mislabelled as
#: domestic_violence on 2 of 3 runs, short-circuiting the legal answer.
FILED_CASE_QUESTION = (
    "I filed a divorce proceedings case against my husband. I also have a "
    "dowry case registered against him. What legal remedies do I have to move "
    "the case faster if it has been stuck for years?"
)

#: Legal-case questions that ALSO describe present, ongoing or recent danger.
#: The "legal proceedings are none" clause must never swallow these.
REGEX_MISSED_CASE_QUESTIONS_WITH_DANGER = [
    "I filed a 498A case but he still raises his hand at me, how do I speed up "
    "the case?",
    "My 498A case is pending and my in-laws keep threatening to kill me if I do "
    "not withdraw it",
    "Mera divorce case chal raha hai aur pati abhi bhi haath uthata hai",
]
REGEX_CAUGHT_CASE_QUESTION_WITH_DANGER = (
    "He raised his hand at me last night, how do I file a domestic violence "
    "complaint?"
)

ORDINARY_FAMILY_LAW_QUESTIONS = [
    FILED_CASE_QUESTION,
    "My husband filed for divorce and the case has been pending for 6 years, "
    "how do I get an early hearing?",
    "I have a 498A case against my in-laws, can I get the trial expedited?",
    "How do I file for maintenance after separation?",
]


# --- CLASSIFIER_SYSTEM_PROMPT: content that drives precision and recall -----


def test_prompt_no_longer_biases_uncertain_cases_towards_a_crisis() -> None:
    """The "when genuinely uncertain, prefer naming the closest category"
    instruction made the model flag ordinary filed-case questions as
    domestic_violence, which replaces the legal answer with helplines."""
    lowered = CLASSIFIER_SYSTEM_PROMPT.lower()
    assert "closest category" not in lowered
    assert "genuinely uncertain" not in lowered


def test_prompt_says_categories_mean_present_or_ongoing_danger() -> None:
    lowered = CLASSIFIER_SYSTEM_PROMPT.lower()
    assert "in danger now" in lowered
    assert "ongoing risk" in lowered


def test_prompt_says_legal_proceedings_without_present_danger_are_none() -> None:
    lowered = CLASSIFIER_SYSTEM_PROMPT.lower()
    assert 'a question about legal proceedings is "none"' in lowered
    for term in ("divorce", "498a", "pwdv", "maintenance", "custody", "bail"):
        assert term in lowered
    assert (
        "only when it describes no current, ongoing or recent violence, "
        "threats, or fear for safety" in lowered
    )


def test_prompt_counts_recent_abuse_inside_a_legal_question_as_a_crisis() -> None:
    """Controller ruling: a false alarm now only adds a helpline card above a
    full answer, so the clause leans toward recall for recent abuse."""
    lowered = CLASSIFIER_SYSTEM_PROMPT.lower()
    assert "even inside a legal question" in lowered
    for cue in ('"last night"', '"yesterday"', '"still"', '"keeps"'):
        assert cue in lowered
    assert "still lives with or has access to" in lowered


def test_prompt_keeps_its_recall_guidance_for_real_crises() -> None:
    """Fixing false alarms must not drop what catches euphemism, third-person
    reports, and non-English scripts. "safety classifier" is also how
    `tests/language/test_pipeline.py` recognises this prompt."""
    lowered = CLASSIFIER_SYSTEM_PROMPT.lower()
    for phrase in (
        "safety classifier",
        "euphemistically",
        "third person",
        "hinglish",
        "devanagari",
        "he raises his hand at me",
        "i want to end it all",
        '{"crisis":',
    ):
        assert phrase in lowered


def test_regex_layer_does_not_flag_the_filed_case_question() -> None:
    """The false alarm came from the classifier, not the regex layer."""
    assert detect_crisis(FILED_CASE_QUESTION) is None


@pytest.mark.parametrize("question", REGEX_MISSED_CASE_QUESTIONS_WITH_DANGER)
def test_regex_misses_present_danger_inside_these_case_questions(
    question: str,
) -> None:
    """For these, the classifier is the ONLY guard -- which is why the live
    recall test below must keep catching them."""
    assert detect_crisis(question) is None


def test_regex_catches_the_recent_hand_raising_complaint_question() -> None:
    """Recorded so the live row for it is read correctly: the free regex
    already catches this one (via "domestic violence"), so in production the
    classifier is never asked about it."""
    assert (
        detect_crisis(REGEX_CAUGHT_CASE_QUESTION_WITH_DANGER)
        is CrisisKind.DOMESTIC_VIOLENCE
    )


# --- classify_crisis: parsing a well-formed response ------------------------


def test_classify_crisis_returns_none_when_the_model_reports_no_crisis() -> None:
    adapter = StubAdapter([_classifier_says(None)])
    result = classify_crisis("what is the rent control act", adapter, ModelRegistry())
    assert result is None


@pytest.mark.parametrize("kind", list(CrisisKind))
def test_classify_crisis_returns_every_reported_kind(kind: CrisisKind) -> None:
    adapter = StubAdapter([_classifier_says(kind)])
    assert classify_crisis("...", adapter, ModelRegistry()) is kind


def test_classify_crisis_uses_the_configured_classifier_model_and_role() -> None:
    adapter = StubAdapter([_classifier_says(None)])
    registry = ModelRegistry({ModelRole.CLASSIFIER: "claude-haiku-9"})

    classify_crisis("q", adapter, registry)

    assert adapter.calls[0]["model"] == "claude-haiku-9"


def test_classify_crisis_requests_structured_output() -> None:
    adapter = StubAdapter([_classifier_says(None)])
    classify_crisis("q", adapter, ModelRegistry())
    assert adapter.calls[0]["output_schema"] is not None


def test_classify_crisis_asks_for_enough_tokens_to_answer() -> None:
    """32 tokens was below the documented ~256 guidance for a classification
    call and risked truncating the JSON response into an unparseable one --
    which `detect_crisis_with_classifier` treats as a failure, i.e. no
    crisis."""
    adapter = StubAdapter([_classifier_says(None)])

    classify_crisis("q", adapter, ModelRegistry())

    assert adapter.calls[0]["max_tokens"] == MAX_CLASSIFIER_TOKENS
    assert MAX_CLASSIFIER_TOKENS >= 256


# --- classify_crisis: never guesses on a bad response ------------------------


def test_classify_crisis_raises_on_malformed_json() -> None:
    adapter = StubAdapter(["not json at all"])
    with pytest.raises(ValueError):
        classify_crisis("q", adapter, ModelRegistry())


def test_classify_crisis_raises_on_an_unrecognised_value() -> None:
    adapter = StubAdapter([json.dumps({"crisis": "made_up_category"})])
    with pytest.raises(ValueError):
        classify_crisis("q", adapter, ModelRegistry())


def test_classify_crisis_raises_when_the_crisis_key_is_missing() -> None:
    adapter = StubAdapter([json.dumps({})])
    with pytest.raises(ValueError):
        classify_crisis("q", adapter, ModelRegistry())


def test_classify_crisis_raises_when_the_response_is_not_an_object() -> None:
    adapter = StubAdapter([json.dumps(["domestic_violence"])])
    with pytest.raises(ValueError):
        classify_crisis("q", adapter, ModelRegistry())


# --- detect_crisis_with_classifier: layering ordering ------------------------


def test_regex_hit_short_circuits_before_the_classifier_is_asked() -> None:
    adapter = StubAdapter([])
    result = detect_crisis_with_classifier(
        "my husband beats me", adapter, ModelRegistry()
    )
    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert result.failure is None
    assert adapter.calls == []


def test_classifier_is_asked_exactly_once_when_the_regex_is_silent() -> None:
    adapter = StubAdapter([_classifier_says(CrisisKind.SELF_HARM)])
    result = detect_crisis_with_classifier(
        "I want to end it all", adapter, ModelRegistry()
    )
    assert result.crisis is CrisisKind.SELF_HARM
    assert result.failure is None
    assert len(adapter.calls) == 1


def test_classifier_confirming_safety_lets_the_question_through() -> None:
    adapter = StubAdapter([_classifier_says(None)])
    result = detect_crisis_with_classifier(
        "what is the rent control act", adapter, ModelRegistry()
    )
    assert result.crisis is None
    assert result.failure is None


# --- revised fail-safe: a classifier failure proceeds on the regex verdict -
# --- (no crisis), but is logged loudly and recorded, never silently lost --


def test_a_raising_adapter_does_not_declare_a_crisis() -> None:
    """A classifier outage (network error, timeout, expired key) must not
    turn an ordinary, regex-silent question into a fabricated crisis --
    every answer already carries the standard escalation footer regardless,
    so there is no missing-help cost to accept in exchange."""
    result = detect_crisis_with_classifier(
        "some ambiguous message", _RaisingAdapter(), ModelRegistry()
    )
    assert result.crisis is None


def test_a_raising_adapter_records_the_failure_rather_than_dropping_it() -> None:
    result = detect_crisis_with_classifier(
        "some ambiguous message", _RaisingAdapter(), ModelRegistry()
    )
    assert isinstance(result.failure, ClassifierFailureEvent)
    assert result.failure.question == "some ambiguous message"
    assert "RuntimeError" in result.failure.error
    assert result.failure.timestamp


def test_unparseable_classifier_output_does_not_declare_a_crisis() -> None:
    adapter = StubAdapter(["<<not json at all>>"])
    result = detect_crisis_with_classifier(
        "some ambiguous message", adapter, ModelRegistry()
    )
    assert result.crisis is None


def test_unparseable_classifier_output_is_recorded() -> None:
    adapter = StubAdapter(["<<not json at all>>"])
    result = detect_crisis_with_classifier(
        "some ambiguous message", adapter, ModelRegistry()
    )
    assert result.failure is not None
    assert "ValueError" in result.failure.error


def test_an_unrecognised_classifier_value_does_not_declare_a_crisis() -> None:
    adapter = StubAdapter([json.dumps({"crisis": "made_up_category"})])
    result = detect_crisis_with_classifier(
        "some ambiguous message", adapter, ModelRegistry()
    )
    assert result.crisis is None
    assert result.failure is not None


def test_an_exhausted_stub_adapter_does_not_declare_a_crisis() -> None:
    """`StubAdapter` raises `RuntimeError` when its queue is empty -- proves
    the fail-safe wrapper catches adapter-side failures generically, not
    just a hand-picked exception type."""
    adapter = StubAdapter([])
    result = detect_crisis_with_classifier("some message", adapter, ModelRegistry())
    assert result.crisis is None
    assert result.failure is not None


def test_a_successful_classification_never_records_a_failure() -> None:
    adapter = StubAdapter([_classifier_says(CrisisKind.TRAFFICKING)])
    result = detect_crisis_with_classifier("some message", adapter, ModelRegistry())
    assert result.failure is None


# --- ClassifierFailureEvent / append_classifier_failure_events -------------


def test_build_classifier_failure_event_captures_question_and_error_type() -> None:
    event = build_classifier_failure_event("my message", RuntimeError("boom"))
    assert event.question == "my message"
    assert event.error == "RuntimeError: boom"
    assert event.timestamp


def test_append_classifier_failure_events_writes_jsonl(tmp_path: Path) -> None:
    event = build_classifier_failure_event("my message", RuntimeError("boom"))
    path = tmp_path / "classifier_failures.jsonl"

    written = append_classifier_failure_events((event,), path)

    assert written == 1
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["question"] == "my message"


def test_append_classifier_failure_events_creates_owner_only_permissions(
    tmp_path: Path,
) -> None:
    import stat

    event = build_classifier_failure_event("my message", RuntimeError("boom"))
    path = tmp_path / "classifier_failures.jsonl"

    append_classifier_failure_events((event,), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_append_classifier_failure_events_with_empty_tuple_writes_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "classifier_failures.jsonl"
    assert append_classifier_failure_events((), path) == 0
    assert not path.exists()


# --- measured gaps the regex misses, now caught by the classifier layer ----


@pytest.mark.parametrize(
    "text,kind",
    [
        # NB: "मुझे मारता है" and "मरना चाहता हूँ" used to live here. Plan 6
        # Task 10 (ruling 2) added a narrow Devanagari pattern set, so the
        # free regex now catches both and they moved to
        # `test_the_devanagari_patterns_catch_what_the_classifier_used_to_
        # carry_alone` below. Everything still listed here remains a
        # measured miss.
        ("mera pati mujhe marta h", CrisisKind.DOMESTIC_VIOLENCE),
        ("I want to end it all", CrisisKind.SELF_HARM),
        ("he raises his hand at me", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister is being beaten", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister was beaten by her husband", CrisisKind.DOMESTIC_VIOLENCE),
        ("என் கணவன் என்னை அடிக்கிறான்", CrisisKind.DOMESTIC_VIOLENCE),  # Tamil
        ("amar husband amake mare", CrisisKind.DOMESTIC_VIOLENCE),  # Bengali translit.
        ("naa mogudu nannu kottadu", CrisisKind.DOMESTIC_VIOLENCE),  # Telugu translit.
        ("mazha navra mala marto", CrisisKind.DOMESTIC_VIOLENCE),  # Marathi translit.
        ("मला खूप भीती वाटते", CrisisKind.DOMESTIC_VIOLENCE),  # Marathi, Devanagari
    ],
)
def test_classifier_catches_phrasing_the_regex_measurably_misses(
    text: str, kind: CrisisKind
) -> None:
    """Each phrasing here is confirmed below to return no match from the pure
    regex (`detect_crisis`) directly, matching a reviewer's measured findings
    against the current patterns. The stub adapter simulates the classifier's
    judgement on that same phrasing; a live-model check of a Devanagari
    example is `test_classifier_recognises_a_devanagari_crisis_phrase_live`
    below, guarded by `@pytest.mark.integration`.
    """
    assert detect_crisis(text) is None

    adapter = StubAdapter([_classifier_says(kind)])
    result = detect_crisis_with_classifier(text, adapter, ModelRegistry())

    assert result.crisis is kind
    assert result.failure is None
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "text,kind",
    [
        ("मुझे मारता है", CrisisKind.DOMESTIC_VIOLENCE),
        ("मरना चाहता हूँ", CrisisKind.SELF_HARM),
    ],
)
def test_the_devanagari_patterns_catch_what_the_classifier_used_to_carry_alone(
    text: str, kind: CrisisKind
) -> None:
    """Plan 6 Task 10, ruling 2. These two phrasings are the ones this
    module's own docstring cites as the measured Devanagari gap, and the
    free layer now reaches them -- so no model call is made at all, which
    is the point: an uploaded document is read by the free layer ALONE.

    The classifier is still needed for everything in the parametrisation
    above, and for Devanagari phrasings outside this deliberately narrow
    set (note the Marathi-in-Devanagari case, which the patterns do not and
    should not try to cover)."""
    assert detect_crisis(text) is kind

    adapter = StubAdapter([])
    result = detect_crisis_with_classifier(text, adapter, ModelRegistry())

    assert result.crisis is kind
    assert len(adapter.calls) == 0


# --- integration: the real model actually makes these calls correctly -----


@pytest.mark.integration
def test_classifier_recognises_a_devanagari_crisis_phrase_live() -> None:
    """Skips cleanly -- no Anthropic API key is configured in this environment."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    result = classify_crisis("मुझे मारता है", adapter, ModelRegistry())

    assert result is CrisisKind.DOMESTIC_VIOLENCE


@pytest.mark.integration
def test_classifier_does_not_flag_an_ordinary_legal_question_live() -> None:
    """Skips cleanly -- no Anthropic API key is configured in this environment."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    result = classify_crisis(
        "what documents do I need to register a rent agreement",
        adapter,
        ModelRegistry(),
    )

    assert result is None


@pytest.mark.integration
@pytest.mark.parametrize("question", ORDINARY_FAMILY_LAW_QUESTIONS)
def test_classifier_does_not_flag_ordinary_family_law_questions_live(
    question: str,
) -> None:
    """A filed case, a court delay, or maintenance is a legal question, not a
    present danger. Skips cleanly without an Anthropic API key."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    result = classify_crisis(question, adapter, ModelRegistry())

    assert result is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "text,kind",
    [
        ("he raises his hand at me", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister is being beaten by her husband", CrisisKind.DOMESTIC_VIOLENCE),
        ("I want to end it all", CrisisKind.SELF_HARM),
        *(
            (question, CrisisKind.DOMESTIC_VIOLENCE)
            for question in REGEX_MISSED_CASE_QUESTIONS_WITH_DANGER
        ),
        (REGEX_CAUGHT_CASE_QUESTION_WITH_DANGER, CrisisKind.DOMESTIC_VIOLENCE),
    ],
)
def test_classifier_still_catches_real_crises_live(
    text: str, kind: CrisisKind
) -> None:
    """Recall guard for the false-alarm fix: euphemism, third-person reports,
    and present or recent danger described INSIDE a legal-case question must
    still be caught. Skips cleanly without an Anthropic API key."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    result = classify_crisis(text, adapter, ModelRegistry())

    assert result is kind
