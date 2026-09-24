"""Per-role model configuration and the provider adapter interface.

Models are chosen per pipeline role, not globally. The generator does grounded
legal reasoning and warrants a frontier model; translation and classification do
not, and paying frontier prices for them is waste. Per-language overrides exist
because Indic-specialist models often beat general models on Indian languages,
and `from_settings` wires them from `OPENLAW_MODEL_TRANSLATOR_OVERRIDES` (a
JSON map of language code to model name) so a deployment can use one without
editing code.

No call site names a model directly — every one resolves through the registry.
That is deliberate: it makes "generation accidentally routed through the cheap
model" and "translation accidentally routed through the frontier model" both
structurally hard to do, which matters at the ~$17,400/month, 10K-user scale
this product is priced around.
"""

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from openlaw.config import Settings


class ModelRole(Enum):
    """A role in the answer pipeline, each independently configurable."""

    GENERATOR = "generator"
    TRANSLATOR = "translator"
    CLASSIFIER = "classifier"


DEFAULT_MODELS: dict[ModelRole, str] = {
    ModelRole.GENERATOR: "claude-opus-5",
    ModelRole.TRANSLATOR: "claude-haiku-4-5",
    ModelRole.CLASSIFIER: "claude-haiku-4-5",
}


class Completion(BaseModel):
    """One model response."""

    model_config = ConfigDict(frozen=True)

    text: str
    stop_reason: str = "end_turn"
    raw: dict[str, Any] = {}


class LLMAdapter(Protocol):
    """A provider-agnostic completion interface.

    `output_schema` carries a JSON schema when the caller needs structured
    output (e.g. the generator's claims-with-citations shape, defined in a
    later task) — `None` means free-form text.
    """

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> Completion:
        """Return a completion from `model`."""
        ...


class ModelRegistry:
    """Resolves a model name for a pipeline role, optionally per language."""

    def __init__(
        self,
        models: dict[ModelRole, str] | None = None,
        language_overrides: dict[tuple[ModelRole, str], str] | None = None,
    ) -> None:
        self._models = dict(models or {})
        self._overrides = dict(language_overrides or {})

    def for_role(self, role: ModelRole, language: str | None = None) -> str:
        """Return the model configured for `role`, honouring `language` overrides."""
        if language:
            override = self._overrides.get((role, language))
            if override:
                return override
        return self._models.get(role) or DEFAULT_MODELS[role]

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelRegistry":
        """Build a registry from environment-driven settings.

        `Settings.model_translator_overrides` (a JSON map of language code to
        model name) becomes the per-language TRANSLATOR overrides, so the
        per-language routing this class has always supported is actually
        reachable from a deployment rather than only from code.
        """
        return cls(
            {
                ModelRole.GENERATOR: settings.model_generator,
                ModelRole.TRANSLATOR: settings.model_translator,
                ModelRole.CLASSIFIER: settings.model_classifier,
            },
            language_overrides={
                (ModelRole.TRANSLATOR, language): model
                for language, model in settings.model_translator_overrides.items()
            },
        )


class StubAdapter:
    """A recording `LLMAdapter` for tests — never touches a network."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> Completion:
        """Return the next queued response, recording the call."""
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": messages,
                "output_schema": output_schema,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise RuntimeError("StubAdapter exhausted: no queued responses remain")
        return Completion(text=self._responses.pop(0))
