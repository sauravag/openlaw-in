"""Rewrite a person's question into short legal-issue search queries.

People describe their situation, not the legal issue: "my divorce case has
been stuck for years, how do I move it faster?" Searched as written, the lay
words ("stuck", "faster", "filed", "years") are noise to the sparse/BM25
side, and Hindu Marriage Act s.21B -- the provision on day-to-day trial and
disposal within six months -- ranked 8th among statutes. Phrased as a legal
issue ("time limit for trial and disposal of divorce petition"), the same
search ranks it 1st, without the rewriter needing to know the answer.

`rewrite_query` asks the CLASSIFIER-role model (cheap by design, see
`openlaw.answers.models.ModelRole`) for 1-3 such queries, under a strict JSON
schema. The original question is always searched as well; these only ADD
searches (`openlaw.retrieval.search.search_many` fuses them).

**The rewrite is search text only.** It never reaches the generator's
prompt, the answer, or the API response, and it can never make anything
citable: the validator still only accepts chunk ids actually retrieved and
shown this turn. A bad rewrite can at worst retrieve less relevant chunks.

**It never raises and never blocks an answer.** Any failure -- no API key,
a network error, a refusal, malformed JSON, an empty or over-long list --
returns `()`, so retrieval runs on the original question alone exactly as it
did before this module existed. The failure is logged at WARNING without
the question text (that text can be sensitive, and the classifier-failure
JSONL log is the place for verbatim inputs, not ordinary logs).
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole

logger = logging.getLogger(__name__)

MAX_REWRITE_QUERIES = 3
#: A search query, not prose: anything longer is the model answering instead
#: of rewriting, and is rejected as invalid.
MAX_REWRITE_QUERY_CHARS = 200
#: Three short queries in one small JSON object fit comfortably.
MAX_REWRITE_TOKENS = 256
#: The rewrite is optional, so it must never hold an answer up for long. The
#: shared adapter's own timeouts are far longer (OpenRouter 120 s; the
#: Anthropic SDK default 600 s with retries); past this bound the turn goes
#: ahead on the original question alone.
REWRITE_TIMEOUT_SECONDS = 5.0
#: Calls run on this pool so the wait can be bounded. A call that times out
#: is abandoned, not joined: it finishes (or fails) in the background and its
#: result is discarded. The pool is small and bounded, so a provider outage
#: that hangs every call queues new ones, which then also time out -- the
#: fallback still happens within the bound.
_REWRITE_WORKERS = 4
_REWRITE_EXECUTOR = ThreadPoolExecutor(
    max_workers=_REWRITE_WORKERS, thread_name_prefix="query-rewrite"
)

REWRITE_SYSTEM_PROMPT = """\
You turn a person's question to an Indian legal-aid service into short \
search queries for a database of Indian statutes and court judgments.

Write 1 to 3 queries in English, each naming one legal issue the question \
raises, in the words a statute or judgment would use (for example: "speedy \
disposal of divorce petition", "limit on adjournments in civil suits", \
"High Court direction for time-bound disposal of a family court case"). \
Cover the remedy or procedure the person is trying to reach, not only the \
background facts.

When an earlier question from the same conversation is given, use it only \
to understand what the current question refers to; write queries for the \
current question.

Do not answer the question. Do not give advice. Do not add names, dates or \
other personal details. Each query must be short, under 20 words.

Respond with strict JSON only, matching this shape exactly:
{"queries": ["...", "..."]}"""

QUERY_REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_REWRITE_QUERIES,
            "description": "Short legal-issue search queries in English.",
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


def _valid_query(item: object) -> str:
    """Return `item` stripped, or raise ValueError when it is not a usable
    query string."""
    if not isinstance(item, str):
        raise ValueError(f"rewrite query is not a string: {type(item).__name__}")
    query = item.strip()
    if not query:
        raise ValueError("rewrite query is blank")
    if len(query) > MAX_REWRITE_QUERY_CHARS:
        raise ValueError(f"rewrite query is {len(query)} characters long")
    return query


def _parse_rewrites(payload: str) -> tuple[str, ...]:
    """Parse the model's JSON into queries, raising ValueError on anything
    that does not match `QUERY_REWRITE_SCHEMA` exactly. Never guesses."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"rewrite returned malformed JSON: {error}") from error
    if not isinstance(data, dict) or set(data) != {"queries"}:
        raise ValueError("rewrite response is not an object with only 'queries'")
    queries = data["queries"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_REWRITE_QUERIES:
        raise ValueError("rewrite 'queries' is not a list of 1-3 items")
    return tuple(_valid_query(item) for item in queries)


def _distinct_from(question: str, queries: tuple[str, ...]) -> tuple[str, ...]:
    """Drop repeats, and any query that is just the question again (it is
    already searched), keeping first-seen order."""
    seen = {question.strip().casefold()}
    kept: list[str] = []
    for query in queries:
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            kept.append(query)
    return tuple(kept)


def _rewrite_input(english_query: str, previous_question: str | None) -> str:
    """The text the rewriter reads: the question alone, or, on a follow-up,
    the previous question labelled as context and then the current one."""
    if not previous_question:
        return english_query
    return (
        f"Earlier question in this conversation (context only):\n"
        f"{previous_question}\n\nCurrent question:\n{english_query}"
    )


def rewrite_query(
    english_query: str,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    previous_question: str | None = None,
    timeout_seconds: float = REWRITE_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Return up to three legal-issue search queries for `english_query`, or
    `()` on any failure. Never raises -- see the module docstring.

    `previous_question` (the person's question from the turn before, if
    any) is context so a follow-up like "and what about an appeal?" can be
    rewritten at all; it stays search input only, like the rewrite itself.
    The call is abandoned after `timeout_seconds`.
    """
    future = _REWRITE_EXECUTOR.submit(
        adapter.complete,
        model=registry.for_role(ModelRole.CLASSIFIER),
        system=REWRITE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _rewrite_input(english_query, previous_question)}
                ],
            }
        ],
        output_schema=QUERY_REWRITE_SCHEMA,
        max_tokens=MAX_REWRITE_TOKENS,
    )
    try:
        completion = future.result(timeout=timeout_seconds)
        queries = _parse_rewrites(completion.text)
    except FutureTimeoutError:
        future.cancel()  # a no-op once running; the result is discarded
        logger.warning(
            "query rewrite timed out after %.1fs; searching the original "
            "question alone.",
            timeout_seconds,
        )
        return ()
    except Exception as error:
        logger.warning(
            "query rewrite failed; searching the original question alone. "
            "error=%s: %s",
            type(error).__name__,
            error,
        )
        return ()
    return _distinct_from(english_query, queries)
