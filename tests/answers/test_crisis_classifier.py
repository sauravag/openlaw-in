"""Tests for the model-based crisis classification layer.

Unit tests use `StubAdapter` -- no network, no API key, matching the normal
state of this environment. The two `@pytest.mark.integration` tests talk to
the real Anthropic API and skip cleanly when no key is configured.
"""

import json

import pytest

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.crisis_classifier import (
    CLASSIFIER_FAILURE_FALLBACK,
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
    assert result is CrisisKind.DOMESTIC_VIOLENCE
    assert adapter.calls == []


def test_classifier_is_asked_exactly_once_when_the_regex_is_silent() -> None:
    adapter = StubAdapter([_classifier_says(CrisisKind.SELF_HARM)])
    result = detect_crisis_with_classifier(
        "I want to end it all", adapter, ModelRegistry()
    )
    assert result is CrisisKind.SELF_HARM
    assert len(adapter.calls) == 1


def test_classifier_confirming_safety_lets_the_question_through() -> None:
    adapter = StubAdapter([_classifier_says(None)])
    result = detect_crisis_with_classifier(
        "what is the rent control act", adapter, ModelRegistry()
    )
    assert result is None


# --- fail-safe: a classifier failure must never look like "safe" -----------


def test_a_raising_adapter_fails_safe_rather_than_crashing_or_looking_safe() -> None:
    result = detect_crisis_with_classifier(
        "some ambiguous message", _RaisingAdapter(), ModelRegistry()
    )
    assert result is CLASSIFIER_FAILURE_FALLBACK
    assert result is not None


def test_unparseable_classifier_output_fails_safe_rather_than_looking_safe() -> None:
    adapter = StubAdapter(["<<not json at all>>"])
    result = detect_crisis_with_classifier(
        "some ambiguous message", adapter, ModelRegistry()
    )
    assert result is CLASSIFIER_FAILURE_FALLBACK


def test_an_unrecognised_classifier_value_fails_safe_rather_than_looking_safe() -> None:
    adapter = StubAdapter([json.dumps({"crisis": "made_up_category"})])
    result = detect_crisis_with_classifier(
        "some ambiguous message", adapter, ModelRegistry()
    )
    assert result is CLASSIFIER_FAILURE_FALLBACK


def test_an_exhausted_stub_adapter_fails_safe_rather_than_crashing() -> None:
    """`StubAdapter` raises `RuntimeError` when its queue is empty -- proves
    the fail-safe wrapper catches adapter-side failures generically, not just
    a hand-picked exception type."""
    adapter = StubAdapter([])
    result = detect_crisis_with_classifier("some message", adapter, ModelRegistry())
    assert result is CLASSIFIER_FAILURE_FALLBACK


def test_classifier_failure_fallback_is_the_most_urgent_category() -> None:
    """Chosen deliberately, not defaulted -- see the module docstring: this
    reuses the same category `detect_crisis` already resolves to when a
    message plausibly matches more than one crisis kind."""
    assert CLASSIFIER_FAILURE_FALLBACK is CrisisKind.SELF_HARM


# --- measured gaps the regex misses, now caught by the classifier layer ----


@pytest.mark.parametrize(
    "text,kind",
    [
        ("मुझे मारता है", CrisisKind.DOMESTIC_VIOLENCE),
        ("मरना चाहता हूँ", CrisisKind.SELF_HARM),
        ("mera pati mujhe marta h", CrisisKind.DOMESTIC_VIOLENCE),
        ("I want to end it all", CrisisKind.SELF_HARM),
        ("he raises his hand at me", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister is being beaten", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister was beaten by her husband", CrisisKind.DOMESTIC_VIOLENCE),
        ("என் கணவன் என்னை அடிக்கிறான்", CrisisKind.DOMESTIC_VIOLENCE),  # Tamil
        ("amar husband amake mare", CrisisKind.DOMESTIC_VIOLENCE),  # Bengali translit.
        ("naa mogudu nannu kottadu", CrisisKind.DOMESTIC_VIOLENCE),  # Telugu translit.
        ("mazha navra mala marto", CrisisKind.DOMESTIC_VIOLENCE),  # Marathi translit.
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

    assert result is kind
    assert len(adapter.calls) == 1


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
