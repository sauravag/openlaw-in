import pytest

from openlaw.answers.models import (
    DEFAULT_MODELS,
    LLMAdapter,
    ModelRegistry,
    ModelRole,
    StubAdapter,
)
from openlaw.config import Settings


def test_generator_defaults_to_opus_5() -> None:
    assert DEFAULT_MODELS[ModelRole.GENERATOR] == "claude-opus-5"


def test_cheaper_roles_default_to_haiku() -> None:
    """Translation and classification do not need frontier reasoning."""
    assert DEFAULT_MODELS[ModelRole.TRANSLATOR] == "claude-haiku-4-5"
    assert DEFAULT_MODELS[ModelRole.CLASSIFIER] == "claude-haiku-4-5"


def test_every_role_has_a_default() -> None:
    for role in ModelRole:
        assert role in DEFAULT_MODELS


def test_registry_returns_the_configured_model_for_a_role() -> None:
    registry = ModelRegistry({ModelRole.GENERATOR: "claude-sonnet-5"})
    assert registry.for_role(ModelRole.GENERATOR) == "claude-sonnet-5"


def test_registry_falls_back_to_the_default_for_an_unconfigured_role() -> None:
    assert ModelRegistry({}).for_role(ModelRole.TRANSLATOR) == "claude-haiku-4-5"


def test_registry_applies_a_per_language_translator_override() -> None:
    """Indic-specialist models often beat general models on Tamil or Marathi."""
    registry = ModelRegistry(
        {ModelRole.TRANSLATOR: "claude-haiku-4-5"},
        language_overrides={(ModelRole.TRANSLATOR, "ta"): "indic-specialist-model"},
    )
    assert registry.for_role(ModelRole.TRANSLATOR, language="ta") == (
        "indic-specialist-model"
    )


def test_language_override_does_not_leak_to_other_languages() -> None:
    registry = ModelRegistry(
        {ModelRole.TRANSLATOR: "claude-haiku-4-5"},
        language_overrides={(ModelRole.TRANSLATOR, "ta"): "indic-specialist-model"},
    )
    assert registry.for_role(ModelRole.TRANSLATOR, language="hi") == "claude-haiku-4-5"


def test_language_override_does_not_leak_to_other_roles() -> None:
    registry = ModelRegistry(
        {}, language_overrides={(ModelRole.TRANSLATOR, "ta"): "indic-specialist-model"}
    )
    assert registry.for_role(ModelRole.GENERATOR, language="ta") == "claude-opus-5"


def test_registry_from_settings_reads_each_role_independently(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "claude-sonnet-5")

    registry = ModelRegistry.from_settings(Settings())

    assert registry.for_role(ModelRole.GENERATOR) == "claude-sonnet-5"
    assert registry.for_role(ModelRole.TRANSLATOR) == "claude-haiku-4-5"
    assert registry.for_role(ModelRole.CLASSIFIER) == "claude-haiku-4-5"


def test_stub_adapter_returns_queued_responses_in_order() -> None:
    adapter: LLMAdapter = StubAdapter(["first", "second"])
    assert adapter.complete("m", "sys", [], None, 100).text == "first"
    assert adapter.complete("m", "sys", [], None, 100).text == "second"


def test_stub_adapter_records_the_model_it_was_called_with() -> None:
    adapter = StubAdapter(["ok"])
    adapter.complete("claude-opus-5", "sys", [], None, 100)
    assert adapter.calls[0]["model"] == "claude-opus-5"


def test_stub_adapter_raises_when_exhausted() -> None:
    adapter = StubAdapter(["only"])
    adapter.complete("m", "s", [], None, 10)
    with pytest.raises(RuntimeError):
        adapter.complete("m", "s", [], None, 10)
