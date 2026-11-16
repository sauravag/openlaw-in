"""Tests for rewriting a question into legal-issue search queries.

The rewrite is SEARCH TEXT ONLY: it only changes what is retrieved, never
what may be cited or shown. Every failure falls back to "no rewrites", so
retrieval runs on the original question alone, exactly as before.
"""

import json
import logging

import pytest

from openlaw.answers.models import Completion, ModelRegistry, ModelRole, StubAdapter
from openlaw.answers.query_rewrite import (
    MAX_REWRITE_QUERIES,
    QUERY_REWRITE_SCHEMA,
    rewrite_query,
)

QUESTION = (
    "I filed a divorce proceedings case against my husband. What legal remedies "
    "do I have to move the case faster if it has been stuck for years?"
)


class _FailingAdapter:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def complete(self, model, system, messages, output_schema, max_tokens) -> Completion:
        raise self._error


def _reply(queries: object) -> str:
    return json.dumps({"queries": queries})


def test_returns_the_rewritten_queries() -> None:
    # Arrange
    adapter = StubAdapter([_reply(["speedy disposal of divorce petition", "limit on adjournments"])])

    # Act
    rewrites = rewrite_query(QUESTION, adapter, ModelRegistry())

    # Assert
    assert rewrites == ("speedy disposal of divorce petition", "limit on adjournments")


def test_asks_the_cheap_classifier_role_with_the_strict_schema() -> None:
    adapter = StubAdapter([_reply(["speedy disposal of divorce petition"])])
    registry = ModelRegistry()

    rewrite_query(QUESTION, adapter, registry)

    call = adapter.calls[0]
    assert call["model"] == registry.for_role(ModelRole.CLASSIFIER)
    assert call["output_schema"] == QUERY_REWRITE_SCHEMA
    assert QUESTION in json.dumps(call["messages"])


def test_the_schema_is_strict() -> None:
    assert QUERY_REWRITE_SCHEMA["additionalProperties"] is False
    assert QUERY_REWRITE_SCHEMA["required"] == ["queries"]
    queries = QUERY_REWRITE_SCHEMA["properties"]["queries"]
    assert queries["minItems"] == 1
    assert queries["maxItems"] == MAX_REWRITE_QUERIES == 3


def test_strips_whitespace_and_drops_repeats_of_the_question() -> None:
    adapter = StubAdapter([_reply(["  speedy disposal  ", QUESTION.upper(), "speedy disposal"])])

    assert rewrite_query(QUESTION, adapter, ModelRegistry()) == ("speedy disposal",)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("no API key configured"), TimeoutError("network timed out")],
)
def test_a_failed_call_falls_back_to_no_rewrites_and_warns(error, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="openlaw.answers.query_rewrite"):
        rewrites = rewrite_query(QUESTION, _FailingAdapter(error), ModelRegistry())

    assert rewrites == ()
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_the_warning_never_logs_the_question_text(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="openlaw.answers.query_rewrite"):
        rewrite_query(QUESTION, StubAdapter(["not json"]), ModelRegistry())

    assert "husband" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps(["speedy disposal"]),
        json.dumps({"other": ["speedy disposal"]}),
        _reply([]),
        _reply("speedy disposal"),
        _reply(["a", "b", "c", "d"]),
        _reply(["speedy disposal", 7]),
        _reply(["   "]),
        _reply(["x" * 1_000]),
        json.dumps({"queries": ["speedy disposal"], "answer": "You should..."}),
    ],
)
def test_schema_invalid_output_falls_back_to_no_rewrites(payload: str) -> None:
    assert rewrite_query(QUESTION, StubAdapter([payload]), ModelRegistry()) == ()


def test_an_exhausted_stub_with_no_queued_reply_falls_back() -> None:
    """The test-suite shape of "no API key configured": nothing to call."""
    assert rewrite_query(QUESTION, StubAdapter([]), ModelRegistry()) == ()


# --- fix round 1: bounded call, follow-up context --------------------------


import threading
import time


class _HangingAdapter:
    """Blocks until released -- a hung provider call."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def complete(self, model, system, messages, output_schema, max_tokens) -> Completion:
        self.release.wait(timeout=30)
        return Completion(text=_reply(["too late"]))


def test_a_hung_call_falls_back_within_the_timeout(caplog) -> None:
    adapter = _HangingAdapter()
    try:
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="openlaw.answers.query_rewrite"):
            rewrites = rewrite_query(QUESTION, adapter, ModelRegistry(), timeout_seconds=0.2)
        elapsed = time.monotonic() - started
    finally:
        adapter.release.set()

    assert rewrites == ()
    assert elapsed < 1.5
    assert "timed out" in caplog.text


def test_the_default_timeout_is_a_few_seconds() -> None:
    from openlaw.answers.query_rewrite import REWRITE_TIMEOUT_SECONDS

    assert 1 <= REWRITE_TIMEOUT_SECONDS <= 10


def test_a_follow_up_sees_the_previous_question_as_context() -> None:
    adapter = StubAdapter([_reply(["appeal against divorce decree"])])

    rewrite_query(
        "and what about an appeal?", adapter, ModelRegistry(),
        previous_question="My divorce case has been stuck for years.",
    )

    sent = json.dumps(adapter.calls[0]["messages"], ensure_ascii=False)
    assert "My divorce case has been stuck for years." in sent
    assert "and what about an appeal?" in sent


def test_a_first_turn_sends_only_the_question() -> None:
    adapter = StubAdapter([_reply(["speedy disposal"])])

    rewrite_query(QUESTION, adapter, ModelRegistry())

    assert adapter.calls[0]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": QUESTION}]}
    ]
