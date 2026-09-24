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

import httpx
from fastapi import FastAPI
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

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


def build_answer_fn(settings: Settings) -> Callable[..., LocalisedAnswer]:
    """Return the production answer function used by the `/ask` endpoint.

    Everything expensive -- the Anthropic client, the model registry, the
    glossary, the query embedder, the shared HTTP client -- is built exactly
    once, here, before this function returns. The closure it returns is
    called once per request and builds nothing heavier than a
    `QdrantGraphRetriever` and its per-request `search_fn` closure (see
    `_build_search_fn`). Calls `answer_in_language` only -- never
    `persist_answer_events` -- so `routes.ask` remains the single place a
    turn's events are written (see the module docstring).
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
        """Answer `question` in the language it was asked in."""
        retriever = QdrantGraphRetriever(
            embedder=embedder,
            search_fn=_build_search_fn(client, settings, question),
            expand_fn=expand_fn,
        )
        return answer_in_language(
            question, retriever, adapter, registry, glossary, state=state
        )

    return answer_fn


def _describe_validation_error(exc: ValidationError) -> str:
    """Return a human-readable summary of every field `exc` failed on.

    Missing fields are named as their `OPENLAW_*` environment variable
    (matching every other setting's documented name); anything else (a
    malformed value pydantic could not coerce -- e.g. non-JSON
    `OPENLAW_TRUSTED_PROXY_IPS`) is named with its field and pydantic's own
    message, since there is no single canonical "what to set it to" for an
    arbitrary bad value the way there is for a simply-missing one.
    """
    parts = [
        f"OPENLAW_{str(error['loc'][0]).upper()}"
        if error["type"] == "missing"
        else f"{error['loc'][0]} ({error['msg']})"
        for error in exc.errors()
    ]
    return ", ".join(parts)


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


def production_app() -> FastAPI:
    """Build the production application.

    The composition root: validates configuration, builds `answer_fn` and the
    database session factory exactly once, and hands both to `create_app`
    (`openlaw.api.app`), which wires the HTTP surface around them without
    constructing or reloading either itself.
    """
    settings = _require_settings()
    engine = engine_from_settings(settings)

    def session_factory():
        return session_scope(engine)

    return create_app(build_answer_fn(settings), session_factory, settings)
