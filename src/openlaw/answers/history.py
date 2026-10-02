"""Conversation history, in the only form the model may ever see it.

Conversation history is the newest route by which unverified prose could
reach a user. A model handed its own earlier free text will restate legal
content in fresh words that no validator ever checked -- the
`unverified_summary` bypass, arriving through a different door. The only
assistant content this module renders is claim text that already survived
`validate_answer`, printed with the very ids that justified it, so a
restatement in the next turn has something to re-cite and the validator has
something to check it against.

**What each turn's claims validate against, said exactly.** The per-turn
contract in `openlaw.answers.validate` does not change and grows no "cited
earlier" branch: a claim survives this turn only if every id it cites is a
member of THIS turn's retrieval set. History is made compatible with that
contract by re-showing, not by relaxing it. `carried_chunk_ids` returns the
ids an earlier claim depends on; the turn assembly (Task 9) re-fetches those
chunks and renders them into this turn's own context message, so they are
in `RetrievalContext.chunk_ids` like any other retrieved chunk.
`build_history_messages` then renders only those claims whose ids are all in
the set actually shown this turn (`shown_chunk_ids`, and
`shown_excerpt_ids` for the document namespace). The result is a property
stronger than the validator's own and checkable by reading one function:
**every id that appears anywhere in the prompt is an id shown this turn.** A
claim whose chunk was re-indexed away, or crowded out of the carried budget,
is simply not shown -- it is never shown with a dangling id, and the
validator is never asked to trust an id it cannot see.

`shown_chunk_ids` is a REQUIRED keyword argument for that reason. The
narrowing is not a courtesy the caller may skip; there is no overload of
this function that renders history without being told what this turn can
support. For the same reason `build_history_messages` takes `HistoryTurn`
and not `ValidatedAnswer`: the narrower type is the enforcement. A
`ValidatedAnswer` carries `unverified_summary`, and a renderer holding one
is a renderer one attribute access away from the bypass. `HistoryTurn` has
no such field, sets `extra="forbid"` so one cannot be added by accident,
and `from_validated_answer` is the single, audited conversion -- it copies
the two claim lists and nothing else. There is deliberately no "conversation
so far" field, no running summary, and no history-flavoured equivalent of
`unverified_summary`: a field like that would be exactly the hole Plan 3
closed, re-cut in a new module.

**Bounded three ways, because a count is not a size.** `MAX_HISTORY_TURNS`
caps how many turns replay, `MAX_CARRIED_CHUNKS` caps how much of this
turn's retrieval budget earlier citations may claim, and
`MAX_HISTORY_CHARS` caps the rendered text itself. The third exists because
the first two do not bound input size: six turns of unbounded claim text is
unbounded input, exactly the finding
`openlaw.answers.generate.MAX_CONTEXT_CHARS` records for retrieved chunks.
Whole turns are dropped, oldest first, so a question never loses its answer.

**A turn that produced no citable claim still renders, as a fixed marker.**
`NO_CARRIED_CLAIMS_LINE` is a constant in this file, not model output and
not prose about the law. It keeps the user/assistant alternation intact
(no orphan question) and says honestly that nothing from that turn is
citable now, rather than inventing an answer or silently deleting the
person's question.

**The user's earlier questions are user input, not system voice.** They are
carried as `role: "user"` messages and fenced with
`HISTORY_QUESTION_FENCE_OPEN`/`_CLOSE`, following
`openlaw.documents.fence`'s approach for untrusted text: `sanitise` runs
over every question before it is fenced, so an earlier question cannot
forge either half of this fence or of the document fence. Claim text is
sanitised on the same call path -- a `Claim.text` from a turn that carried
an uploaded document is prose an attacker may have influenced (see
`openlaw.answers.validate`'s "fifth route"), so it gets the same treatment
rather than being trusted because a validator passed its citations.

**History is crisis-scanned, at document strength** (`history_crisis`).
Until the final whole-plan review nothing in the codebase scanned history at
all, and carry-forward 18's instruction to do it at question strength would
have silenced the legal answer on every turn after a disclosure. The verdict
found here therefore rides ALONGSIDE the answer, exactly as a document's
stored verdict does, and costs nothing: free regex over the person's own
earlier questions, never a model call and never over assistant text.

**Language-blind by construction.** Nothing here branches on
`HistoryTurn.language`: the same code renders a Hindi turn and an English
one, so a property that holds for English cannot silently fail in
translation -- this project's most repeated defect (Plan 4 finding #4,
Plan 5 H1, Task 6's crisis footer, Task 7's document claims). `language` is
carried as data for the turn assembly's benefit, never consulted here, and
`tests/answers/test_history.py` is parametrised over both languages on
every path, including the empty-history and no-surviving-claims returns.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openlaw.answers.escalation import CrisisKind, detect_crisis, most_urgent_crisis
from openlaw.answers.schema import Claim, DocumentClaim
from openlaw.answers.validate import ValidatedAnswer
from openlaw.documents import (
    MAX_CARRIED_CHUNKS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
)
from openlaw.documents.fence import sanitise

#: Fence for an earlier question. The user's own words are untrusted text
#: like any other (`openlaw.documents.fence`), so they are marked as the
#: person's, on a `role: "user"` message, and `sanitise`d first. The
#: delimiters are spelled with runs of three `<`/`>` exactly so that
#: `sanitise`'s generic angle-run breaker already neutralises them inside
#: any text placed within -- no new escaping rule is needed.
HISTORY_QUESTION_FENCE_OPEN = "<<<EARLIER_USER_QUESTION>>>"
HISTORY_QUESTION_FENCE_CLOSE = "<<<END_EARLIER_USER_QUESTION>>>"

#: The two line shapes this module may emit, and the only ones. Fixed
#: strings rather than inline f-strings so a test can pin the rendering
#: byte-for-byte and so there is one place to read what the model is shown.
CLAIM_LINE = "- {text} [supported by: {ids}]"
DOCUMENT_CLAIM_LINE = "- {text} [from the person's document: {ids}]"

#: Shown in place of an assistant turn with nothing citable this turn --
#: either the turn produced no surviving claim, or the chunks that
#: justified its claims are not in this turn's context. Our own fixed text,
#: never the model's, and deliberately free of legal content.
NO_CARRIED_CLAIMS_LINE = "(no verified statement from that turn is citable this turn)"


class HistoryTurn(BaseModel):
    """One completed turn, in the only form the model may ever see it.

    `question` is the person's own verbatim words; `claims` and
    `document_claims` are what survived `validate_answer`, each still
    carrying the ids that justified it. There is no field for the model's
    prose and `extra="forbid"` means one cannot be added by accident --
    see the module docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    claims: tuple[Claim, ...] = ()
    document_claims: tuple[DocumentClaim, ...] = ()
    language: str = Field(min_length=1)

    @classmethod
    def from_validated_answer(
        cls, *, question: str, answer: ValidatedAnswer, language: str
    ) -> HistoryTurn:
        """Build the carried form of a completed turn.

        The single, audited conversion from the full answer object to the
        narrow one. It reads `claims` and `document_claims` and nothing
        else: `unverified_summary` is unchecked model prose,
        `sources`/`document_citations` are rendered to the person rather
        than to the model, and `dropped` is a refusal record. None of them
        has a route into a prompt through here.
        """
        return cls(
            question=question,
            claims=answer.claims,
            document_claims=answer.document_claims,
            language=language,
        )


def history_crisis(turns: tuple[HistoryTurn, ...]) -> CrisisKind | None:
    """Return the most urgent crisis disclosed in an EARLIER turn's question.

    Carry-forward 18, as the final whole-plan review reversed it. CF-18 ruled
    that a crisis in history should be QUESTION-strength and short-circuit the
    turn; applied literally, a woman who discloses abuse on turn one could
    never receive a legal answer again in that conversation, which is exactly
    the harm the "rides alongside" ruling exists to prevent. A disclosure on
    turn one IS historical by turn four -- the same category of weaker
    evidence as a document's stored verdict -- so the verdict found here is
    carried at DOCUMENT strength: `openlaw.language.pipeline
    ._with_alongside_crisis` attaches its helplines beside a full legal
    answer, and the legal question is still answered.

    **The FREE regex layer only, and deliberately.** `detect_crisis` is a
    deterministic pattern match -- no model call, no cost, no API key. History
    grows with every turn, so a paid classifier call here would make the price
    of turn ten proportional to turns one through nine; the stored document
    verdict exists to avoid that same shape of cost. The coverage limit is
    therefore the regex layer's own (English, transliterated Hinglish, and the
    narrow precision-tuned Devanagari set), and the compensating control is
    that the question of every turn, including this one, still reaches the
    paid classifier on its own account.

    Reads `turn.question` only -- the person's own words. Never a claim, never
    model prose: an assistant turn's text is this system's own output, and
    scanning it would route on the helpline wording it had already emitted.

    NOT windowed by `recent_turns`: `MAX_HISTORY_TURNS` bounds what is
    rendered into a prompt, which is a cost budget. A safety signal that
    expired after six turns would be a disclosure this system heard and then
    forgot, which is the defect CF-18 was opened about.
    """
    return most_urgent_crisis(detect_crisis(turn.question) for turn in turns)


def recent_turns(turns: tuple[HistoryTurn, ...]) -> tuple[HistoryTurn, ...]:
    """Return at most `MAX_HISTORY_TURNS` turns, oldest first.

    Keeps the most recent suffix: a long conversation must not crowd out
    this turn's own retrieval. Both public functions below apply this same
    window, so the ids `carried_chunk_ids` asks the caller to re-fetch are
    exactly the ids the rendered turns may cite -- carrying an id for a turn
    the model never sees would spend a slot of the `MAX_CARRIED_CHUNKS`
    budget on nothing.
    """
    return turns[-MAX_HISTORY_TURNS:]


def _cited_chunk_ids_newest_first(turns: tuple[HistoryTurn, ...]) -> Iterator[str]:
    """Yield every corpus chunk id cited by `turns`, newest turn first.

    Document excerpt ids are deliberately not yielded: they are a separate
    namespace (`openlaw.documents.DOCUMENT_EXCERPT_PREFIX`) which no corpus
    re-fetch could resolve, and mixing them in here would be the namespace
    confusion `openlaw.answers.validate` exists to prevent.
    """
    for turn in reversed(turns):
        for claim in turn.claims:
            yield from claim.chunk_ids


def carried_chunk_ids(turns: tuple[HistoryTurn, ...]) -> tuple[str, ...]:
    """Return the chunk ids an earlier claim depends on, most recent first.

    Deduplicated, capped at `MAX_CARRIED_CHUNKS`, and drawn only from the
    turns `recent_turns` will actually render. These are the chunks the turn
    assembly must re-fetch and re-render into THIS turn's context, so that a
    carried claim's citations are shown to the model again and the per-turn
    validator needs no special case. Ids beyond the cap are simply not
    carried, and the claims that cite them are then not rendered either (see
    `build_history_messages`) -- the budget bounds the prompt, never the
    truthfulness of what is in it.
    """
    unique = dict.fromkeys(_cited_chunk_ids_newest_first(recent_turns(turns)))
    return tuple(unique)[:MAX_CARRIED_CHUNKS]


def _is_fully_shown(cited: tuple[str, ...], shown: frozenset[str]) -> bool:
    """Whether every id in `cited` is in the set shown to the model this turn.

    Whole-claim, like `openlaw.answers.validate._partition_claims`: one
    absent id drops the claim entirely rather than trimming it back to the
    half that checked out.
    """
    return bool(cited) and all(identifier in shown for identifier in cited)


def _claim_lines(turn: HistoryTurn, shown_chunk_ids: frozenset[str]) -> list[str]:
    """Render the legal claims of `turn` that this turn can still support."""
    return [
        CLAIM_LINE.format(text=sanitise(claim.text), ids=", ".join(claim.chunk_ids))
        for claim in turn.claims
        if _is_fully_shown(claim.chunk_ids, shown_chunk_ids)
    ]


def _document_claim_lines(
    turn: HistoryTurn, shown_excerpt_ids: frozenset[str]
) -> list[str]:
    """Render the document claims of `turn` whose excerpts are shown again.

    Labelled "from the person's document", never "supported by": an excerpt
    is not law and must not read like a citation of one (see
    `openlaw.answers.validate.DocumentCitation`).
    """
    return [
        DOCUMENT_CLAIM_LINE.format(
            text=sanitise(claim.text), ids=", ".join(claim.excerpt_ids)
        )
        for claim in turn.document_claims
        if _is_fully_shown(claim.excerpt_ids, shown_excerpt_ids)
    ]


def _text_message(role: str, text: str) -> dict[str, Any]:
    """One message in the shape `openlaw.answers.prompts` already uses.

    Carries no `cache_control`: history differs every turn, so caching it
    would produce a 0% hit rate -- the same reasoning as
    `build_context_message`.
    """
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _user_message(turn: HistoryTurn) -> dict[str, Any]:
    """The person's earlier question, sanitised and fenced as their own words."""
    fenced = "\n".join(
        (
            HISTORY_QUESTION_FENCE_OPEN,
            sanitise(turn.question),
            HISTORY_QUESTION_FENCE_CLOSE,
        )
    )
    return _text_message("user", fenced)


def _assistant_message(
    turn: HistoryTurn,
    shown_chunk_ids: frozenset[str],
    shown_excerpt_ids: frozenset[str],
) -> dict[str, Any]:
    """The verified statements of `turn`, or the fixed no-claims marker."""
    lines = _claim_lines(turn, shown_chunk_ids) + _document_claim_lines(
        turn, shown_excerpt_ids
    )
    return _text_message("assistant", "\n".join(lines) or NO_CARRIED_CLAIMS_LINE)


_MessagePair = tuple[dict[str, Any], dict[str, Any]]


def _pair_chars(pair: _MessagePair) -> int:
    """Total rendered characters of one turn's two messages."""
    return sum(len(message["content"][0]["text"]) for message in pair)


def _within_char_budget(newest_first: tuple[_MessagePair, ...]) -> list[_MessagePair]:
    """Return the newest turns that fit within `MAX_HISTORY_CHARS`.

    A turn count alone is not a bound -- the lesson
    `openlaw.answers.generate._bounded_chunks` records for retrieved chunks,
    which applies identically here: six turns of unbounded claim text is
    unbounded input, and would crowd out this turn's own retrieval.

    Whole turns are dropped, oldest first, so a question is never left
    without its answer or an answer without its question. The newest turn
    is always kept even when it alone exceeds the budget -- same choice, and
    same reasoning, as `_bounded_chunks` keeping its first chunk: the most
    recent exchange is the one a follow-up question is most likely about,
    and its size is itself bounded upstream by
    `openlaw.answers.generate.MAX_ANSWER_TOKENS`.
    """
    kept: list[_MessagePair] = []
    remaining = MAX_HISTORY_CHARS
    for pair in newest_first:
        cost = _pair_chars(pair)
        if kept and cost > remaining:
            break
        kept.append(pair)
        remaining -= cost
    return kept


def build_history_messages(
    turns: tuple[HistoryTurn, ...],
    *,
    shown_chunk_ids: frozenset[str],
    shown_excerpt_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Return at most `MAX_HISTORY_TURNS` turns as alternating messages.

    Oldest first, one `user` message then one `assistant` message per turn,
    so the oldest surviving message is always a question and never an
    orphaned answer. Assistant content is rendered ONLY from claim text with
    its ids appended, never from prose.

    `shown_chunk_ids` and `shown_excerpt_ids` are the id sets actually
    rendered into THIS turn's prompt (`RetrievalContext.chunk_ids` and
    `DocumentContext.excerpt_ids`). A carried claim is rendered only when
    every id it cites is in them, which keeps "cited implies shown to the
    model this turn" literally true and keeps the per-turn validator free of
    a "cited earlier" branch. `shown_excerpt_ids` defaults to empty -- the
    same fail-closed default as `validate_answer`'s `EMPTY_DOCUMENT_CONTEXT`
    -- so a turn with no document attached carries no document claims.

    Bounded three ways: `MAX_HISTORY_TURNS` turns, `MAX_HISTORY_CHARS` of
    rendered text, and -- for what the caller must re-fetch to make any of
    it citable -- `MAX_CARRIED_CHUNKS`. Never mutates `turns`; every model
    involved is frozen.
    """
    pairs = [
        (
            _user_message(turn),
            _assistant_message(turn, shown_chunk_ids, shown_excerpt_ids),
        )
        for turn in recent_turns(turns)
    ]
    kept = _within_char_budget(tuple(reversed(pairs)))
    return [message for pair in reversed(kept) for message in pair]
