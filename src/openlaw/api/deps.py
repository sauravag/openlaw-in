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

**Required secrets are validated before anything expensive is built.**
`production_app()` fails fast with a single, human-readable message naming
every missing setting -- never a raw stack trace and never a service that
starts and only fails once a real user asks a question. See
`_require_settings` and `_require_anthropic_key`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

import httpx
from fastapi import FastAPI
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.models import ModelRegistry
from openlaw.api.app import create_app
from openlaw.config import Settings, get_settings
from openlaw.db.session import engine_from_settings, session_scope
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks
from openlaw.language.glossary import load_glossary
from openlaw.language.pipeline import LocalisedAnswer, answer_in_language
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk
from openlaw.retrieval.factory import build_query_embedder
from openlaw.retrieval.query import QueryEmbedder
from openlaw.retrieval.rerank import ScoreReranker, merge_chunks
from openlaw.retrieval.search import search

RETRIEVAL_LIMIT = 10
EXPANSION_LIMIT = 20

#: A resolved graph-expansion result: bare (id, label, source_url) rows from
#: `openlaw.graph.queries.expand_chunks`, turned into citable `RetrievedChunk`s.
ExpandFn = Callable[[list[str]], tuple[RetrievedChunk, ...]]
#: A vector search over the configured corpus collections for one query.
SearchFn = Callable[[list[float]], tuple[RetrievedChunk, ...]]


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
    ) -> None:
        self._embedder = embedder
        self._search = search_fn
        self._expand = expand_fn
        self._reranker = ScoreReranker()

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        vector = self._embedder.embed_query(english_query)
        primary = self._search(vector)
        expanded = self._expand([chunk.chunk_id for chunk in primary])
        merged = merge_chunks(primary, expanded)
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


def _require_anthropic_key(settings: Settings) -> None:
    """Fail fast, with a clear message, when the Anthropic key is missing.

    Every pipeline role (generator, translator, classifier -- see
    `openlaw.answers.models.ModelRegistry`) goes through `AnthropicAdapter`,
    so this key is required unconditionally, unlike the Voyage key: the
    default query embedder is local `voyage-4-nano`
    (`openlaw.retrieval.factory.build_query_embedder`), and that path needs
    no Voyage key at all. Checked before `AnthropicAdapter` or the embedder
    is constructed, so a missing key never pays for either.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "OPENLAW_ANTHROPIC_API_KEY is required to answer questions -- "
            "generation, translation, and crisis classification all go "
            "through it. Set it in your environment or .env file before "
            "starting the service."
        )


#: The exact shape the task brief specifies and `routes.ask` calls against
#: (`answer_fn(body.question, body.language, body.state)`). Precise on
#: purpose (Task 5 review, H1/LOW): the loose `Callable[..., LocalisedAnswer]`
#: this used to be typed as let `answer_fn` silently drop `language` without
#: any type checker ever flagging it -- see `answer_fn`'s docstring below.
AnswerFn = Callable[[str, str | None, str | None], LocalisedAnswer]


def build_answer_fn(settings: Settings) -> AnswerFn:
    """Return the production answer function used by the `/ask` endpoint.

    Everything expensive -- the Anthropic client, the model registry, the
    glossary, the query embedder, the shared HTTP client -- is built exactly
    once, here, before this function returns. The closure it returns is
    called once per request and builds nothing heavier than a
    `QdrantGraphRetriever` and its per-request `search_fn` closure (see
    `_build_search_fn`). Calls `answer_in_language` only -- never
    `persist_answer_events` -- so `routes.ask` remains the single place a
    turn's events are written (see the module docstring).

    `client` (the shared `httpx.Client` used for every search request) is
    deliberately never closed: it must live exactly as long as the closure
    that captures it, which is the lifetime of the process, so there is no
    earlier point at which closing it would be correct.
    """
    _require_anthropic_key(settings)

    adapter = AnthropicAdapter(settings.anthropic_api_key)
    registry = ModelRegistry.from_settings(settings)
    glossary = load_glossary()
    embedder = build_query_embedder(settings)
    expand_fn = build_expand_fn(settings)
    client = httpx.Client(timeout=60)

    def answer_fn(
        question: str, language: str | None, state: str | None
    ) -> LocalisedAnswer:
        """Answer `question` in `language`, or the language it was asked in.

        Task 5 review, H1: an earlier version of this closure accepted
        `language` and never forwarded it to `answer_in_language`, so an
        explicit language selection from `AskRequest.language` -- which
        `answer_in_language`'s own docstring documents as overriding
        detection outright, and which is the only way a Marathi or Assamese
        speaker (script-indistinguishable from Hindi/Bengali by detection)
        gets served in their own language -- was silently discarded in
        production. `language=language` below is the fix; see
        `tests/api/test_deps.py` for the regression test, whose stub now
        requires this keyword rather than defaulting it away.
        """
        retriever = QdrantGraphRetriever(
            embedder=embedder,
            search_fn=_build_search_fn(client, settings, question),
            expand_fn=expand_fn,
        )
        return answer_in_language(
            question, retriever, adapter, registry, glossary,
            state=state, language=language,
        )

    return answer_fn


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

    return create_app(build_answer_fn(settings), session_factory, settings)
