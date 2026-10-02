"""Per-role provider routing: the provider lives in the model string.

The configuration this file exists to make work is a mixed one -- the
generator on OpenRouter, the translator and classifier still on Anthropic
Haiku -- which no single global provider switch can express. Everything here
runs with no network and no API key of any kind: the underlying adapters are
recording stubs.
"""

import pytest

from openlaw.answers.models import Completion, ModelRegistry, ModelRole
from openlaw.answers.routing import (
    LLMProvider,
    ModelRouteError,
    RoutingAdapter,
    parse_model_route,
    referenced_providers,
)
from openlaw.config import Settings


class _Recording:
    """A minimal `LLMAdapter` that records the model it was asked for."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.models: list[str] = []

    def complete(self, model, system, messages, output_schema, max_tokens) -> Completion:
        self.models.append(model)
        return Completion(text=f"{self.name}:{model}")


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"vaquill_root": "/tmp/v", "kanoongpt_root": "/tmp/k"}
    return Settings(**{**defaults, **overrides})


# --- parsing ----------------------------------------------------------------


def test_an_unprefixed_model_means_anthropic() -> None:
    """Backwards compatibility: every value shipped today is unprefixed, and
    must keep meaning exactly what it means now."""
    route = parse_model_route("claude-opus-5")

    assert route.provider is LLMProvider.ANTHROPIC
    assert route.model == "claude-opus-5"


def test_an_openrouter_prefix_selects_openrouter_and_is_stripped() -> None:
    route = parse_model_route("openrouter:z-ai/glm-5.3-flash")

    assert route.provider is LLMProvider.OPENROUTER
    assert route.model == "z-ai/glm-5.3-flash"


def test_an_explicit_anthropic_prefix_is_accepted_and_stripped() -> None:
    route = parse_model_route("anthropic:claude-haiku-4-5")

    assert route.provider is LLMProvider.ANTHROPIC
    assert route.model == "claude-haiku-4-5"


def test_only_the_first_colon_is_the_prefix_separator() -> None:
    """OpenRouter model ids carry their own colons (`:free`, `:nitro`), so
    splitting on anything but the first one would mangle them."""
    route = parse_model_route("openrouter:z-ai/glm-5.3-flash:free")

    assert route.provider is LLMProvider.OPENROUTER
    assert route.model == "z-ai/glm-5.3-flash:free"


def test_an_unknown_prefix_is_a_configuration_error_naming_the_value() -> None:
    with pytest.raises(ModelRouteError) as excinfo:
        parse_model_route("gpt5:some-model")

    message = str(excinfo.value)
    assert "gpt5" in message
    assert "anthropic" in message and "openrouter" in message


def test_a_prefix_with_no_model_name_is_a_configuration_error() -> None:
    with pytest.raises(ModelRouteError, match="no model name"):
        parse_model_route("openrouter:")


def test_a_blank_model_is_a_configuration_error() -> None:
    with pytest.raises(ModelRouteError, match="empty"):
        parse_model_route("   ")


# --- dispatch ---------------------------------------------------------------


def _routing() -> tuple[RoutingAdapter, _Recording, _Recording, list[str]]:
    anthropic, openrouter = _Recording("anthropic"), _Recording("openrouter")
    built: list[str] = []

    def _anthropic_factory() -> _Recording:
        built.append("anthropic")
        return anthropic

    def _openrouter_factory() -> _Recording:
        built.append("openrouter")
        return openrouter

    adapter = RoutingAdapter(
        {
            LLMProvider.ANTHROPIC: _anthropic_factory,
            LLMProvider.OPENROUTER: _openrouter_factory,
        }
    )
    return adapter, anthropic, openrouter, built


def test_routing_sends_a_prefixed_model_to_the_named_provider_unprefixed() -> None:
    adapter, anthropic, openrouter, _ = _routing()

    completion = adapter.complete("openrouter:z-ai/glm-5.3-flash", "sys", [], None, 16)

    assert openrouter.models == ["z-ai/glm-5.3-flash"]
    assert anthropic.models == []
    assert completion.text == "openrouter:z-ai/glm-5.3-flash"


def test_routing_sends_an_unprefixed_model_to_anthropic() -> None:
    adapter, anthropic, openrouter, _ = _routing()

    adapter.complete("claude-opus-5", "sys", [], None, 16)

    assert anthropic.models == ["claude-opus-5"]
    assert openrouter.models == []


def test_an_unused_provider_client_is_never_constructed() -> None:
    """A deployment with no OpenRouter role must not build an OpenRouter
    client, and vice versa -- see `build_llm_adapter`'s key validation."""
    adapter, _, _, built = _routing()

    adapter.complete("claude-opus-5", "sys", [], None, 16)

    assert built == ["anthropic"]


def test_each_provider_client_is_constructed_at_most_once() -> None:
    adapter, _, _, built = _routing()

    adapter.complete("claude-opus-5", "sys", [], None, 16)
    adapter.complete("claude-haiku-4-5", "sys", [], None, 16)
    adapter.complete("openrouter:z-ai/glm-5.3-flash", "sys", [], None, 16)

    assert built == ["anthropic", "openrouter"]


def test_routing_to_a_provider_with_no_factory_fails_loudly() -> None:
    adapter = RoutingAdapter({LLMProvider.ANTHROPIC: lambda: _Recording("a")})

    with pytest.raises(ModelRouteError, match="openrouter"):
        adapter.complete("openrouter:z-ai/glm-5.3-flash", "sys", [], None, 16)


def test_a_bad_prefix_reaching_the_adapter_raises_rather_than_guessing() -> None:
    adapter, _, _, _ = _routing()

    with pytest.raises(ModelRouteError):
        adapter.complete("gpt5:x", "sys", [], None, 16)


# --- the user's target configuration ----------------------------------------


def test_generator_on_openrouter_with_translator_and_classifier_on_anthropic(
    monkeypatch,
) -> None:
    """The mixed configuration this feature exists for: one registry, three
    roles, two providers, each call reaching the right adapter with the bare
    model name its own provider expects."""
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "openrouter:z-ai/glm-5.3-flash")
    monkeypatch.setenv("OPENLAW_MODEL_TRANSLATOR", "anthropic:claude-haiku-4-5")
    monkeypatch.setenv("OPENLAW_MODEL_CLASSIFIER", "claude-haiku-4-5")
    registry = ModelRegistry.from_settings(_settings())
    adapter, anthropic, openrouter, _ = _routing()

    for role in ModelRole:
        adapter.complete(registry.for_role(role), "sys", [], None, 16)

    assert openrouter.models == ["z-ai/glm-5.3-flash"]
    assert anthropic.models == ["claude-haiku-4-5", "claude-haiku-4-5"]


# --- which providers a deployment actually references -----------------------


def test_referenced_providers_is_anthropic_only_for_the_shipped_defaults() -> None:
    assert referenced_providers(ModelRegistry.from_settings(_settings())) == frozenset(
        {LLMProvider.ANTHROPIC}
    )


def test_referenced_providers_reports_both_for_a_mixed_configuration(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "openrouter:z-ai/glm-5.3-flash")

    assert referenced_providers(ModelRegistry.from_settings(_settings())) == frozenset(
        {LLMProvider.ANTHROPIC, LLMProvider.OPENROUTER}
    )


def test_referenced_providers_is_openrouter_only_when_every_role_is_openrouter(
    monkeypatch,
) -> None:
    """The case that must not demand an Anthropic key."""
    for name in ("GENERATOR", "TRANSLATOR", "CLASSIFIER"):
        monkeypatch.setenv(f"OPENLAW_MODEL_{name}", "openrouter:z-ai/glm-5.3-flash")

    assert referenced_providers(ModelRegistry.from_settings(_settings())) == frozenset(
        {LLMProvider.OPENROUTER}
    )


def test_referenced_providers_includes_per_language_translator_overrides(
    monkeypatch,
) -> None:
    """A per-language override carries its provider for free -- and a
    deployment that references OpenRouter only from one language override
    still needs that key."""
    monkeypatch.setenv(
        "OPENLAW_MODEL_TRANSLATOR_OVERRIDES",
        '{"mr": "openrouter:z-ai/glm-5.3-flash"}',
    )

    assert referenced_providers(ModelRegistry.from_settings(_settings())) == frozenset(
        {LLMProvider.ANTHROPIC, LLMProvider.OPENROUTER}
    )


def test_a_bad_prefix_is_reported_with_the_setting_that_carries_it(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "gpt5:some-model")

    with pytest.raises(ModelRouteError) as excinfo:
        referenced_providers(ModelRegistry.from_settings(_settings()))

    assert "OPENLAW_MODEL_GENERATOR" in str(excinfo.value)
    assert "gpt5:some-model" in str(excinfo.value)


def test_a_bad_prefix_in_a_language_override_names_that_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_MODEL_TRANSLATOR_OVERRIDES", '{"mr": "gpt5:x"}')

    with pytest.raises(ModelRouteError) as excinfo:
        referenced_providers(ModelRegistry.from_settings(_settings()))

    assert 'OPENLAW_MODEL_TRANSLATOR_OVERRIDES["mr"]' in str(excinfo.value)


def test_configured_models_lists_every_role_and_override() -> None:
    registry = ModelRegistry(
        {ModelRole.GENERATOR: "openrouter:z-ai/glm-5.3-flash"},
        language_overrides={(ModelRole.TRANSLATOR, "mr"): "indic-model"},
    )

    configured = dict(registry.configured_models())

    assert configured["OPENLAW_MODEL_GENERATOR"] == "openrouter:z-ai/glm-5.3-flash"
    assert configured["OPENLAW_MODEL_TRANSLATOR"] == "claude-haiku-4-5"
    assert configured["OPENLAW_MODEL_CLASSIFIER"] == "claude-haiku-4-5"
    assert configured['OPENLAW_MODEL_TRANSLATOR_OVERRIDES["mr"]'] == "indic-model"
