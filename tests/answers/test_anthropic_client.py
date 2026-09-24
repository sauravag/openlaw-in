"""Unit tests fake the Anthropic client boundary -- no key, no network. The one
`@pytest.mark.integration` test talks to the real API and skips cleanly when
no key is configured (the normal state in this environment).
"""

from types import SimpleNamespace
from typing import Any

import pytest

import openlaw.answers.anthropic_client as anthropic_client_module
from openlaw.answers.anthropic_client import FALLBACK_BETA, AnthropicAdapter
from openlaw.answers.models import ModelRegistry, ModelRole
from openlaw.config import get_settings


class _FakeMessages:
    """Records the request passed to `create` and returns a queued response."""

    def __init__(self, response: SimpleNamespace) -> None:
        self.captured: dict[str, Any] | None = None
        self._response = response

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.captured = kwargs
        return self._response


class _FakeBeta:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


class _FakeAnthropicClient:
    """Stands in for `anthropic.Anthropic` -- captures the api_key, never dials out."""

    def __init__(self, response: SimpleNamespace) -> None:
        self.api_key: str | None = None
        self.messages = _FakeMessages(response)
        self.beta = _FakeBeta(self.messages)

    def __call__(self, api_key: str) -> "_FakeAnthropicClient":
        self.api_key = api_key
        return self


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, response: SimpleNamespace
) -> _FakeAnthropicClient:
    fake = _FakeAnthropicClient(response)
    monkeypatch.setattr(anthropic_client_module, "Anthropic", fake)
    return fake


def test_adapter_passes_the_api_key_to_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("hi")])
    fake = _install_fake_client(monkeypatch, response)

    AnthropicAdapter(api_key="sk-test-123")

    assert fake.api_key == "sk-test-123"


def test_adapter_returns_the_concatenated_text_of_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn", content=[_text_block("hello "), _text_block("world")]
    )
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    completion = adapter.complete("claude-opus-5", "sys", [], None, 100)

    assert completion.text == "hello world"
    assert completion.stop_reason == "end_turn"
    assert fake.messages.captured is not None


def test_adapter_sends_adaptive_thinking_and_the_refusal_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("ok")])
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    adapter.complete("claude-opus-5", "sys", [{"role": "user", "content": "hi"}], None, 50)

    request = fake.messages.captured
    assert request is not None
    assert request["thinking"] == {"type": "adaptive"}
    assert request["betas"] == [FALLBACK_BETA]
    assert request["fallbacks"] == "default"
    assert request["model"] == "claude-opus-5"
    assert request["max_tokens"] == 50


def test_adapter_puts_the_cache_breakpoint_on_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("ok")])
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    adapter.complete("claude-opus-5", "you are a legal assistant", [], None, 50)

    system = fake.messages.captured["system"]
    assert system == [
        {
            "type": "text",
            "text": "you are a legal assistant",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_adapter_requests_structured_output_when_a_schema_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("{}")])
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")
    schema = {"type": "object", "properties": {"claims": {"type": "array"}}}

    adapter.complete("claude-opus-5", "sys", [], schema, 50)

    assert fake.messages.captured["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }


def test_adapter_omits_output_config_when_no_schema_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("ok")])
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    adapter.complete("claude-opus-5", "sys", [], None, 50)

    assert "output_config" not in fake.messages.captured


def test_adapter_returns_an_empty_completion_on_refusal_without_reading_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExplodingContent(list):
        def __iter__(self) -> Any:
            raise AssertionError("content must not be read after a refusal")

    response = SimpleNamespace(stop_reason="refusal", content=_ExplodingContent())
    _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    completion = adapter.complete("claude-opus-5", "sys", [], None, 50)

    assert completion.text == ""
    assert completion.stop_reason == "refusal"


def test_adapter_defaults_a_missing_stop_reason_to_end_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(stop_reason=None, content=[_text_block("ok")])
    _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    completion = adapter.complete("claude-opus-5", "sys", [], None, 50)

    assert completion.stop_reason == "end_turn"


@pytest.mark.integration
def test_adapter_completes_against_the_live_anthropic_api() -> None:
    """Skips cleanly -- no Anthropic API key is configured in this environment."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("OPENLAW_ANTHROPIC_API_KEY is not configured")

    adapter = AnthropicAdapter(api_key=settings.anthropic_api_key)
    completion = adapter.complete(
        model="claude-haiku-4-5",
        system="Reply with exactly one word: pong",
        messages=[{"role": "user", "content": "ping"}],
        output_schema=None,
        max_tokens=16,
    )

    assert completion.text


# --- per-role request shaping ----------------------------------------------
#
# The regression these cover: one unconditional request shape (adaptive
# thinking + the Opus-tier refusal-fallback beta) was sent for every role,
# including the CLASSIFIER role, whose default model is `claude-haiku-4-5`.
# Haiku rejects all of it, so every crisis-classifier call raised in
# production and the second layer of crisis routing was dead on arrival --
# silently, because `detect_crisis_with_classifier` falls back to the regex
# verdict. Nothing in the suite caught it: the only live test skips without
# an API key. These assert the request SHAPE per role instead.


def _capture_request(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the request the adapter would send for `model`."""
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("ok")])
    fake = _install_fake_client(monkeypatch, response)
    adapter = AnthropicAdapter(api_key="k")

    adapter.complete(model, "sys", [], output_schema, 256)

    assert fake.messages.captured is not None
    return fake.messages.captured


@pytest.mark.parametrize("role", [ModelRole.CLASSIFIER, ModelRole.TRANSLATOR])
def test_haiku_class_roles_send_no_thinking_no_betas_and_no_fallbacks(
    monkeypatch: pytest.MonkeyPatch, role: ModelRole
) -> None:
    model = ModelRegistry().for_role(role)

    request = _capture_request(monkeypatch, model)

    assert model == "claude-haiku-4-5"
    assert "thinking" not in request
    assert "betas" not in request
    assert "fallbacks" not in request


@pytest.mark.parametrize("role", [ModelRole.CLASSIFIER, ModelRole.TRANSLATOR])
def test_haiku_class_roles_never_send_output_config_effort(
    monkeypatch: pytest.MonkeyPatch, role: ModelRole
) -> None:
    schema = {"type": "object", "properties": {"crisis": {"type": "string"}}}

    request = _capture_request(monkeypatch, ModelRegistry().for_role(role), schema)

    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }
    assert "effort" not in request["output_config"]


def test_the_generator_role_keeps_adaptive_thinking_and_the_fallback_beta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ModelRegistry().for_role(ModelRole.GENERATOR)

    request = _capture_request(monkeypatch, model)

    assert model == "claude-opus-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["betas"] == [FALLBACK_BETA]
    assert request["fallbacks"] == "default"


def test_every_role_still_sends_the_cache_breakpoint_and_the_bare_essentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for role in ModelRole:
        request = _capture_request(monkeypatch, ModelRegistry().for_role(role))

        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert request["max_tokens"] == 256
        assert request["messages"] == []


def test_an_unrecognised_model_gets_the_plain_shape_every_model_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capability check is an allowlist: an unknown model must not be
    assumed to accept frontier-only parameters."""
    request = _capture_request(monkeypatch, "some-future-cheap-model")

    assert "thinking" not in request
    assert "betas" not in request
