"""Per-role provider routing: the provider lives in the model string.

A deployment does not pick one LLM provider; it picks one **per role**. The
configuration this module exists for is the mixed one -- the generator on
OpenRouter (`z-ai/glm-5.3-flash`, cheap and long-context) while the
translator and classifier stay on Anthropic Haiku -- and no single global
`provider` switch can express that.

**The shape: one self-describing string per role.**

    OPENLAW_MODEL_GENERATOR=openrouter:z-ai/glm-5.3-flash
    OPENLAW_MODEL_TRANSLATOR=anthropic:claude-haiku-4-5
    OPENLAW_MODEL_CLASSIFIER=claude-haiku-4-5

Chosen over three parallel `*_provider` settings for three reasons. It makes
invalid combinations unrepresentable -- a separate provider field lets
someone pair `anthropic` with `z-ai/glm-5.3-flash` and get a confusing
runtime failure, whereas one string cannot disagree with itself. It extends
to `OPENLAW_MODEL_TRANSLATOR_OVERRIDES` for free, since each per-language
override is already a model string and can now carry its provider with no
new setting and no second parsing rule to learn. And it keeps the settings
surface at three values rather than six.

**An unprefixed value means Anthropic.** That is not a convenience, it is
the compatibility guarantee: every value shipped today (`claude-opus-5`,
`claude-haiku-4-5`), every `.env`, and every existing test keeps behaving
exactly as it did. Only `openrouter:` has to be written explicitly.

Only the **first** colon separates prefix from model, because OpenRouter's
own model ids carry colons (`z-ai/glm-5.3-flash:free`, `...:nitro`) and
splitting anywhere else would mangle them. The corollary is that any value
containing a colon must start with a known provider name: an unrecognised
prefix is a configuration error, raised at startup by `referenced_providers`
with the setting and the offending value named, not a surprise on some
citizen's question hours later.

**Provider clients are constructed lazily and validated by actual use.**
`RoutingAdapter` takes factories, not adapters, and calls one only when a
role first routes to that provider. A deployment whose every role is on
OpenRouter therefore never constructs an Anthropic client -- and
`referenced_providers` is what lets the composition root demand *only* the
keys that deployment actually needs. Demanding an Anthropic key from someone
who is not using Anthropic is exactly the kind of thing that makes a
self-host look broken.

Per-model request shaping is untouched by any of this. The routing layer
chooses the provider; each provider adapter still shapes its own request
from the bare model name it is handed (`anthropic_client.build_request`'s
adaptive-thinking allowlist, `openrouter_client`'s reasoning default).
"""

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, NamedTuple

from openlaw.answers.models import Completion, LLMAdapter, ModelRegistry


class ModelRouteError(ValueError):
    """A configured model string does not name a usable provider/model."""


class LLMProvider(Enum):
    """A provider a role can be routed to."""

    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"


#: The prefix an operator writes, mapped to the provider it selects.
PROVIDER_PREFIXES: dict[str, LLMProvider] = {
    provider.value: provider for provider in LLMProvider
}

#: What an unprefixed model string means. Anthropic, permanently: this is the
#: backwards-compatibility guarantee for every value configured before
#: routing existed, not a default to reconsider later.
IMPLICIT_PROVIDER = LLMProvider.ANTHROPIC


class ModelRoute(NamedTuple):
    """A resolved model string: which provider, and the bare model name."""

    provider: LLMProvider
    model: str


def parse_model_route(value: str) -> ModelRoute:
    """Return the provider and bare model name encoded in `value`.

    `"claude-opus-5"` routes to Anthropic unchanged;
    `"openrouter:z-ai/glm-5.3-flash:free"` routes to OpenRouter as
    `"z-ai/glm-5.3-flash:free"`. Raises `ModelRouteError` on an unknown
    prefix, a prefix with no model after it, or a blank value -- never
    guesses, because guessing would send a request a provider cannot serve
    and turn a typo into a runtime failure on a user's question.
    """
    prefix, separator, remainder = value.partition(":")
    if not separator:
        if not value.strip():
            raise ModelRouteError("model name is empty")
        return ModelRoute(IMPLICIT_PROVIDER, value)

    provider = PROVIDER_PREFIXES.get(prefix)
    if provider is None:
        known = ", ".join(sorted(PROVIDER_PREFIXES))
        raise ModelRouteError(
            f"unknown provider prefix {prefix!r} in model {value!r} -- expected "
            f"one of: {known}; or no prefix at all, which means "
            f"{IMPLICIT_PROVIDER.value}"
        )
    if not remainder.strip():
        raise ModelRouteError(f"model {value!r} has a provider prefix but no model name")
    return ModelRoute(provider, remainder)


def referenced_providers(registry: ModelRegistry) -> frozenset[LLMProvider]:
    """Return every provider some role (or language override) routes to.

    This is what turns "validate the selected provider's key" into "validate
    the key of each provider actually in use". Raises `ModelRouteError`
    naming the setting and its value when any configured model string is
    unroutable, so a typo is a startup failure an operator can act on rather
    than a 500 on a citizen's question.
    """
    providers: set[LLMProvider] = set()
    for setting, model in registry.configured_models():
        try:
            providers.add(parse_model_route(model).provider)
        except ModelRouteError as error:
            raise ModelRouteError(f"{setting}={model!r} is invalid: {error}") from error
    return frozenset(providers)


class RoutingAdapter:
    """An `LLMAdapter` that dispatches on the provider in the model string.

    Takes *factories* rather than adapters so a provider's client is built
    only if some call actually routes to it, and built at most once. The
    memoised `_adapters` map is a construction cache, not shared data: no
    caller can observe it, and nothing about a request is retained.
    """

    def __init__(self, factories: Mapping[LLMProvider, Callable[[], LLMAdapter]]) -> None:
        self._factories = dict(factories)
        self._adapters: dict[LLMProvider, LLMAdapter] = {}

    def _adapter_for(self, provider: LLMProvider) -> LLMAdapter:
        """Return the adapter for `provider`, constructing it on first use."""
        cached = self._adapters.get(provider)
        if cached is not None:
            return cached
        factory = self._factories.get(provider)
        if factory is None:
            raise ModelRouteError(
                f"a model routes to {provider.value!r} but no {provider.value} "
                "client is configured -- this deployment was started without "
                "that provider's API key"
            )
        adapter = factory()
        self._adapters[provider] = adapter
        return adapter

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> Completion:
        """Route `model` to its provider and complete with the bare name."""
        route = parse_model_route(model)
        return self._adapter_for(route.provider).complete(
            route.model, system, messages, output_schema, max_tokens
        )
