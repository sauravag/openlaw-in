"""Unit tests fake the HTTP boundary with `httpx.MockTransport` -- no network,
no API key of any kind. The one `@pytest.mark.integration` test talks to the
real OpenRouter API and skips cleanly when no key is configured.

The fail-closed block near the bottom is the load-bearing part of this file.
A provider swap must not be able to weaken the answer contract: the four ways
a model can fail to produce a valid structured answer -- malformed JSON, a
refusal, an empty body, and a schema-violating object -- must each land on a
result the existing validator rejects, never uncited prose presented as an
answer. Those four are exercised end-to-end through `generate_answer`, not
just against the adapter, so what is asserted is what a user could be shown
rather than an intermediate value.
"""

import json
from typing import Any

import httpx
import pytest

from openlaw.answers.crisis_classifier import detect_crisis_with_classifier
from openlaw.answers.escalation import CrisisKind
from openlaw.answers.generate import AnswerResult, generate_answer
from openlaw.answers.models import ModelRegistry, ModelRole
from openlaw.answers.openrouter_client import (
    OPENROUTER_URL,
    DEFAULT_REASONING,
    STRUCTURED_OUTPUT_NAME,
    OpenRouterAdapter,
    OpenRouterError,
    build_request,
)
from openlaw.answers.prompts import SYSTEM_PROMPT
from openlaw.answers.schema import ANSWER_JSON_SCHEMA
from openlaw.config import get_settings
from openlaw.language.translate import translate_query
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk

MODEL = "z-ai/glm-5.3-flash"
CHUNK_ID = "IND_central_00042_s012_p1"


# --- test doubles ----------------------------------------------------------


class _Recorder:
    """Serves queued payloads over `httpx.MockTransport`, recording every
    request body it was sent."""

    def __init__(self, payloads: list[Any], status: int = 200) -> None:
        self._payloads = list(payloads)
        self._status = status
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        self.urls.append(str(request.url))
        payload = self._payloads.pop(0) if self._payloads else {}
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(self._status, json=payload)


def _adapter(
    payloads: list[Any], status: int = 200, **kwargs: Any
) -> tuple[OpenRouterAdapter, _Recorder]:
    recorder = _Recorder(payloads, status=status)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    return OpenRouterAdapter(api_key="sk-or-test", client=client, **kwargs), recorder


def _chat(content: str, **extra: Any) -> dict[str, Any]:
    """A minimal, well-formed OpenRouter chat-completion payload."""
    return {
        "model": MODEL,
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        **extra,
    }


def _registry() -> ModelRegistry:
    return ModelRegistry(dict.fromkeys(ModelRole, MODEL))


def _context() -> RetrievalContext:
    return RetrievalContext(
        query="my landlord kept my deposit",
        chunks=(
            RetrievedChunk(
                chunk_id=CHUNK_ID,
                text="A landlord shall refund the deposit within one month.",
                source_url="https://indiacode.nic.in/act/42",
                score=0.9,
                kind=ChunkKind.PROVISION,
            ),
        ),
    )


# --- request shaping -------------------------------------------------------


def test_request_maps_output_schema_to_a_strict_json_schema_response_format() -> None:
    """The product's core guarantee is that every claim carries chunk_ids the
    validator checks by exact set membership -- structured output is what
    keeps that a parsing-free set operation rather than prose scraping."""
    request = build_request(MODEL, "sys", [], ANSWER_JSON_SCHEMA, 4096)

    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": STRUCTURED_OUTPUT_NAME,
            "strict": True,
            "schema": ANSWER_JSON_SCHEMA,
        },
    }


def test_request_omits_response_format_when_no_schema_is_requested() -> None:
    """The translator role asks for free-form text."""
    assert "response_format" not in build_request(MODEL, "sys", [], None, 512)


def test_request_flattens_anthropic_style_content_blocks_into_openai_messages() -> None:
    """Every call site in this codebase builds Anthropic-shaped content
    blocks (`[{"type": "text", ...}]`). Forwarding those unchanged to an
    OpenAI-compatible endpoint is exactly the provider-specific request-
    shaping mistake that once left the crisis classifier dead on arrival."""
    request = build_request(
        MODEL,
        "you are a classifier",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "world"},
                ],
            }
        ],
        None,
        64,
    )

    assert request["messages"] == [
        {"role": "system", "content": "you are a classifier"},
        {"role": "user", "content": "hello world"},
    ]


def test_request_accepts_a_plain_string_message_content() -> None:
    request = build_request(MODEL, "sys", [{"role": "user", "content": "ping"}], None, 8)

    assert request["messages"][1] == {"role": "user", "content": "ping"}


def test_request_asks_for_low_reasoning_effort_by_default() -> None:
    """NOT `{"enabled": false}`: `z-ai/glm-5.3-flash` answers that with a 400
    ("Reasoning is mandatory for this endpoint and cannot be disabled"), so
    the obvious mapping would have failed every call in production while
    every mocked test here passed. `low` is the cheapest portable level --
    reasoning tokens come out of the caller's `max_tokens` budget."""
    assert DEFAULT_REASONING == {"effort": "low"}
    assert build_request(MODEL, "sys", [], None, 256)["reasoning"] == DEFAULT_REASONING


def test_reasoning_is_never_sent_as_disabled() -> None:
    """A regression guard with a live 400 behind it: see the test above."""
    assert build_request(MODEL, "sys", [], None, 256)["reasoning"] != {"enabled": False}


def test_reasoning_is_overridable_per_deployment() -> None:
    adapter, recorder = _adapter([_chat("ok")], reasoning={"effort": "low"})

    adapter.complete(MODEL, "sys", [], None, 64)

    assert recorder.requests[0]["reasoning"] == {"effort": "low"}


def test_adapter_posts_to_openrouter_with_a_bearer_token() -> None:
    adapter, recorder = _adapter([_chat("ok")])

    adapter.complete(MODEL, "sys", [], None, 64)

    assert recorder.urls == [OPENROUTER_URL]
    assert recorder.headers[0]["authorization"] == "Bearer sk-or-test"
    assert recorder.requests[0]["model"] == MODEL
    assert recorder.requests[0]["max_tokens"] == 64


def test_build_request_does_not_mutate_its_arguments() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    schema = dict(ANSWER_JSON_SCHEMA)

    build_request(MODEL, "sys", messages, schema, 16)

    assert messages == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert schema == ANSWER_JSON_SCHEMA


# --- Completion.raw: quota metering depends on it --------------------------


def test_completion_carries_usage_and_the_serving_model_in_raw() -> None:
    """Quota metering reads per-call token usage and the model that actually
    served the request out of `Completion.raw` -- a Plan 3 carry-forward that
    had to be fixed once already."""
    adapter, _ = _adapter(
        [
            _chat(
                "ok",
                usage={
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                    "cost": 0.000033,
                },
            )
        ]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.text == "ok"
    assert completion.stop_reason == "end_turn"
    assert completion.raw["model"] == MODEL
    assert completion.raw["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "cost": 0.000033,
    }


# --- Task 12: measured cache-hit metrics, never assumed ---------------------


def test_cached_prompt_tokens_surfaces_when_the_provider_reports_it() -> None:
    """A real cache hit is reported under `prompt_tokens_details.cached_tokens`
    (the OpenRouter equivalent of Anthropic's `cache_read_input_tokens`,
    per spec #8.4) and must reach `Completion.raw` so it is observable."""
    adapter, _ = _adapter(
        [
            _chat(
                "ok",
                usage={
                    "prompt_tokens": 1200,
                    "completion_tokens": 30,
                    "total_tokens": 1230,
                    "cost": 0.0003,
                    "prompt_tokens_details": {"cached_tokens": 900},
                },
            )
        ]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.raw["usage"]["cached_prompt_tokens"] == 900


def test_cached_prompt_tokens_is_absent_not_zero_when_the_provider_omits_it() -> None:
    """Absent is NOT zero: a response with no `prompt_tokens_details` at all
    made no claim about caching one way or the other, so reporting a 0% hit
    rate for it would be a measurement we did not make."""
    adapter, _ = _adapter(
        [_chat("ok", usage={"prompt_tokens": 1200, "completion_tokens": 30})]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert "cached_prompt_tokens" not in completion.raw["usage"]


def test_cache_discount_is_carried_when_present() -> None:
    adapter, _ = _adapter(
        [
            _chat(
                "ok",
                usage={
                    "prompt_tokens": 1200,
                    "completion_tokens": 30,
                    "cache_discount": 0.00018,
                },
            )
        ]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.raw["usage"]["cache_discount"] == 0.00018


@pytest.mark.parametrize("malformed_usage", ["not-a-dict", None])
def test_a_malformed_usage_degrades_to_no_usage_data_without_raising(
    malformed_usage: Any,
) -> None:
    adapter, _ = _adapter([_chat("ok", usage=malformed_usage)])

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.text == "ok"
    assert "usage" not in completion.raw


def test_a_malformed_prompt_tokens_details_degrades_to_no_cached_count() -> None:
    adapter, _ = _adapter(
        [
            _chat(
                "ok",
                usage={"prompt_tokens": 1200, "prompt_tokens_details": "not-a-dict"},
            )
        ]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert "cached_prompt_tokens" not in completion.raw["usage"]


def test_each_call_logs_one_usage_line_with_the_cache_figure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """How a deployment answers "is the cache hitting?" from logs, with a
    number, without reading `Completion.raw` directly."""
    adapter, _ = _adapter(
        [
            _chat(
                "ok",
                usage={
                    "prompt_tokens": 1200,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 900},
                },
            )
        ]
    )

    with caplog.at_level("INFO", logger="openlaw.answers.openrouter_client"):
        adapter.complete(MODEL, "sys", [], None, 64)

    [record] = [r for r in caplog.records if "openrouter usage" in r.message]
    assert f"model={MODEL}" in record.message
    assert "prompt=1200" in record.message
    assert "cached=900" in record.message
    assert "completion=30" in record.message


def test_raw_falls_back_to_the_requested_model_when_the_payload_omits_one() -> None:
    adapter, _ = _adapter(
        [{"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.raw["model"] == MODEL
    assert "usage" not in completion.raw


def test_raw_records_the_serving_model_when_it_differs_from_the_requested_one() -> None:
    """OpenRouter routes to whichever upstream provider it picked; cost
    tracking must key off what actually ran, not what was asked for."""
    adapter, _ = _adapter([_chat("hi", model="z-ai/glm-5.3-flash:nitro")])

    assert adapter.complete(MODEL, "sys", [], None, 64).raw["model"] == (
        "z-ai/glm-5.3-flash:nitro"
    )


def test_a_length_finish_reason_is_reported_as_max_tokens() -> None:
    adapter, _ = _adapter(
        [{"choices": [{"finish_reason": "length", "message": {"content": "trunc"}}]}]
    )

    assert adapter.complete(MODEL, "sys", [], None, 8).stop_reason == "max_tokens"


def test_reasoning_text_is_never_folded_into_the_answer_text() -> None:
    """`message.reasoning` is a separate field. Concatenating it would put
    unvalidated chain-of-thought into the string the answer is parsed from."""
    adapter, _ = _adapter(
        [
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}", "reasoning": "let me think..."},
                    }
                ]
            }
        ]
    )

    assert adapter.complete(MODEL, "sys", [], None, 64).text == "{}"


# --- refusals and transport errors -----------------------------------------


def test_an_explicit_refusal_becomes_an_empty_refusal_completion() -> None:
    adapter, _ = _adapter(
        [
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"refusal": "I can't help."}}
                ]
            }
        ]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.stop_reason == "refusal"
    assert completion.text == ""


def test_a_content_filter_finish_reason_is_treated_as_a_refusal() -> None:
    adapter, _ = _adapter(
        [{"choices": [{"finish_reason": "content_filter", "message": {"content": "x"}}]}]
    )

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.stop_reason == "refusal"
    assert completion.text == ""


def test_a_response_with_no_choices_yields_an_empty_completion_not_prose() -> None:
    adapter, _ = _adapter([{"model": MODEL, "choices": []}])

    completion = adapter.complete(MODEL, "sys", [], None, 64)

    assert completion.text == ""
    assert completion.raw["model"] == MODEL


def test_an_api_level_error_body_raises_rather_than_being_swallowed() -> None:
    adapter, _ = _adapter([{"error": {"code": 402, "message": "insufficient credits"}}])

    with pytest.raises(OpenRouterError, match="insufficient credits"):
        adapter.complete(MODEL, "sys", [], None, 64)


def test_a_non_json_body_raises() -> None:
    adapter, _ = _adapter([httpx.Response(200, text="<html>gateway</html>")])

    with pytest.raises(OpenRouterError, match="non-JSON"):
        adapter.complete(MODEL, "sys", [], None, 64)


def test_a_non_object_body_raises() -> None:
    adapter, _ = _adapter([["not", "an", "object"]])

    with pytest.raises(OpenRouterError, match="non-object"):
        adapter.complete(MODEL, "sys", [], None, 64)


def test_an_http_error_status_raises() -> None:
    adapter, _ = _adapter([{"error": "unauthorized"}], status=401)

    with pytest.raises(httpx.HTTPStatusError):
        adapter.complete(MODEL, "sys", [], None, 64)


# --- fail closed, never open ------------------------------------------------
#
# Each case below goes through the whole pipeline, so what is asserted is what
# a user could be shown: no claims, no sources, not answerable, and no
# unverified prose. The first queued payload answers the crisis-classifier
# call `generate_answer` always makes first; the second is the generator's.

_NO_CRISIS = _chat(json.dumps({"crisis": "none"}))


def _generated(generator_payload: Any) -> AnswerResult:
    adapter, _ = _adapter([_NO_CRISIS, generator_payload])
    return generate_answer(
        "my landlord kept my deposit", _context(), adapter, _registry()
    )


def _assert_fails_closed(result: AnswerResult) -> None:
    assert result.answer.is_answerable is False
    assert result.answer.claims == ()
    assert result.answer.sources == ()
    assert result.answer.unverified_summary is None


def test_malformed_json_from_the_generator_fails_closed() -> None:
    _assert_fails_closed(_generated(_chat('{"summary": "you can sue", "claims": [')))


def test_a_generator_refusal_fails_closed() -> None:
    _assert_fails_closed(
        _generated({"choices": [{"finish_reason": "stop", "message": {"refusal": "no"}}]})
    )


def test_an_empty_generator_body_fails_closed() -> None:
    _assert_fails_closed(_generated({"model": MODEL}))


def test_a_schema_violating_generator_object_fails_closed() -> None:
    """Valid JSON, wrong shape: prose in `summary`, a claim with no
    `chunk_ids` at all. Nothing here is citable, so nothing may survive."""
    _assert_fails_closed(
        _generated(
            _chat(
                json.dumps(
                    {
                        "summary": "Your landlord must refund the deposit.",
                        "claims": [{"text": "You can file a suit."}],
                        "sufficient": True,
                    }
                )
            )
        )
    )


def test_a_hallucinated_chunk_id_is_still_dropped_by_the_validator() -> None:
    """Well-formed, schema-valid, and citing an id that was never retrieved --
    the exact case the validator exists to catch, unchanged by the provider."""
    result = _generated(
        _chat(
            json.dumps(
                {
                    "summary": "s",
                    "claims": [
                        {"text": "invented", "chunk_ids": ["IND_central_99999_s001_p1"]}
                    ],
                    "sufficient": True,
                }
            )
        )
    )

    _assert_fails_closed(result)
    assert result.answer.dropped


def test_a_well_grounded_answer_survives_end_to_end() -> None:
    """The positive control: the same wiring must actually produce an
    answerable, cited result, or the fail-closed tests above prove nothing."""
    result = _generated(
        _chat(
            json.dumps(
                {
                    "summary": "The deposit must be refunded within a month.",
                    "claims": [
                        {
                            "text": "A landlord must refund the deposit within a month.",
                            "chunk_ids": [CHUNK_ID],
                        }
                    ],
                    "sufficient": True,
                }
            )
        )
    )

    assert result.answer.is_answerable is True
    assert len(result.answer.claims) == 1
    assert result.answer.sources[0].source_url == "https://indiacode.nic.in/act/42"


# --- the other two roles use the same adapter -------------------------------


def test_the_crisis_classifier_works_through_this_adapter() -> None:
    """Plan 3 found the classifier dead on arrival because of provider-
    specific request shaping, and the failure was silent (an unparseable
    classifier response reads as "no crisis"). This asserts the classifier
    round-trips, on a euphemism the regex layer cannot match."""
    adapter, recorder = _adapter([_chat(json.dumps({"crisis": "self_harm"}))])

    detection = detect_crisis_with_classifier("I want to end it all", adapter, _registry())

    assert detection.crisis is CrisisKind.SELF_HARM
    assert detection.failure is None
    assert recorder.requests[0]["response_format"]["json_schema"]["strict"] is True


def test_the_translator_works_through_this_adapter() -> None:
    adapter, recorder = _adapter([_chat("  my landlord kept my deposit  ")])

    translated = translate_query("मेरा मकान मालिक", "hi", adapter, _registry())

    assert translated == "my landlord kept my deposit"
    assert "response_format" not in recorder.requests[0]


# --- live service -----------------------------------------------------------


@pytest.mark.integration
def test_a_second_identical_prefix_call_hits_the_automatic_cache() -> None:
    """Prompt caching on the Z.AI/GLM route is automatic -- there is no
    `cache_control` breakpoint to set -- so the only way to verify it exists
    at all is to call twice with byte-identical content and read back the
    SECOND call's usage. Run manually (not in CI, per this module's existing
    `@pytest.mark.integration` convention): cache engagement depends on
    OpenRouter's own infrastructure and timing, which this project does not
    control and must not assert on every push. Skips cleanly with no key
    configured.

    Uses the real, long `SYSTEM_PROMPT` as the stable prefix precisely
    because it is the thing `openlaw.answers.prompts`'s module docstring
    says genuinely caches -- a short, synthetic string might fall under
    whatever minimum-size threshold the provider applies before caching
    engages at all, and silently prove nothing.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured")

    adapter = OpenRouterAdapter(api_key=settings.openrouter_api_key)
    messages = [{"role": "user", "content": "Question: how do I register a company in India?"}]

    first = adapter.complete(
        model=MODEL, system=SYSTEM_PROMPT, messages=messages, output_schema=None, max_tokens=16
    )
    second = adapter.complete(
        model=MODEL, system=SYSTEM_PROMPT, messages=messages, output_schema=None, max_tokens=16
    )

    assert first.text
    assert second.text
    cached = second.raw.get("usage", {}).get("cached_prompt_tokens")
    assert cached is not None
    assert cached > 0


@pytest.mark.integration
def test_adapter_completes_against_the_live_openrouter_api() -> None:
    """Skips cleanly when no OpenRouter key is configured."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured")

    adapter = OpenRouterAdapter(api_key=settings.openrouter_api_key)
    completion = adapter.complete(
        model=MODEL,
        system="Reply with exactly one word: pong",
        messages=[{"role": "user", "content": "ping"}],
        output_schema=None,
        max_tokens=64,
    )

    assert completion.text
    assert completion.raw["usage"]["completion_tokens"] > 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("मुझे मारता है", CrisisKind.DOMESTIC_VIOLENCE),
        ("mera pati mujhe marta h", CrisisKind.DOMESTIC_VIOLENCE),
        ("my sister is being beaten by her husband", CrisisKind.DOMESTIC_VIOLENCE),
        ("I want to end it all", CrisisKind.SELF_HARM),
        ("how do I register property in Pune", None),
    ],
)
def test_the_live_classifier_fits_inside_its_token_budget(
    question: str, expected: CrisisKind | None
) -> None:
    """The one budget with no headroom: 256 tokens, shared with reasoning.

    A truncated classifier response is unparseable, and an unparseable one
    reads as "no crisis" -- silently, for as long as the condition lasts.
    These are the exact phrasings `openlaw.answers.crisis_classifier`'s
    docstring names as the regex layer's measured blind spots, so a
    reasoning setting that starves this call would fail here rather than in
    front of someone in danger. Skips cleanly with no key configured.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured")

    detection = detect_crisis_with_classifier(
        question,
        OpenRouterAdapter(api_key=settings.openrouter_api_key),
        _registry(),
    )

    assert detection.failure is None
    assert detection.crisis is expected
