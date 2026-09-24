"""Anthropic implementation of the LLM adapter.

Details that are load-bearing on current models:

- Structured outputs use `output_config={"format": ...}`, not the deprecated
  `output_format`.
- Thinking is `{"type": "adaptive"}`; `budget_tokens` is rejected on Opus 5.
- Assistant prefill returns a 400 and is not used — shape comes from the schema.
- Server-side refusal fallbacks are enabled by default on Opus 5, and
  `stop_reason == "refusal"` is checked before reading content.
- Prompt caching: the system prompt carries the cache breakpoint; per-turn
  retrieved chunks live in `messages` after it, so they never invalidate it.
"""

from typing import Any

from anthropic import Anthropic

from openlaw.answers.models import Completion

FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
        """Return a completion, with caching, adaptive thinking and fallbacks."""
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
            "thinking": {"type": "adaptive"},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if output_schema is not None:
            request["output_config"] = {
                "format": {"type": "json_schema", "schema": output_schema}
            }

        response = self._client.beta.messages.create(**request)

        if response.stop_reason == "refusal":
            return Completion(text="", stop_reason="refusal")

        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return Completion(text=text, stop_reason=response.stop_reason or "end_turn")
