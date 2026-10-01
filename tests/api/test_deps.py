"""Tests for the production composition root.

`QdrantGraphRetriever` is exercised with injected `search_fn`/`expand_fn`, per
the task brief -- no live Qdrant or Neo4j required. `build_answer_fn` and
`production_app` are exercised only up to the point where they would need a
real Anthropic key or the local `voyage-4-nano` model: those branches are
covered by fast, fully-mocked paths so this suite never loads the local
embedding model or makes a network call, matching every other test in this
project (see `tests/api/test_routes.py`'s module docstring).
"""

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import create_engine

from openlaw.answers.models import Completion, ModelRegistry
from openlaw.api.deps import (
    QdrantGraphRetriever,
    build_llm_adapter,
    _describe_one_error,
    _require_database_reachable,
    _require_proxy_acknowledgement,
    build_answer_fn,
    build_conversation_answer_fn,
    build_expand_fn,
    production_app,
)
from openlaw.config import Settings, get_settings
from openlaw.retrieval.context import ChunkKind, RetrievedChunk


class _Embedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 1024


def _chunk(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://x.gov.in/{chunk_id}",
        score=score,
        kind=ChunkKind.PROVISION,
    )


def test_retriever_returns_a_context_for_the_query() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (),
    )
    context = retriever.retrieve("my landlord kept my deposit")

    assert context.query == "my landlord kept my deposit"
    assert context.chunk_ids == frozenset({"a"})


def test_retriever_merges_graph_expansion_into_the_context() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (_chunk("b", 0.4),),
    )
    assert retriever.retrieve("q").chunk_ids == frozenset({"a", "b"})


def test_graph_expansion_never_displaces_a_directly_retrieved_chunk() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=lambda chunk_ids: (_chunk("a", 0.1),),
    )
    context = retriever.retrieve("q")

    assert len(context.chunks) == 1
    assert context.chunks[0].score == 0.9


def test_expansion_is_asked_only_about_retrieved_chunks() -> None:
    seen: list[list[str]] = []

    def expand(chunk_ids: list[str]) -> tuple[RetrievedChunk, ...]:
        seen.append(list(chunk_ids))
        return ()

    QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (_chunk("a", 0.9),),
        expand_fn=expand,
    ).retrieve("q")

    assert seen == [["a"]]


def test_empty_search_yields_an_empty_context() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (),
        expand_fn=lambda chunk_ids: (),
    )
    assert retriever.retrieve("q").chunks == ()


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/v",
        "kanoongpt_root": "/tmp/k",
    }
    return Settings(**{**defaults, **overrides})


def test_build_expand_fn_is_a_noop_when_graph_expansion_is_disabled() -> None:
    """Binding decision (Plan 3 T3, measured): expansion stays off the
    request path by default. `QdrantGraphRetriever` always calls `expand_fn`
    unconditionally, so honouring the default has to happen here."""
    expand = build_expand_fn(_settings(graph_expansion_enabled=False))

    assert expand(["a", "b"]) == ()


def test_build_expand_fn_returns_empty_for_no_chunk_ids_without_a_driver(
    monkeypatch,
) -> None:
    """Expansion enabled, but nothing was retrieved -- must not touch Neo4j
    at all for an empty `chunk_ids` (see `openlaw.graph.queries.expand_chunks`'s
    own no-op-on-empty-input contract, exercised here at the wiring layer)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not open a driver for an empty expansion")

    monkeypatch.setattr("openlaw.api.deps.graph_driver", _boom)
    expand = build_expand_fn(_settings(graph_expansion_enabled=True))

    assert expand([]) == ()


def test_build_expand_fn_resolves_rows_into_citable_chunks(monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def _fake_driver(settings: Settings):
        yield object()

    def _fake_expand_chunks(
        driver: object, chunk_ids: list[str], limit: int
    ) -> list[dict]:
        assert chunk_ids == ["a"]
        assert limit == 20
        return [
            {"id": "j1", "label": "Judgment", "source_url": "https://x.gov.in/j1"},
            {"id": "s1", "label": "Section", "source_url": "https://x.gov.in/s1"},
            # Dropped: no source_url.
            {"id": "s2", "label": "Section", "source_url": None},
        ]

    monkeypatch.setattr("openlaw.api.deps.graph_driver", _fake_driver)
    monkeypatch.setattr("openlaw.api.deps.expand_chunks", _fake_expand_chunks)

    expand = build_expand_fn(_settings(graph_expansion_enabled=True))
    resolved = expand(["a"])

    assert {chunk.chunk_id for chunk in resolved} == {"j1", "s1"}
    by_id = {chunk.chunk_id: chunk for chunk in resolved}
    assert by_id["j1"].kind == ChunkKind.JUDGMENT
    assert by_id["s1"].kind == ChunkKind.PROVISION


# --- Task 5 review, H3: the reverse-proxy trust decision must be explicit -


def test_require_proxy_acknowledgement_rejects_the_ambiguous_default() -> None:
    """Neither `trusted_proxy_ips` nor `no_reverse_proxy` set -- the exact
    state that silently collapses per-IP rate limiting and the anonymous
    quota the moment a proxy is added without anyone updating the setting."""
    with pytest.raises(RuntimeError, match="OPENLAW_NO_REVERSE_PROXY"):
        _require_proxy_acknowledgement(
            _settings(trusted_proxy_ips=(), no_reverse_proxy=False)
        )


def test_require_proxy_acknowledgement_accepts_a_configured_proxy() -> None:
    _require_proxy_acknowledgement(
        _settings(trusted_proxy_ips=("172.18.0.1",), no_reverse_proxy=False)
    )


def test_require_proxy_acknowledgement_accepts_an_explicit_no_proxy() -> None:
    _require_proxy_acknowledgement(
        _settings(trusted_proxy_ips=(), no_reverse_proxy=True)
    )


# --- Task 5 review, M3: a bad database URL must fail at startup, not on the
# --- first authenticated request ------------------------------------------


def test_require_database_reachable_passes_for_a_reachable_database() -> None:
    engine = create_engine("sqlite://")
    _require_database_reachable(engine)  # must not raise


def test_require_database_reachable_fails_fast_with_no_credential_leak() -> None:
    """Connection refused (nothing listens on this port) -- must fail with a
    readable message, and that message must not include the password from
    the connection URL."""
    engine = create_engine(
        "postgresql+psycopg://openlaw:supersecret@localhost:1/nonexistent"
    )
    with pytest.raises(RuntimeError, match="cannot reach the configured database") as excinfo:
        _require_database_reachable(engine)
    assert "supersecret" not in str(excinfo.value)


def test_build_answer_fn_requires_an_anthropic_key() -> None:
    """Fails fast, before the (expensive) query embedder is ever built --
    see the startup-validation requirement in the task's binding
    requirements."""
    with pytest.raises(RuntimeError, match="OPENLAW_ANTHROPIC_API_KEY"):
        build_answer_fn(_settings(anthropic_api_key=None))


def test_build_answer_fn_never_loads_a_real_embedder_before_the_key_check(
    monkeypatch,
) -> None:
    """The Anthropic-key check must run before `build_query_embedder` --
    otherwise a missing key would only surface after a ~2.5s local model
    load (or a Voyage API construction). Proven here by patching the
    embedder factory to explode if it is ever reached."""

    def _boom(settings: Settings) -> None:
        raise AssertionError("embedder must not be built before the key check")

    monkeypatch.setattr("openlaw.api.deps.build_query_embedder", _boom)

    with pytest.raises(RuntimeError, match="OPENLAW_ANTHROPIC_API_KEY"):
        build_answer_fn(_settings(anthropic_api_key=None))


def test_production_app_fails_fast_with_a_readable_message_when_config_is_missing(
    monkeypatch,
) -> None:
    """`Settings.vaquill_root`/`kanoongpt_root` have no defaults -- a bare
    `pydantic.ValidationError` would be a stack trace naming pydantic
    internals, not an environment variable an operator can act on."""
    for name in ("OPENLAW_VAQUILL_ROOT", "OPENLAW_KANOONGPT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="OPENLAW_VAQUILL_ROOT"):
            production_app()
    finally:
        get_settings.cache_clear()


def test_production_app_fails_fast_with_a_readable_message_for_malformed_trusted_proxy_ips(
    monkeypatch,
) -> None:
    """A genuinely malformed (non-blank) `OPENLAW_TRUSTED_PROXY_IPS` value
    must still fail loudly and readably -- only the blank-value case (Task 5
    review, C1) is special-cased to the safe empty tuple. Raises
    `pydantic.ValidationError` (caught via `NoDecode` + the field validator,
    see `openlaw.config`), not `SettingsError`."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_TRUSTED_PROXY_IPS", "not-json")
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="trusted_proxy_ips"):
            production_app()
    finally:
        monkeypatch.delenv("OPENLAW_TRUSTED_PROXY_IPS", raising=False)
        get_settings.cache_clear()


def test_production_app_fails_fast_with_a_readable_message_for_malformed_json_config(
    monkeypatch,
) -> None:
    """A field that still uses pydantic-settings' own JSON decoding (unlike
    `trusted_proxy_ips`, which now decodes itself -- see the test above)
    raises `pydantic_settings.exceptions.SettingsError`, not
    `pydantic.ValidationError` -- a different exception type that must be
    caught separately or it reaches the operator as a raw stack trace."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_MODEL_TRANSLATOR_OVERRIDES", "not-json")
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="invalid configuration"):
            production_app()
    finally:
        monkeypatch.delenv("OPENLAW_MODEL_TRANSLATOR_OVERRIDES", raising=False)
        get_settings.cache_clear()


def test_production_app_refuses_to_start_with_no_proxy_decision_made(
    monkeypatch,
) -> None:
    """End-to-end: even with every other setting present and valid,
    `production_app()` still refuses to start while the reverse-proxy trust
    question is unanswered (Task 5 review, H3) -- and does so before the
    Anthropic key check or anything expensive, since it is unreachable via
    `.env.example`'s own (unconfigured) values otherwise."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENLAW_DATABASE_URL", "sqlite://")
    monkeypatch.delenv("OPENLAW_TRUSTED_PROXY_IPS", raising=False)
    monkeypatch.delenv("OPENLAW_NO_REVERSE_PROXY", raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="reverse-proxy trust is not configured"):
            production_app()
    finally:
        get_settings.cache_clear()


def test_build_search_fn_forwards_the_question_and_vector_to_search(
    monkeypatch,
) -> None:
    """The per-request `search_fn` closure -- see its docstring for why it
    cannot be built once alongside the embedder -- must actually forward the
    curried question, the vector `QdrantGraphRetriever` hands it, and
    `RETRIEVAL_LIMIT` to `openlaw.retrieval.search.search`."""
    import openlaw.api.deps as deps_module

    seen: dict = {}

    def _fake_search(client, qdrant_url, text, vector, limit):
        seen.update(client=client, qdrant_url=qdrant_url, text=text, vector=vector, limit=limit)
        return ("sentinel",)

    monkeypatch.setattr(deps_module, "search", _fake_search)

    search_fn = deps_module._build_search_fn(
        "fake-client", _settings(qdrant_url="http://q:6333"), "my landlord kept my deposit"
    )
    result = search_fn([0.1, 0.2])

    assert result == ("sentinel",)
    assert seen == {
        "client": "fake-client",
        "qdrant_url": "http://q:6333",
        "text": "my landlord kept my deposit",
        "vector": [0.1, 0.2],
        "limit": deps_module.RETRIEVAL_LIMIT,
    }


def test_production_app_builds_a_working_app_without_touching_qdrant_or_neo4j(
    monkeypatch,
) -> None:
    """Exercises the full `production_app()` composition root -- Anthropic
    adapter, model registry, glossary, query embedder, database engine, and
    `create_app` -- using the Voyage API embedder backend (construction only
    stores an API client; no network call, no local model load) so this test
    never touches the ~2.5s local `voyage-4-nano` model or a live datastore.
    Also exercises `answer_fn` and `session_factory` themselves (not just
    their existence), with `answer_in_conversation` stubbed out so no real
    Anthropic/Qdrant/Neo4j call is ever made.
    """
    import openlaw.api.deps as deps_module

    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENLAW_QUERY_EMBEDDER_BACKEND", "voyage_api")
    monkeypatch.setenv("OPENLAW_VOYAGE_API_KEY", "voyage-test")
    monkeypatch.setenv("OPENLAW_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("OPENLAW_NO_REVERSE_PROXY", "true")
    get_settings.cache_clear()

    seen: dict = {}

    # EVERY keyword deliberately has NO default (Task 5 review, H1, extended
    # for Plan 6 Task 15): the earlier stub's `state=None` default let
    # `answer_fn` silently drop `language` without any test ever noticing, and
    # `history`/`document_context`/`document_crisis` are now three more
    # arguments that could be dropped the same way -- the last of them
    # silently hiding an attached FIR's crisis verdict. A stub that requires
    # all of them turns a dropped one back into an immediate `TypeError`.
    def _fake_answer_in_conversation(
        question, retriever, adapter, registry, glossary, *,
        history, document_context, document_crisis, state, language,
    ):
        seen.update(
            question=question, retriever=retriever, state=state, language=language,
            history=history, document_context=document_context,
            document_crisis=document_crisis,
        )
        return "sentinel-answer"

    monkeypatch.setattr(
        deps_module, "answer_in_conversation", _fake_answer_in_conversation
    )

    try:
        app = production_app()
        assert callable(app.state.answer_fn)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

        result = app.state.answer_fn("my landlord kept my deposit", "mr", "KA")
        assert result == "sentinel-answer"
        assert seen["question"] == "my landlord kept my deposit"
        assert seen["state"] == "KA"
        assert seen["language"] == "mr"
        assert isinstance(seen["retriever"], deps_module.QdrantGraphRetriever)
        assert seen["history"] == ()
        assert seen["document_crisis"] is None

        with app.state.session_factory() as session:
            assert session.is_active
    finally:
        get_settings.cache_clear()


def test_describe_one_error_handles_a_model_level_error_with_no_loc() -> None:
    """LOW, Task 5 review: pydantic's own typing allows `loc` to be empty
    for a model-level error -- indexing `loc[0]` unguarded would raise
    `IndexError` from inside this error-reporting path itself."""
    described = _describe_one_error({"loc": (), "msg": "something went wrong", "type": "value_error"})

    assert described == "(configuration: something went wrong)"


# --- provider keys are required by actual use, not by a fixed list --------


def test_an_all_openrouter_deployment_does_not_need_an_anthropic_key() -> None:
    """The failure this guards against is a correct self-host that looks
    broken: an operator who has deliberately moved every role to OpenRouter
    must not be told to set an Anthropic key they will never use."""
    openrouter_roles = {
        "model_generator": "openrouter:z-ai/glm-5.3-flash",
        "model_translator": "openrouter:z-ai/glm-5.3-flash",
        "model_classifier": "openrouter:z-ai/glm-5.3-flash",
    }
    settings = _settings(
        anthropic_api_key=None, openrouter_api_key="sk-or", **openrouter_roles
    )

    build_llm_adapter(settings, ModelRegistry.from_settings(settings))  # must not raise


def test_an_all_openrouter_deployment_still_demands_the_openrouter_key() -> None:
    settings = _settings(
        anthropic_api_key=None,
        openrouter_api_key=None,
        model_generator="openrouter:z-ai/glm-5.3-flash",
        model_translator="openrouter:z-ai/glm-5.3-flash",
        model_classifier="openrouter:z-ai/glm-5.3-flash",
    )

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_llm_adapter(settings, ModelRegistry.from_settings(settings))


def test_an_anthropic_only_deployment_does_not_need_an_openrouter_key() -> None:
    settings = _settings(anthropic_api_key="sk-ant", openrouter_api_key=None)

    build_llm_adapter(settings, ModelRegistry.from_settings(settings))  # must not raise


def test_a_mixed_deployment_demands_both_keys_and_names_the_missing_one() -> None:
    settings = _settings(
        anthropic_api_key="sk-ant",
        openrouter_api_key=None,
        model_generator="openrouter:z-ai/glm-5.3-flash",
    )

    with pytest.raises(RuntimeError) as excinfo:
        build_llm_adapter(settings, ModelRegistry.from_settings(settings))

    assert "OPENROUTER_API_KEY" in str(excinfo.value)
    assert "OPENLAW_ANTHROPIC_API_KEY" not in str(excinfo.value)


def test_a_provider_referenced_only_by_a_language_override_still_needs_its_key() -> None:
    """A per-language translator override is the one way a provider can be
    referenced by exactly one language and nothing else -- missing it would
    leave, say, every Marathi question failing while everything else works."""
    settings = _settings(
        anthropic_api_key="sk-ant",
        openrouter_api_key=None,
        model_translator_overrides={"mr": "openrouter:z-ai/glm-5.3-flash"},
    )

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_llm_adapter(settings, ModelRegistry.from_settings(settings))


def test_an_unknown_provider_prefix_fails_at_startup_naming_role_and_value() -> None:
    """A typo must be a readable startup refusal, not a 500 on a question."""
    settings = _settings(anthropic_api_key="sk-ant", model_generator="gpt5:some-model")

    with pytest.raises(RuntimeError) as excinfo:
        build_llm_adapter(settings, ModelRegistry.from_settings(settings))

    message = str(excinfo.value)
    assert "OPENLAW_MODEL_GENERATOR" in message
    assert "gpt5:some-model" in message


def test_build_answer_fn_requires_the_openrouter_key_before_the_embedder(
    monkeypatch,
) -> None:
    """Same fail-before-anything-expensive guarantee the Anthropic path has:
    a missing key must not cost a ~2.5s local model load first."""

    def _boom(settings: Settings) -> None:
        raise AssertionError("embedder must not be built before the key check")

    monkeypatch.setattr("openlaw.api.deps.build_query_embedder", _boom)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_answer_fn(
            _settings(
                anthropic_api_key=None,
                openrouter_api_key=None,
                model_generator="openrouter:z-ai/glm-5.3-flash",
                model_translator="openrouter:z-ai/glm-5.3-flash",
                model_classifier="openrouter:z-ai/glm-5.3-flash",
            )
        )


def test_no_provider_client_is_constructed_for_an_unused_provider(monkeypatch) -> None:
    """Laziness is the mechanism behind "must not construct that client":
    proven by making the OpenRouter constructor explode if it is ever
    reached, then routing a real call through the Anthropic side."""
    built: list[str] = []

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unused provider client must never be built")

    class _FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            built.append("anthropic")

        def complete(self, model, system, messages, output_schema, max_tokens):
            return Completion(text=model)

    monkeypatch.setattr("openlaw.api.deps.OpenRouterAdapter", _boom)
    monkeypatch.setattr("openlaw.api.deps.AnthropicAdapter", _FakeAnthropic)
    settings = _settings(anthropic_api_key="sk-ant")

    adapter = build_llm_adapter(settings, ModelRegistry.from_settings(settings))

    assert built == []  # nothing is built until a call actually routes
    assert adapter.complete("claude-haiku-4-5", "sys", [], None, 8).text == (
        "claude-haiku-4-5"
    )
    assert built == ["anthropic"]


# --- multi-turn is reachable in production (Plan 6 Task 15) ---------------


def test_the_retriever_refetches_carried_chunk_ids_rather_than_replaying_them() -> None:
    """The `ConversationRetriever` contract. A carried chunk must be fetched
    from the corpus again this turn, so one that has since left the corpus
    stops being citable -- see `openlaw.retrieval.fetch`."""
    fetched: list[tuple[str, ...]] = []

    def fetch_fn(chunk_ids: tuple[str, ...]):
        fetched.append(chunk_ids)
        return (
            RetrievedChunk(
                chunk_id="carried-1",
                text="carried provision",
                source_url="https://www.indiacode.nic.in/handle/1",
                score=0.0,
                kind=ChunkKind.PROVISION,
            ),
        )

    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (),
        expand_fn=lambda chunk_ids: (),
        fetch_fn=fetch_fn,
    )

    context = retriever.retrieve("deposit refund", ("carried-1",))

    assert fetched == [("carried-1",)]
    assert [chunk.chunk_id for chunk in context.chunks] == ["carried-1"]


def test_a_retriever_with_no_fetcher_refuses_to_silently_drop_carried_ids() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (),
        expand_fn=lambda chunk_ids: (),
    )

    with pytest.raises(ValueError, match="carried chunk ids"):
        retriever.retrieve("deposit refund", ("carried-1",))


def test_a_single_turn_retrieve_still_works_with_no_carried_ids() -> None:
    retriever = QdrantGraphRetriever(
        embedder=_Embedder(),
        search_fn=lambda vector: (),
        expand_fn=lambda chunk_ids: (),
    )

    assert retriever.retrieve("deposit refund").chunks == ()


def test_build_answer_fn_is_the_conversation_builder() -> None:
    """`build_answer_fn` must not be a second implementation that could drift
    from the conversation-aware one."""
    assert build_answer_fn is build_conversation_answer_fn
