"""Production dependency wiring.

The only module that knows how every piece from Plans 1-5 fits together into a
running service. Search and graph expansion are injected as callables so the
composition (`QdrantGraphRetriever`) is testable without a live Qdrant or
Neo4j -- see `tests/api/test_deps.py`.

**The query embedder is built exactly once, at process startup.**
`openlaw.api.app.create_app` deliberately accepts a pre-built `answer_fn` and
loads nothing itself: the default local `voyage-4-nano` embedder
(`openlaw.retrieval.factory.build_query_embedder`) takes roughly 2.5 seconds
to load. `build_answer_fn` below is the one place that construction happens,
and it happens once, when `production_app()` runs -- never inside the
`answer_fn` closure it returns, which is called once per request. What DOES
get rebuilt per request is a cheap `QdrantGraphRetriever`, closing over the
already-loaded embedder plus a `search_fn`/`expand_fn` pair curried with that
request's question text -- see `_build_search_fn`'s docstring for why the
retriever itself cannot be built once alongside the embedder.

**Never call `answer_question`/`answer_in_language` a second time outside the
`/ask` endpoint.** `openlaw.api.routes.ask` calls `answer_fn(...)` and then
`persist_answer_events(...)` exactly once per turn. `answer_fn` below calls
only `answer_in_language` -- never `persist_answer_events` itself -- so events
are written exactly once, at the endpoint, never here. Adding a second call
to `answer_in_language`/`answer_question` anywhere in this module (a health
check, a warm-up call, a CLI wrapper) would either double-write the
hallucination/classifier-failure logs or, if that second call's events are
silently discarded, lose them outright.

**Required secrets are validated before anything expensive is built, and
which secrets are required follows the configuration.** `production_app()`
fails fast with a single, human-readable message naming every missing
setting -- never a raw stack trace and never a service that starts and only
fails once a real user asks a question. Provider selection is per-role (the
provider is encoded in each model string -- see `openlaw.answers.routing`),
so `_require_provider_keys` demands a key for exactly the providers this
deployment's roles actually route to: an all-OpenRouter deployment is never
asked for an Anthropic key, and never constructs an Anthropic client. See
`_require_settings`, `_require_provider_keys` and `build_llm_adapter`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

import httpx
from fastapi import FastAPI
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.models import LLMAdapter, ModelRegistry
from openlaw.answers.openrouter_client import OpenRouterAdapter
from openlaw.answers.routing import (
    LLMProvider,
    ModelRouteError,
    RoutingAdapter,
    referenced_providers,
)
from openlaw.api.app import create_app
from openlaw.config import Settings, get_settings
from openlaw.db.session import engine_from_settings, session_scope
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks
from openlaw.language.glossary import load_glossary
from openlaw.answers.history import HistoryTurn
from openlaw.documents.context import (
    EMPTY_DOCUMENT_CONTEXT,
    DocumentContext,
)
from openlaw.language.pipeline import (
    LocalisedAnswer,
    resolve_document_crisis,
    answer_in_conversation,
)
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.query import QueryEmbedder
from openlaw.retrieval.fetch import fetch_chunks_by_id
from openlaw.retrieval.rerank import ScoreReranker, merge_chunks
from openlaw.retrieval.search import search

RETRIEVAL_LIMIT = 10
EXPANSION_LIMIT = 20

#: A resolved graph-expansion result: bare (id, label, source_url) rows from
#: `openlaw.graph.queries.expand_chunks`, turned into citable `RetrievedChunk`s.
ExpandFn = Callable[[list[str]], tuple[RetrievedChunk, ...]]
#: A vector search over the configured corpus collections for one query.
SearchFn = Callable[[list[float]], tuple[RetrievedChunk, ...]]
#: A read-only re-fetch of specific corpus chunks by id, for the chunks an
#: earlier turn's claims cite (`openlaw.retrieval.fetch.fetch_chunks_by_id`).
FetchFn = Callable[[tuple[str, ...]], tuple[RetrievedChunk, ...]]


class QdrantGraphRetriever:
    """Composes vector search with bounded graph expansion.

    Satisfies `openlaw.language.pipeline.Retriever` structurally. `search_fn`
    and `expand_fn` are injected rather than this class calling
    `openlaw.retrieval.search.search`/`openlaw.graph.queries.expand_chunks`
    itself, so tests exercise the merge/rerank logic with no live Qdrant or
    Neo4j -- see the module docstring and `tests/api/test_deps.py`.
    """

    def __init__(
        self,
        embedder: QueryEmbedder,
        search_fn: SearchFn,
        expand_fn: ExpandFn,
        fetch_fn: FetchFn | None = None,
    ) -> None:
        self._embedder = embedder
        self._search = search_fn
        self._expand = expand_fn
        self._fetch = fetch_fn
        self._reranker = ScoreReranker()

    def _fetch_carried(self, chunk_ids: tuple[str, ...]) -> tuple[RetrievedChunk, ...]:
        """Re-fetch `chunk_ids`, or return nothing when no fetcher is wired.

        `fetch_fn` is optional so a single-shot caller (and every test written
        before conversations existed) builds this class unchanged. Carrying
        ids with no fetcher would silently drop them, so that combination is a
        programming error and says so -- the same reasoning
        `openlaw.language.pipeline._SingleTurnRetriever` applies to the same
        hazard.
        """
        if not chunk_ids:
            return ()
        if self._fetch is None:
            raise ValueError(
                "this retriever was built without a fetch_fn and cannot "
                "re-fetch carried chunk ids; build it with one for multi-turn "
                "conversations"
            )
        return self._fetch(chunk_ids)

    def retrieve(
        self, english_query: str, carried_chunk_ids: tuple[str, ...] = ()
    ) -> RetrievalContext:
        """Return the retrieval context for `english_query`.

        Satisfies BOTH `openlaw.language.pipeline.Retriever` (one argument)
        and `ConversationRetriever` (two), rather than two classes that would
        have to keep the same merge and rerank behaviour in step.

        `carried_chunk_ids` -- `openlaw.answers.history.carried_chunk_ids`'s
        output, the corpus chunks an earlier turn's surviving claims cite --
        is RE-FETCHED from the corpus (`openlaw.retrieval.fetch
        .fetch_chunks_by_id`), never replayed from a cached copy. That is the
        whole contract: a chunk that has since been re-indexed comes back in
        its current form, and one that has left the corpus altogether does not
        come back at all, so the claim that cited it is simply not replayed
        into the prompt and "cited implies shown this turn" stays true with no
        "cited earlier" branch anywhere in the validator.

        `merge_chunks` dedupes with the fresh side winning, so a carried id
        this turn's own search also returned is not counted twice.
        """
        vector = self._embedder.embed_query(english_query)
        primary = self._search(vector)
        expanded = self._expand([chunk.chunk_id for chunk in primary])
        carried = self._fetch_carried(carried_chunk_ids)
        merged = merge_chunks(primary, merge_chunks(expanded, carried))
        return RetrievalContext(
            query=english_query, chunks=self._reranker.rerank(english_query, merged)
        )


def _build_search_fn(
    client: httpx.Client, settings: Settings, question: str
) -> SearchFn:
    """Return a `SearchFn` for one request's `question`.

    Built fresh per request rather than once at startup, unlike the
    embedder: `openlaw.retrieval.search.search` needs the raw query TEXT
    (independently of its vector) to build the sparse/lexical side of hybrid
    search, but `QdrantGraphRetriever.retrieve` calls `search_fn` with only a
    vector -- that is the contract `tests/api/test_deps.py` fixes, matching
    the multilingual `Retriever` protocol's `retrieve(english_query)` shape.
    Currying `question` into a small closure here is cheap (no I/O, no model
    load) and is the only way to satisfy both constraints at once.
    """
    return lambda vector: search(
        client, settings.qdrant_url, question, vector, limit=RETRIEVAL_LIMIT
    )


def _expand_chunk_kind(label: str | None) -> ChunkKind:
    """Return the `ChunkKind` for a graph-expansion row's node label.

    `expand_chunks_cypher` projects `labels(related)[0]` as `label`, one of
    `"Section"`, `"Act"`, or `"Judgment"` (see `openlaw.graph.queries`'s
    module docstring). Only a `Judgment` row can only have been reached via
    `INTERPRETS` -- nothing else touches a Judgment node -- so mapping it to
    `ChunkKind.JUDGMENT` is what lets `openlaw.retrieval.rerank
    ._relationship_rank` actually rank INTERPRETS-derived hits ahead of the
    weaker REFERENCES-derived ones, instead of every expanded chunk landing
    in the same tier regardless of which relationship produced it.
    """
    return ChunkKind.JUDGMENT if label == "Judgment" else ChunkKind.PROVISION


def _resolve_expanded_chunks(rows: list[dict]) -> tuple[RetrievedChunk, ...]:
    """Turn `expand_chunks` rows into citable `RetrievedChunk`s.

    Rows missing an `id` or `source_url` are dropped -- everything merged
    into a retrieval context becomes citable, and a chunk with no real
    government source can never be shown to a user (see
    `openlaw.retrieval.context.RetrievalContext`'s module docstring).
    """
    return tuple(
        RetrievedChunk(
            chunk_id=str(row["id"]),
            text=str(row.get("label") or "related provision"),
            source_url=str(row["source_url"]),
            score=0.0,
            kind=_expand_chunk_kind(row.get("label")),
        )
        for row in rows
        if row.get("id") and row.get("source_url")
    )


def build_expand_fn(settings: Settings) -> ExpandFn:
    """Return the graph-expansion callable for `QdrantGraphRetriever`.

    Gated on `settings.graph_expansion_enabled` (default `False`) -- see
    `openlaw.retrieval.rerank`'s module docstring for the measured, current
    reasons expansion is unsafe as a request-path default (the statute-to-
    case-law bridge is too sparse, and `REFERENCES` fans out through shared-
    Act supernodes). `QdrantGraphRetriever.retrieve` always calls `expand_fn`
    unconditionally -- that is its tested contract -- so honouring the
    project-wide default here, by returning a no-op when disabled, is what
    keeps the documented "expansion is off by default" decision true for the
    one call site the multilingual pipeline actually uses. Opens a fresh
    Neo4j driver per call rather than a shared one (module docstring, "what
    the default actually costs" -- a known, accepted inefficiency, correct
    but wasteful, left for a future task once request volume justifies a
    long-lived driver).
    """
    if not settings.graph_expansion_enabled:
        return lambda chunk_ids: ()

    def expand(chunk_ids: list[str]) -> tuple[RetrievedChunk, ...]:
        if not chunk_ids:
            return ()
        with graph_driver(settings) as driver:
            rows = expand_chunks(driver, chunk_ids, limit=EXPANSION_LIMIT)
        return _resolve_expanded_chunks(rows)

    return expand


#: Where each provider's API key lives on `Settings`, and the environment
#: variable name to quote back at an operator who is missing it. OpenRouter's
#: is deliberately unprefixed -- see `Settings.openrouter_api_key`.
PROVIDER_KEYS: dict[LLMProvider, tuple[str, str]] = {
    LLMProvider.ANTHROPIC: ("anthropic_api_key", "OPENLAW_ANTHROPIC_API_KEY"),
    LLMProvider.OPENROUTER: ("openrouter_api_key", "OPENROUTER_API_KEY"),
}


def _provider_key(settings: Settings, provider: LLMProvider) -> str:
    """Return the configured API key for `provider`, or the empty string."""
    attribute, _ = PROVIDER_KEYS[provider]
    return getattr(settings, attribute) or ""


def _require_provider_keys(settings: Settings, registry: ModelRegistry) -> None:
    """Fail fast when a provider some role routes to has no API key.

    Required keys follow the *configuration*, not a fixed list. Provider
    selection is per-role (`openlaw.answers.routing`), so a deployment that
    puts every role on OpenRouter genuinely does not need an Anthropic key,
    and one that leaves the translator on Haiku genuinely needs both.
    Demanding a key for a provider an operator is not using would make a
    correct self-host look broken, which is the failure this walks the
    registry to avoid -- per-language translator overrides included, since
    an override is the one way a provider can be referenced by exactly one
    language and nothing else.

    An unroutable model string (a typo in the provider prefix) surfaces here
    too, as the same readable startup error rather than a 500 on some
    citizen's question hours later. Runs before `build_query_embedder`, so a
    configuration mistake never pays for a ~2.5s local model load.
    """
    try:
        providers = referenced_providers(registry)
    except ModelRouteError as error:
        raise RuntimeError(f"invalid model configuration: {error}") from error

    missing = sorted(
        PROVIDER_KEYS[provider][1]
        for provider in providers
        if not _provider_key(settings, provider)
    )
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} is required to answer questions: this "
            "deployment's configured models route at least one pipeline role "
            "(generation, translation, or crisis classification) to that "
            "provider. Set it in your environment or .env file before "
            "starting the service, or point every role at a provider you do "
            "have a key for -- see .env.example."
        )


def build_llm_adapter(settings: Settings, registry: ModelRegistry) -> LLMAdapter:
    """Return the adapter that serves every role, routing per model string.

    Both factories are registered but neither is *called* until some model
    actually routes to it, so an Anthropic-only deployment never constructs
    an OpenRouter client and vice versa. `_require_provider_keys` has
    already guaranteed that whichever factory does run has a key.
    """
    _require_provider_keys(settings, registry)
    return RoutingAdapter(
        {
            LLMProvider.ANTHROPIC: lambda: AnthropicAdapter(
                _provider_key(settings, LLMProvider.ANTHROPIC)
            ),
            LLMProvider.OPENROUTER: lambda: OpenRouterAdapter(
                _provider_key(settings, LLMProvider.OPENROUTER)
            ),
        }
    )


#: What `openlaw.api.routes.ask` calls, once per turn. The three positional
#: arguments are the caller's own words; the three keyword-only ones are what
#: the route loaded for this turn before generation. Precise on purpose (Task 5
#: review, H1/LOW): the loose `Callable[..., LocalisedAnswer]` this used to be
#: let `answer_fn` silently drop `language`, and the identical hazard now
#: applies three times over -- a closure that quietly ignored
#: `document_crisis_kinds` would make a woman's attached protection order
#: invisible with nothing failing anywhere.
class ConversationAnswerFn(Protocol):
    """One turn of a conversation, answered."""

    def __call__(
        self,
        question: str,
        language: str | None,
        state: str | None,
        *,
        history: tuple[HistoryTurn, ...] = (),
        document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
        document_crisis_kinds: tuple[str | None, ...] = (),
    ) -> LocalisedAnswer:
        """Answer `question` as turn `len(history)` of its conversation."""
        ...


#: Retained name for the single-turn shape, so nothing that only ever asked a
#: standalone question has to care that conversations exist.
AnswerFn = ConversationAnswerFn


def _build_fetch_fn(client: httpx.Client, settings: Settings) -> FetchFn:
    """Return the carried-chunk re-fetch callable for one deployment.

    Read-only and shared across requests (unlike `_build_search_fn`, which has
    to curry the question text): `fetch_chunks_by_id` needs nothing from the
    turn but the ids themselves.
    """
    return lambda chunk_ids: fetch_chunks_by_id(client, settings.qdrant_url, chunk_ids)


def build_conversation_answer_fn(settings: Settings) -> ConversationAnswerFn:
    """Return the production answer function used by the `/ask` endpoint.

    Everything expensive -- the provider client(s), the model registry, the
    glossary, the query embedder, the shared HTTP client -- is built exactly
    once, here, before this function returns. The closure it returns is called
    once per request and builds nothing heavier than a
    `QdrantGraphRetriever` and its per-request `search_fn` closure (see
    `_build_search_fn`). Calls `answer_in_conversation` only -- never
    `persist_answer_events` -- so `routes.ask` remains the single place a
    turn's events are written (see the module docstring).

    **This is what makes multi-turn reachable at all.** `answer_in_conversation`
    has existed since Plan 6 Task 9 and nothing in production called it: this
    module built a single-turn `answer_fn` that passed no history, no document
    context and no document crisis verdicts, so every one of those paths was
    dead code behind a live endpoint. The retriever built below is a
    `ConversationRetriever` -- it re-fetches `carried_chunk_ids` through
    `fetch_chunks_by_id` and merges them into this turn's context -- and the
    three keyword arguments are forwarded, not defaulted away.

    `client` (the shared `httpx.Client` used for every search and re-fetch) is
    deliberately never closed: it must live exactly as long as the closure
    that captures it, which is the lifetime of the process, so there is no
    earlier point at which closing it would be correct.
    """
    registry = ModelRegistry.from_settings(settings)
    adapter = build_llm_adapter(settings, registry)
    glossary = load_glossary()
    embedder = build_query_embedder(settings)
    expand_fn = build_expand_fn(settings)
    client = httpx.Client(timeout=60)
    fetch_fn = _build_fetch_fn(client, settings)

    def answer_fn(
        question: str,
        language: str | None,
        state: str | None,
        *,
        history: tuple[HistoryTurn, ...] = (),
        document_context: DocumentContext = EMPTY_DOCUMENT_CONTEXT,
        document_crisis_kinds: tuple[str | None, ...] = (),
    ) -> LocalisedAnswer:
        """Answer one turn, in `language` or the language it was asked in.

        Task 5 review, H1 (and its Plan 6 restatement): every argument this
        closure accepts is forwarded, by keyword, to `answer_in_conversation`.
        An earlier version accepted `language` and never passed it on, which
        silently discarded the only way a Marathi or Assamese speaker is served
        in their own language; `document_crisis_kinds` is the same hazard with
        higher stakes, since dropping it would make an attached FIR's crisis
        verdict vanish with nothing failing. `tests/api/test_deps.py`'s stub
        requires all of them rather than defaulting them away, so a future
        regression fails a test instead of a person.
        """
        retriever = QdrantGraphRetriever(
            embedder=embedder,
            search_fn=_build_search_fn(client, settings, question),
            expand_fn=expand_fn,
            fetch_fn=fetch_fn,
        )
        return answer_in_conversation(
            question,
            retriever,
            adapter,
            registry,
            glossary,
            history=history,
            document_context=document_context,
            document_crisis=resolve_document_crisis(document_crisis_kinds),
            state=state,
            language=language,
        )

    return answer_fn


#: Kept as an alias rather than a second implementation: `build_answer_fn` was
#: the name every caller and test used before conversations existed, and the
#: function it named is now a special case of this one (empty history, no
#: document) reached through exactly the same closure.
build_answer_fn = build_conversation_answer_fn


def _describe_one_error(error: dict) -> str:
    """Return a readable description of one pydantic error dict.

    `error["loc"]` is guarded (LOW, Task 5 review): pydantic's own typing
    allows `loc` to be empty for a model-level (rather than field-level)
    error, and indexing it unguarded would raise `IndexError` from inside
    this error-reporting path itself -- the one place that must not itself
    fail with an unreadable traceback. A missing field is named as its
    `OPENLAW_*` environment variable (matching every other setting's
    documented name); anything else (a malformed value pydantic could not
    coerce -- e.g. non-JSON `OPENLAW_TRUSTED_PROXY_IPS`) is named with its
    field and pydantic's own message, since there is no single canonical
    "what to set it to" for an arbitrary bad value the way there is for a
    simply-missing one.
    """
    loc = error["loc"]
    if not loc:
        return f"(configuration: {error['msg']})"
    if error["type"] == "missing":
        return f"OPENLAW_{str(loc[0]).upper()}"
    return f"{loc[0]} ({error['msg']})"


def _describe_validation_error(exc: ValidationError) -> str:
    """Return a human-readable summary of every field `exc` failed on."""
    return ", ".join(_describe_one_error(error) for error in exc.errors())


def _require_settings() -> Settings:
    """Return validated settings, or fail fast with a readable message.

    `get_settings()` can raise two different exceptions on bad configuration,
    neither of which is right to show an operator directly: a missing
    required field (`vaquill_root`, `kanoongpt_root`) raises
    `pydantic.ValidationError`, a multi-line, library-internal message that
    never says which `OPENLAW_*` environment variable to set; a malformed
    value for a JSON-typed field (e.g. `OPENLAW_TRUSTED_PROXY_IPS` not being
    valid JSON) instead raises `pydantic_settings.exceptions.SettingsError`
    (a `ValueError`, not a `ValidationError`) before pydantic ever gets to
    validate it. Both are re-raised here as one readable `RuntimeError`,
    stopping the service before it binds a port -- never a stack trace, and
    never a service that starts and only fails once a real user asks it a
    question.
    """
    try:
        return get_settings()
    except ValidationError as exc:
        raise RuntimeError(
            f"missing or invalid configuration: {_describe_validation_error(exc)}. "
            "Copy .env.example to .env and fill these in before starting the "
            "service."
        ) from exc
    except SettingsError as exc:
        raise RuntimeError(
            f"invalid configuration: {exc}. Check .env.example for the "
            "expected format of every OPENLAW_* setting."
        ) from exc


def _require_proxy_acknowledgement(settings: Settings) -> None:
    """Fail fast unless the reverse-proxy trust decision was made explicitly.

    Task 5 review, H3. `settings.trusted_proxy_ips` left at its empty
    default is genuinely ambiguous between two very different deployments:
    "no proxy, correctly trusting nothing" and "there IS a proxy and nobody
    configured it" -- and the second one fails with no crash and no 500,
    just the fourth visitor of the day getting a normal-looking 429 while
    every caller behind the proxy shares one rate-limit/anonymous-quota
    bucket (see `Settings.trusted_proxy_ips`'s docstring). Documentation
    alone does not close this: the value lives in a file the operator copies
    once and may never re-read, and the correct value is not even knowable
    until after the proxy is running. Requiring an explicit choice -- either
    a configured `trusted_proxy_ips`, or `OPENLAW_NO_REVERSE_PROXY=true`
    acknowledging there is none -- turns an easy-to-miss comment into a
    state the service refuses to start in silently, matching every other
    required-secret check in this module.
    """
    if settings.trusted_proxy_ips or settings.no_reverse_proxy:
        return
    raise RuntimeError(
        "reverse-proxy trust is not configured. If a reverse proxy "
        "(nginx/Caddy/Traefik, or the Docker Compose network) sits in front "
        "of this service, set OPENLAW_TRUSTED_PROXY_IPS to its address (see "
        ".env.example). If nothing does -- a bare, direct deployment -- set "
        "OPENLAW_NO_REVERSE_PROXY=true to confirm that explicitly. Leaving "
        "both unset would silently collapse per-IP rate limiting and the "
        "anonymous quota into one shared bucket for the whole deployment "
        "the moment a proxy is added without anyone updating this setting."
    )


def _require_database_reachable(engine: Engine) -> None:
    """Fail fast when the configured database cannot actually be reached.

    Task 5 review, M3. `engine_from_settings` builds a lazily-connecting
    SQLAlchemy engine -- the pool never opens a real connection until first
    use -- so a wrong host, wrong credentials, or a Postgres that was never
    started would otherwise surface for the first time as a generic 500 on
    a real user's first authenticated request (the exact "starts and only
    fails once a user asks a question" outcome this task's requirements
    forbid), not at startup alongside every other required setting. One
    cheap `SELECT 1` here converts that into the same fail-fast
    `RuntimeError` every other missing/invalid setting gets. The URL in the
    error message has its password redacted -- an error message must not
    leak a credential.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        safe_url = engine.url.render_as_string(hide_password=True)
        raise RuntimeError(
            f"cannot reach the configured database ({safe_url}): {exc}. "
            "Check OPENLAW_DATABASE_URL and that Postgres is running "
            "(`docker compose up -d postgres`)."
        ) from exc


def production_app() -> FastAPI:
    """Build the production application.

    The composition root: validates configuration (including the reverse-
    proxy trust acknowledgement and live database reachability -- see
    `_require_proxy_acknowledgement`/`_require_database_reachable`), builds
    `answer_fn` and the database session factory exactly once, and hands
    both to `create_app` (`openlaw.api.app`), which wires the HTTP surface
    around them without constructing or reloading either itself.
    """
    settings = _require_settings()
    _require_proxy_acknowledgement(settings)
    engine = engine_from_settings(settings)
    _require_database_reachable(engine)

    def session_factory() -> AbstractContextManager[Session]:
        return session_scope(engine)

    return create_app(
        build_conversation_answer_fn(settings), session_factory, settings
    )
