"""Anthropic implementation of the LLM adapter.

**Request shaping is per-model, not one shape for all roles.** This module
serves three roles (see `openlaw.answers.models.ModelRole`) that resolve to
models of different tiers: the GENERATOR runs on an Opus-tier model, while
the CLASSIFIER and TRANSLATOR default to `claude-haiku-4-5`. A single
unconditional request shape cannot be correct for both — Haiku rejects
adaptive thinking, rejects `output_config.effort`, and has no server-side
refusal-fallback behaviour to enable. Sending the frontier shape to it made
**every** crisis-classifier call raise in production, which
`detect_crisis_with_classifier` then absorbed by falling back to the regex
verdict: the entire second layer of crisis routing was dead on arrival, and
silently so. A woman typing "मुझे मारता है" — Devanagari, which the regex
cannot match — got a statutory citation instead of a helpline.

`_supports_adaptive_thinking` is therefore an **allowlist** of model families
known to accept the frontier parameters. An unrecognised model gets the plain
shape, which every current model accepts; the failure direction of a wrong
guess is then a slightly less capable request, not a 400 on every call.

Other details that are load-bearing on current models:

- Structured outputs use `output_config={"format": ...}`, not the deprecated
  `output_format`. `output_config.effort` is never sent: it errors on
  Haiku-class models, and the default (`high`) is what the generator wants.
- Adaptive thinking is `{"type": "adaptive"}` on Opus-tier models;
  `budget_tokens` is rejected there. Haiku 4.5 is the inverse — it takes
  `{"type": "enabled", "budget_tokens": N}` and rejects adaptive — but this
  module sends no thinking parameter at all for it: the Haiku-role calls
  here are a five-word classification and a translation, neither of which
  needs reasoning tokens paid for on every question.
- Assistant prefill returns a 400 and is not used — shape comes from the
  schema.
- Server-side refusal fallbacks are an Opus-5/Fable feature, enabled by
  default for those models, and `stop_reason == "refusal"` is checked before
  reading content.
- Prompt caching: the system prompt carries the cache breakpoint; per-turn
  retrieved chunks live in `messages` after it, so they never invalidate it.
"""

from typing import Any

from anthropic import Anthropic

from openlaw.answers.models import Completion

FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Model-family prefixes that accept `thinking={"type": "adaptive"}` together
#: with the server-side refusal-fallback beta. Deliberately an allowlist and
#: deliberately prefix-based: it is a capability question ("is this a
#: frontier-tier model?"), not an identity one, so a version bump inside a
#: family needs no code change, while a new cheap family is treated as
#: plain until someone verifies otherwise. See the module docstring.
ADAPTIVE_THINKING_FAMILIES: tuple[str, ...] = (
    "claude-opus-",
    "claude-fable-",
    "claude-mythos-",
)


def _supports_adaptive_thinking(model: str) -> bool:
    """Return whether `model` accepts adaptive thinking and refusal fallbacks."""
    return model.startswith(ADAPTIVE_THINKING_FAMILIES)


#: `response.usage` fields worth carrying into `Completion.raw` for
#: downstream cost metering. `cache_creation_input_tokens` and
#: `cache_read_input_tokens` are priced far below plain `input_tokens`, so
#: dropping them would overstate spend on every cache hit.
_USAGE_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _usage_raw(response: Any, requested_model: str) -> dict[str, Any]:
    """Return per-call token usage plus the model that actually served the
    request, for `Completion.raw` -- the field quota metering reads.

    Reads named attributes with `getattr` rather than requiring a
    `model_dump()` method, so this works against both the real Anthropic SDK
    response and a lightweight test double. `response.model` can differ from
    `requested_model` when a server-side refusal fallback (see
    `build_request`) re-routes the call to a different model -- cost
    tracking must key off what actually ran, not what was requested.
    """
    raw: dict[str, Any] = {"model": getattr(response, "model", requested_model)}
    usage = getattr(response, "usage", None)
    if usage is not None:
        usage_values = {
            field: value
            for field in _USAGE_FIELDS
            if (value := getattr(usage, field, None)) is not None
        }
        if usage_values:
            raw["usage"] = usage_values
    return raw


def build_request(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    output_schema: dict[str, Any] | None,
    max_tokens: int,
) -> dict[str, Any]:
    """Return the request body for `model`, shaped to what it accepts.

    Everything every model accepts goes in unconditionally; the frontier-only
    parameters are added only for the families in
    `ADAPTIVE_THINKING_FAMILIES`. Returns a new dict — no argument is mutated.
    """
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
    }
    if _supports_adaptive_thinking(model):
        request["thinking"] = {"type": "adaptive"}
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    if output_schema is not None:
        request["output_config"] = {
            "format": {"type": "json_schema", "schema": output_schema}
        }
    return request


class AnthropicAdapter:
    """An `LLMAdapter` backed by the Anthropic API."""

    def __init__(self, api_key: str) -> None:
        self._client = Anthropic(api_key=api_key)

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> Completion:
        """Return a completion, with caching and per-model request shaping."""
        response = self._client.beta.messages.create(
            **build_request(model, system, messages, output_schema, max_tokens)
        )
        raw = _usage_raw(response, model)

        if response.stop_reason == "refusal":
            return Completion(text="", stop_reason="refusal", raw=raw)

        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return Completion(
            text=text, stop_reason=response.stop_reason or "end_turn", raw=raw
        )
