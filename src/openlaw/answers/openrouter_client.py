"""OpenRouter implementation of the LLM adapter.

OpenRouter exposes **only** an OpenAI-compatible endpoint
(`/api/v1/chat/completions`). There is no Anthropic-native Messages endpoint
behind it, so pointing the Anthropic SDK at a different base URL cannot work
and this has to be a separate adapter rather than a base-URL setting. `httpx`
is used directly: the dependency is already present for retrieval, and the
request this module needs is a single JSON POST.

**Translating the request is where a provider swap goes wrong quietly.**
Every call site in this codebase builds Anthropic-shaped messages -- content
is a list of `{"type": "text", "text": ...}` blocks, and the system prompt is
a separate argument, not a message. Both have to be converted:
`_flatten_content` turns the blocks into the plain string an
OpenAI-compatible endpoint expects, and the system prompt becomes a leading
`{"role": "system"}` message. Forwarding either one unchanged is the same
class of mistake that once sent Opus-shaped parameters to Haiku and left
**every** crisis-classifier call raising in production -- silently, because
`detect_crisis_with_classifier` reads a failed classifier call as "no
crisis". The conversion here is unconditional and role-independent, so there
is no shape that works for the generator and breaks the classifier.

**Structured output is load-bearing.** `output_schema` maps to
`response_format: {"type": "json_schema", "json_schema": {..., "strict":
true}}`. Every schema this project sends (`ANSWER_JSON_SCHEMA`,
`CRISIS_CLASSIFIER_SCHEMA`) already sets `additionalProperties: false` and
lists every property as required, which is what `strict` mode demands.

**Reasoning: `{"effort": "low"}`, for every role.** OpenRouter takes a
`reasoning` parameter; Anthropic's `thinking` shaping does not transfer and
is not faked here. The obvious mapping -- disable it, since none of these
three roles is a deliberation task -- was tried against the live API first
and is **impossible on the target model**: `z-ai/glm-5.3-flash` answers
`reasoning: {"enabled": false}` with

    400 "Reasoning is mandatory for this endpoint and cannot be disabled."

so an adapter that sent it would have failed *every* call in production
while every mocked test passed. That is the shape of the Plan 3 classifier
bug, and it is the reason this parameter is chosen from measurement rather
than from what ought to be true.

What is left is how much reasoning, and that matters because reasoning
tokens are counted inside `completion_tokens` -- i.e. they are drawn from
the caller's `max_tokens` budget and billed at the output rate ($0.50/M).
Measured on the live model, one trivial call spends 33 reasoning tokens at
the model's own default, 20 at `low`, and 10 at `minimal`. `low` is the
setting sent here:

- CLASSIFIER: the tightest constraint -- a 256-token budget for one short
  JSON object. Reasoning eating that budget truncates the JSON, and
  `detect_crisis_with_classifier` reads unparseable output as "no crisis":
  silent, sustained, and exactly the failure that once left a woman typing
  "मुझे मारता है" with a statutory citation instead of a helpline. Measured
  at `low` across the phrasings that module's docstring names as the regex's
  blind spots (Devanagari, SMS transliteration, euphemism, third person):
  0-74 reasoning tokens, worst case 86 of the 256 available, every one
  classified correctly and none truncated. The live integration test at the
  bottom of `tests/answers/test_openrouter_client.py` re-checks that against
  the real API, because this is the one budget with no headroom to spare.
- TRANSLATOR: a translation needs no deliberation; `low` is the cheapest
  level this adapter can portably ask for.
- GENERATOR: grounded extraction from excerpts already in context, under a
  strict schema. A little reasoning plausibly helps citation fidelity, and
  4096 tokens leaves ample room for it.

`low` rather than `minimal` (10 tokens, marginally cheaper) because
`minimal` is not part of OpenRouter's portable effort set: every model
routed through this adapter accepts `low`, and saving ~10 output tokens is
not worth a per-model compatibility cliff on a code path whose failure mode
is silent. A deployment that wants to spend differently passes `reasoning=`
to the constructor; nothing else in the pipeline needs to know.

**Prompt caching does not transfer.** The Anthropic path marks the system
prompt with `cache_control`, so the long system prompt is billed at
cache-read rates on every question after the first. OpenRouter's
OpenAI-compatible surface has no equivalent this adapter can set, and faking
one would be worse than not having it. The cost consequence is real and
should be budgeted for: the full system prompt is billed at the normal input
rate on every call, for all three roles.

**Failure direction: closed, never open.** Nothing here ever invents text. A
refusal (`message.refusal`, or `finish_reason == "content_filter"`) returns
an empty `Completion` with `stop_reason="refusal"`; a response carrying no
choices returns an empty one too. Both are what `openlaw.answers.generate`
already turns into an unanswerable result, and malformed or schema-violating
JSON is passed through untouched so `DraftAnswer.from_json` rejects it the
way it always has. Transport and API-level errors raise, matching what the
Anthropic SDK does, so the classifier records them as failures rather than
mistaking them for a verdict.
"""

import logging
from typing import Any

import httpx

from openlaw.answers.models import Completion

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Identifies this application to OpenRouter (its documented, optional
#: attribution headers). Neither affects routing or billing.
APP_URL = "https://openlaw.in"
APP_TITLE = "OpenLaw.in"

#: Generation over a long context can take a while; the Anthropic SDK's own
#: default sits in the same range.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: `response_format.json_schema.name` is required by the OpenAI-compatible
#: schema envelope but is not otherwise meaningful to us.
STRUCTURED_OUTPUT_NAME = "openlaw_structured_output"

#: See the module docstring. Sent explicitly, for every role, rather than
#: omitted and left to whatever a model defaults to. NOT `{"enabled": false}`:
#: the target model rejects that with a 400, verified against the live API.
DEFAULT_REASONING: dict[str, Any] = {"effort": "low"}

#: `usage` fields worth carrying into `Completion.raw` for downstream cost
#: metering. `cost` is OpenRouter's own computed figure for the call, and the
#: cheapest available source of truth for spend.
_USAGE_FIELDS: tuple[str, ...] = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
)

#: OpenAI-compatible `finish_reason` values mapped onto the vocabulary the
#: rest of this codebase already uses (Anthropic's `stop_reason`). Only
#: "refusal" is branched on downstream, but keeping one vocabulary means a
#: future consumer cannot accidentally handle just one provider's.
_STOP_REASONS: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
    "tool_calls": "tool_use",
}


class OpenRouterError(RuntimeError):
    """OpenRouter returned something that is not a usable completion."""


def _flatten_content(content: Any) -> str:
    """Return the plain text of `content`, whatever shape it arrived in.

    Accepts a bare string (what the OpenAI surface itself uses) and the
    Anthropic-style list of typed blocks every call site in this codebase
    builds. Anything else -- `None`, a number, a block list with no text
    blocks -- yields the empty string, which fails closed: an empty
    completion is unanswerable, never prose.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        part["text"]
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def _to_chat_messages(
    system: str, messages: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Return `messages` as OpenAI-compatible chat messages, system first.

    Builds a new list; no argument is mutated.
    """
    converted = [{"role": "system", "content": system}]
    converted.extend(
        {
            "role": str(message.get("role") or "user"),
            "content": _flatten_content(message.get("content")),
        }
        for message in messages
    )
    return converted


def build_request(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    output_schema: dict[str, Any] | None,
    max_tokens: int,
    reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the request body for one OpenRouter chat completion.

    Returns a new dict — no argument is mutated.
    """
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _to_chat_messages(system, messages),
        "reasoning": dict(DEFAULT_REASONING if reasoning is None else reasoning),
    }
    if output_schema is not None:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": STRUCTURED_OUTPUT_NAME,
                "strict": True,
                "schema": output_schema,
            },
        }
    return request


def _usage_raw(payload: dict[str, Any], requested_model: str) -> dict[str, Any]:
    """Return per-call token usage plus the model that actually served the
    request, for `Completion.raw` -- the field quota metering reads.

    OpenRouter picks an upstream provider per call and reports what it
    actually used in `model`, which can differ from what was requested (a
    `:nitro`/`:floor` variant, or a fallback route). Cost tracking must key
    off what ran.
    """
    raw: dict[str, Any] = {"model": payload.get("model") or requested_model}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        values = {
            field: usage[field] for field in _USAGE_FIELDS if usage.get(field) is not None
        }
        if values:
            raw["usage"] = values
    return raw


def _error_text(error: Any) -> str:
    """Return a readable message for OpenRouter's error object.

    Never includes anything from the request, so an API key cannot reach a
    log through this path.
    """
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def read_payload(response: httpx.Response) -> dict[str, Any]:
    """Return the decoded response body, raising on anything unusable.

    Raises rather than returning an empty completion for these cases on
    purpose: they are infrastructure failures, not model output. The crisis
    classifier records a raised call as a `ClassifierFailureEvent` (so a
    sustained outage stays countable after the fact), and the generator
    surfaces it the same way an Anthropic SDK error already does.
    """
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise OpenRouterError(f"OpenRouter returned a non-JSON body: {error}") from error
    if not isinstance(payload, dict):
        raise OpenRouterError(
            f"OpenRouter returned a non-object body of type {type(payload).__name__}"
        )
    if payload.get("error"):
        raise OpenRouterError(
            f"OpenRouter returned an error: {_error_text(payload['error'])}"
        )
    return payload


def parse_response(payload: dict[str, Any], requested_model: str) -> Completion:
    """Turn one decoded OpenRouter response into a `Completion`.

    Fails closed on every path: a refusal and a choice-less response both
    yield empty text, which `openlaw.answers.generate` already treats as
    unanswerable. Model output that is merely *wrong* (malformed or
    schema-violating JSON) is passed through verbatim, so the existing
    `DraftAnswer.from_json` rejection still does that job.
    """
    raw = _usage_raw(payload, requested_model)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        logger.error(
            "OpenRouter response carried no usable choices (model=%s); treating "
            "it as an empty, unanswerable completion",
            raw["model"],
        )
        return Completion(text="", stop_reason="error", raw=raw)

    choice = choices[0]
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    finish_reason = str(choice.get("finish_reason") or "stop")

    if message.get("refusal") or finish_reason == "content_filter":
        return Completion(text="", stop_reason="refusal", raw=raw)

    return Completion(
        text=_flatten_content(message.get("content")),
        stop_reason=_STOP_REASONS.get(finish_reason, finish_reason),
        raw=raw,
    )


class OpenRouterAdapter:
    """An `LLMAdapter` backed by OpenRouter's OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        reasoning: dict[str, Any] | None = None,
        url: str = OPENROUTER_URL,
    ) -> None:
        self._url = url
        self._reasoning = dict(DEFAULT_REASONING if reasoning is None else reasoning)
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
        # Held here rather than on the client so an injected client (tests,
        # or a caller pooling connections) is still authenticated.
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": APP_URL,
            "X-Title": APP_TITLE,
        }

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> Completion:
        """Return a completion from `model`, failing closed on bad output."""
        response = self._client.post(
            self._url,
            headers=self._headers,
            json=build_request(
                model, system, messages, output_schema, max_tokens, self._reasoning
            ),
        )
        return parse_response(read_payload(response), model)
