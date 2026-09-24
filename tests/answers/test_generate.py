"""Tests for the answer pipeline.

Binding corrections over the original task-8 brief (see task-8-report.md):
  * Crisis routing runs FIRST -- a crisis question never reaches `adapter`,
    so no generation call is wasted (and none of its output is discarded).
  * `generate_answer` bounds the context it renders into the generator's
    prompt to `MAX_CONTEXT_CHUNKS`, and validates against that same bounded
    set.
  * `build_retrieval_context`/`answer_question` are new orchestration entry
    points wiring `openlaw.retrieval.factory.build_query_embedder`,
    `openlaw.retrieval.search.search` (called with the raw question text),
    and gated graph expansion into one path -- none of that wiring existed
    anywhere in the codebase before this task, and `Settings.
    graph_expansion_enabled` was read nowhere at all.

Task 8b adds a second crisis-detection layer (see
`tests/answers/test_crisis_classifier.py` for that layer's own tests) and
fixes a task-8 regression: crisis routing now delivers the category-specific
helpline (`crisis_response`), not only the generic NALSA footer. Because
crisis routing now asks the CLASSIFIER-role model whenever the regex is
silent, every non-crisis `generate_answer`/`answer_question` call in this
file queues one extra `_SAFE_CLASSIFIER_RESPONSE` ahead of the generator's
response -- see `_classifier_response`.
"""

import json
from typing import Any

import pytest

from openlaw.answers.escalation import CrisisKind
from openlaw.answers.generate import (
    MAX_CONTEXT_CHUNKS,
    answer_question,
    build_retrieval_context,
    generate_answer,
)
from openlaw.answers.models import Completion, ModelRegistry, ModelRole, StubAdapter
from openlaw.config import Settings
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk
from openlaw.retrieval.query import StubQueryEmbedder

_QDRANT_URL = "http://127.0.0.1:6333"


def _classifier_response(kind: CrisisKind | None = None) -> str:
    """The classifier's JSON reply -- "none" unless `kind` is given."""
    return json.dumps({"crisis": kind.value if kind is not None else "none"})


#: Queued ahead of a generator response in every test below whose question
#: does not match the regex: the classifier is asked once before generation.
_SAFE_CLASSIFIER_RESPONSE = _classifier_response()


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="A landlord shall refund the deposit.",
        source_url=f"https://www.indiacode.nic.in/{chunk_id}",
        score=0.9,
        kind=ChunkKind.PROVISION,
    )


def _context(*chunk_ids: str) -> RetrievalContext:
    return RetrievalContext(
        query="my landlord kept my deposit",
        chunks=tuple(_chunk(cid) for cid in chunk_ids),
    )


def _response(chunk_ids: list[str], sufficient: bool = True) -> str:
    return json.dumps(
        {
            "summary": "The deposit must be refunded.",
            "claims": [{"text": "A refund is required.", "chunk_ids": chunk_ids}],
            "sufficient": sufficient,
        }
    )


def _settings(**overrides: Any) -> Settings:
    return Settings(vaquill_root="/tmp/v", kanoongpt_root="/tmp/k", **overrides)


class _FakeResponse:
    def __init__(self, points: list[dict[str, Any]]) -> None:
        self._points = points

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"result": {"points": self._points}}


class _FakeSearchClient:
    """Records every POST it receives and answers with canned per-collection
    hits, mirroring `tests/retrieval/test_search.py`'s `_FakeClient`."""

    def __init__(self, points_by_collection: dict[str, list[dict[str, Any]]]) -> None:
        self._points_by_collection = points_by_collection
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.requests.append((url, json))
        for collection, points in self._points_by_collection.items():
            if collection in url:
                return _FakeResponse(points)
        return _FakeResponse([])


def _search_hit(chunk_id: str) -> dict[str, Any]:
    return {
        "score": 0.9,
        "payload": {
            "chunk_id": chunk_id,
            "text": "A landlord shall refund the deposit.",
            "source_url": f"https://www.indiacode.nic.in/{chunk_id}",
        },
    }


def _fake_search_client(chunk_id: str = "a1") -> _FakeSearchClient:
    return _FakeSearchClient(
        {"acts_india_v1": [_search_hit(chunk_id)], "sc_judgments_v1": []}
    )


class _RaisingClient:
    """Fails loudly if touched -- proves a code path never reaches search."""

    def post(self, url: str, json: dict[str, Any], timeout: float) -> Any:
        raise AssertionError("search_client was touched unexpectedly")


class _ExhaustedAdapter:
    """Fails loudly if `.complete` is ever called."""

    def complete(self, **kwargs: Any) -> Any:
        raise AssertionError("adapter.complete was called unexpectedly")


class _ClassifierFailsThenGeneratorSucceedsAdapter:
    """Simulates a classifier outage (network error/timeout/expired key):
    the CLASSIFIER-role call always raises, but the GENERATOR-role call
    succeeds normally with `generator_response` -- used to prove a
    classifier failure does not stop generation, per the revised ruling in
    `crisis_classifier.py`'s module docstring."""

    def __init__(self, generator_response: str) -> None:
        self._generator_response = generator_response
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> Completion:
        self.calls.append(kwargs)
        if kwargs["model"] == ModelRegistry().for_role(ModelRole.CLASSIFIER):
            raise RuntimeError("simulated classifier outage")
        return Completion(text=self._generator_response)


# --- Core generate/validate/escalate behaviour (task-8 brief, Step 1) ------


def test_valid_answer_survives_validation() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert result.answer.is_answerable is True
    assert len(result.answer.claims) == 1


def test_fabricated_citation_is_dropped_and_logged() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["ghost"])])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert result.answer.claims == ()
    assert result.answer.is_answerable is False
    assert result.events[0].dropped_chunk_ids == ("ghost",)


def test_generation_uses_the_configured_generator_model() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    registry = ModelRegistry({ModelRole.GENERATOR: "claude-sonnet-5"})
    generate_answer("q", _context("a"), adapter, registry)

    assert adapter.calls[-1]["model"] == "claude-sonnet-5"


def test_generation_passes_the_structured_output_schema() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    generate_answer("q", _context("a"), adapter, ModelRegistry())

    schema = adapter.calls[-1]["output_schema"]
    assert schema is not None
    assert "claims" in schema["properties"]


def test_crisis_question_is_detected_and_reported() -> None:
    adapter = StubAdapter([_response(["a"])])
    result = generate_answer(
        "my husband beats me", _context("a"), adapter, ModelRegistry()
    )
    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE


def test_ordinary_question_reports_no_crisis() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    assert generate_answer("q", _context("a"), adapter, ModelRegistry()).crisis is None


def test_every_answer_carries_the_escalation_footer() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())
    assert "15100" in result.footer


def test_footer_names_the_state_authority_when_supplied() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    result = generate_answer(
        "q", _context("a"), adapter, ModelRegistry(), state="Kerala"
    )
    assert "Kerala" in result.footer


def test_model_reported_insufficiency_is_not_answerable() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"], sufficient=False)])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())
    assert result.answer.is_answerable is False


def test_malformed_model_json_yields_an_unanswerable_result() -> None:
    """A broken generator must never produce an unvalidated answer."""
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, "this is not json"])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert result.answer.is_answerable is False
    assert result.answer.claims == ()


def test_empty_generation_yields_an_unanswerable_result() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, ""])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())
    assert result.answer.is_answerable is False


def test_generation_refusal_yields_an_unanswerable_result() -> None:
    """Exercises the `stop_reason == "refusal"` branch, which an all-`end_turn`
    `StubAdapter` can never reach on its own."""

    class _ClassifierThenRefusingAdapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def complete(self, **kwargs: Any) -> Completion:
            self.calls.append(kwargs)
            if kwargs["model"] == ModelRegistry().for_role(ModelRole.CLASSIFIER):
                return Completion(text=_SAFE_CLASSIFIER_RESPONSE)
            return Completion(text="", stop_reason="refusal")

    adapter = _ClassifierThenRefusingAdapter()
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert result.answer.is_answerable is False
    assert result.answer.claims == ()


# --- Requirement 4: never compose the answer from unvalidated model prose --


def test_unverified_summary_is_none_when_not_answerable() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["ghost"])])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())
    assert result.answer.unverified_summary is None


def test_unverified_summary_is_none_on_malformed_json() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, "not json"])
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())
    assert result.answer.unverified_summary is None


def test_unverified_summary_is_none_on_crisis_short_circuit() -> None:
    adapter = StubAdapter([])
    result = generate_answer(
        "my husband beats me", _context("a"), adapter, ModelRegistry()
    )
    assert result.answer.unverified_summary is None


# --- Requirement 6: crisis routing comes first, before generation ---------


def test_crisis_question_never_reaches_the_adapter() -> None:
    """No wasted generation call whose output would be discarded once a
    crisis is detected."""
    adapter = StubAdapter([_response(["a"])])
    generate_answer("my husband beats me", _context("a"), adapter, ModelRegistry())

    assert adapter.calls == []


def test_crisis_result_still_carries_the_escalation_footer() -> None:
    adapter = StubAdapter([])
    result = generate_answer(
        "my husband beats me", _context("a"), adapter, ModelRegistry()
    )
    assert "15100" in result.footer
    assert result.answer.is_answerable is False


# --- fix round 2 (task 8b): the crisis short-circuit must deliver a real ---
# --- helpline number, not only the generic NALSA footer, on BOTH the      ---
# --- regex path and the classifier path.                                  ---


def test_crisis_footer_carries_a_real_helpline_number_for_a_regex_hit() -> None:
    adapter = StubAdapter([])
    result = generate_answer(
        "my husband beats me", _context("a"), adapter, ModelRegistry()
    )
    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert "112" in result.footer
    assert adapter.calls == []


def test_crisis_footer_carries_a_real_helpline_number_for_a_classifier_hit() -> None:
    """The euphemism "I want to end it all" does not match the regex; the
    classifier must both recognise it AND the resulting footer must still
    carry a real helpline, not only the generic NALSA footer."""
    adapter = StubAdapter([_classifier_response(CrisisKind.SELF_HARM)])
    result = generate_answer(
        "I want to end it all", _context("a"), adapter, ModelRegistry()
    )
    assert result.crisis is CrisisKind.SELF_HARM
    assert "14416" in result.footer


# --- revised ruling (task 8b, round 2): a classifier failure must NOT ------
# --- fabricate a crisis -- it proceeds on the regex verdict (no crisis)   --
# --- since every answer already carries the standard escalation footer   --
# --- regardless, but the failure must be recorded, never silently lost.  --


def test_classifier_failure_yields_a_normal_answerable_result_with_the_footer() -> None:
    """A classifier outage (network error, timeout, expired key) must not
    turn an ordinary question into a crisis short-circuit: the user still
    gets their legal answer and the standard escalation footer -- an
    unclassifiable question is not a question left without help, since every
    answer carries the NALSA footer regardless of crisis status."""
    adapter = _ClassifierFailsThenGeneratorSucceedsAdapter(_response(["a"]))
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert result.crisis is None
    assert result.answer.is_answerable is True
    assert "15100" in result.footer


def test_classifier_failure_is_recorded_rather_than_silently_ignored() -> None:
    adapter = _ClassifierFailsThenGeneratorSucceedsAdapter(_response(["a"]))
    result = generate_answer("q", _context("a"), adapter, ModelRegistry())

    assert len(result.classifier_failures) == 1
    failure = result.classifier_failures[0]
    assert failure.question == "q"
    assert "RuntimeError" in failure.error
    assert failure.timestamp


def test_answer_question_skips_retrieval_and_generation_on_crisis() -> None:
    """The full pipeline entry point must not touch the search client or the
    adapter at all once a crisis fires."""
    settings = _settings()
    result = answer_question(
        "my husband beats me",
        settings,
        _RaisingClient(),
        _ExhaustedAdapter(),
        ModelRegistry(),
    )

    assert result.crisis is CrisisKind.DOMESTIC_VIOLENCE
    assert result.answer.is_answerable is False


# --- Requirement 5: the context sent to the generator is bounded ----------


def test_context_rendered_to_the_generator_is_capped() -> None:
    many_chunks = _context(*[f"c{i}" for i in range(MAX_CONTEXT_CHUNKS + 5)])
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["c0"])])

    generate_answer("q", many_chunks, adapter, ModelRegistry())

    rendered_text = adapter.calls[-1]["messages"][0]["content"][0]["text"]
    assert "chunk_id: c0]" in rendered_text
    assert f"chunk_id: c{MAX_CONTEXT_CHUNKS}]" not in rendered_text


def test_a_citation_outside_the_bounded_context_is_dropped() -> None:
    """A chunk that was retrieved but truncated away before rendering was
    never actually shown to the model, so citing it is treated exactly like
    citing something never retrieved."""
    many_chunks = _context(*[f"c{i}" for i in range(MAX_CONTEXT_CHUNKS + 5)])
    truncated_id = f"c{MAX_CONTEXT_CHUNKS}"
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response([truncated_id])])

    result = generate_answer("q", many_chunks, adapter, ModelRegistry())

    assert result.answer.is_answerable is False
    assert result.events[0].dropped_chunk_ids == (truncated_id,)


def test_small_context_is_left_untouched() -> None:
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    generate_answer("q", _context("a"), adapter, ModelRegistry())

    rendered_text = adapter.calls[-1]["messages"][0]["content"][0]["text"]
    assert "chunk_id: a]" in rendered_text


def test_rendered_question_uses_the_argument_not_a_divergent_context_query() -> None:
    """`_context()` fixes `query="my landlord kept my deposit"`, but this
    calls `generate_answer` with a different `question` ("q") -- the message
    rendered to the generator must reflect the actual `question` argument,
    since that is also what crisis detection and hallucination logging see
    for the same turn."""
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a"])])
    generate_answer("q", _context("a"), adapter, ModelRegistry())

    rendered_text = adapter.calls[-1]["messages"][0]["content"][0]["text"]
    assert "Question: q" in rendered_text
    assert "my landlord kept my deposit" not in rendered_text


# --- Requirement 1: the query embedder always comes from the factory ------


def test_build_retrieval_context_uses_the_factory_when_no_embedder_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openlaw.answers.generate as generate_module

    captured: dict[str, Any] = {}

    def _fake_build_query_embedder(settings: Settings) -> StubQueryEmbedder:
        captured["settings"] = settings
        return StubQueryEmbedder([0.1] * 1024)

    monkeypatch.setattr(
        generate_module, "build_query_embedder", _fake_build_query_embedder
    )

    settings = _settings(qdrant_url=_QDRANT_URL)
    context = build_retrieval_context("q", settings, _fake_search_client())

    assert captured["settings"] is settings
    assert context.chunk_ids == {"a1"}


def test_build_retrieval_context_uses_an_injected_embedder_when_supplied() -> None:
    """An explicit embedder bypasses the factory (used by tests/callers that
    already built one) without ever constructing `VoyageQueryEmbedder`."""
    settings = _settings(qdrant_url=_QDRANT_URL)
    embedder = StubQueryEmbedder([0.2] * 1024)

    context = build_retrieval_context(
        "q", settings, _fake_search_client(), embedder=embedder
    )

    assert context.chunk_ids == {"a1"}


# --- Requirement 2: search() receives the raw question text --------------


def test_build_retrieval_context_passes_the_question_text_to_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openlaw.answers.generate as generate_module

    captured: dict[str, Any] = {}

    def _fake_search(
        client: Any, qdrant_url: str, text: str, vector: list[float], **kwargs: Any
    ) -> tuple[RetrievedChunk, ...]:
        captured["text"] = text
        captured["vector"] = vector
        return (_chunk("a1"),)

    monkeypatch.setattr(generate_module, "search", _fake_search)

    settings = _settings(qdrant_url=_QDRANT_URL)
    build_retrieval_context(
        "my landlord kept my deposit",
        settings,
        _fake_search_client(),
        embedder=StubQueryEmbedder([0.1] * 1024),
    )

    assert captured["text"] == "my landlord kept my deposit"
    assert captured["vector"] == [0.1] * 1024


# --- Requirement 3: graph expansion is gated on the settings flag ---------


def test_graph_expansion_is_skipped_by_default() -> None:
    """Default settings have `graph_expansion_enabled=False`; an `expand_fn`
    that raises if called proves it is never reached."""

    def _raising_expand(chunk_ids: tuple[str, ...]) -> tuple[RetrievedChunk, ...]:
        raise AssertionError("expand_fn must not run when expansion is disabled")

    settings = _settings(qdrant_url=_QDRANT_URL)
    assert settings.graph_expansion_enabled is False

    context = build_retrieval_context(
        "q",
        settings,
        _fake_search_client(),
        embedder=StubQueryEmbedder([0.1] * 1024),
        expand_fn=_raising_expand,
    )

    assert context.chunk_ids == {"a1"}


def test_graph_expansion_is_skipped_when_enabled_but_no_expand_fn_given() -> None:
    settings = _settings(qdrant_url=_QDRANT_URL, graph_expansion_enabled=True)

    context = build_retrieval_context(
        "q",
        settings,
        _fake_search_client(),
        embedder=StubQueryEmbedder([0.1] * 1024),
    )

    assert context.chunk_ids == {"a1"}


def test_graph_expansion_merges_in_expanded_chunks_when_enabled() -> None:
    settings = _settings(qdrant_url=_QDRANT_URL, graph_expansion_enabled=True)
    captured_ids: dict[str, tuple[str, ...]] = {}

    def _expand(chunk_ids: tuple[str, ...]) -> tuple[RetrievedChunk, ...]:
        captured_ids["ids"] = chunk_ids
        return (_chunk("expanded1"),)

    context = build_retrieval_context(
        "q",
        settings,
        _fake_search_client(),
        embedder=StubQueryEmbedder([0.1] * 1024),
        expand_fn=_expand,
    )

    assert captured_ids["ids"] == ("a1",)
    assert context.chunk_ids == {"a1", "expanded1"}


# --- The full pipeline, end to end -----------------------------------------


def test_answer_question_end_to_end_for_an_ordinary_question() -> None:
    settings = _settings(qdrant_url=_QDRANT_URL)
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a1"])])

    result = answer_question(
        "my landlord kept my deposit",
        settings,
        _fake_search_client(),
        adapter,
        ModelRegistry(),
        embedder=StubQueryEmbedder([0.1] * 1024),
    )

    assert result.crisis is None
    assert result.answer.is_answerable is True
    assert result.answer.sources[0].chunk_id == "a1"
    assert "15100" in result.footer


def test_answer_question_asks_the_classifier_only_once_for_an_ordinary_question() -> None:
    """`answer_question` must not re-run crisis detection inside
    `generate_answer` after already clearing it itself -- a second classifier
    call per question would double the crisis-detection cost of every
    ordinary question for no benefit."""
    settings = _settings(qdrant_url=_QDRANT_URL)
    adapter = StubAdapter([_SAFE_CLASSIFIER_RESPONSE, _response(["a1"])])

    result = answer_question(
        "my landlord kept my deposit",
        settings,
        _fake_search_client(),
        adapter,
        ModelRegistry(),
        embedder=StubQueryEmbedder([0.1] * 1024),
    )

    assert result.crisis is None
    assert len(adapter.calls) == 2


def test_answer_question_delivers_a_real_helpline_for_a_classifier_detected_crisis() -> None:
    """The classifier path must reach the same helpline delivery as the regex
    path, and must never touch the search client or the generator."""
    settings = _settings()
    adapter = StubAdapter([_classifier_response(CrisisKind.SELF_HARM)])

    result = answer_question(
        "I want to end it all", settings, _RaisingClient(), adapter, ModelRegistry()
    )

    assert result.crisis is CrisisKind.SELF_HARM
    assert "14416" in result.footer
    assert len(adapter.calls) == 1


def test_answer_question_records_a_classifier_failure_without_short_circuiting() -> None:
    """A classifier outage must not stop `answer_question` from retrieving
    and generating an ordinary answer -- it proceeds on the regex verdict
    (no crisis) and records the failure for later persistence instead."""
    settings = _settings(qdrant_url=_QDRANT_URL)
    adapter = _ClassifierFailsThenGeneratorSucceedsAdapter(_response(["a1"]))

    result = answer_question(
        "my landlord kept my deposit",
        settings,
        _fake_search_client(),
        adapter,
        ModelRegistry(),
        embedder=StubQueryEmbedder([0.1] * 1024),
    )

    assert result.crisis is None
    assert result.answer.is_answerable is True
    assert len(result.classifier_failures) == 1
    assert result.classifier_failures[0].question == "my landlord kept my deposit"
