"""Escalation, crisis routing, and hallucination logging.

Routing someone to a human is a success state, not a failure state. Every
answer ends with a route to free legal aid, and questions signalling danger
get helpline numbers ahead of any legal analysis — these surface constantly in
legal-aid queries, and a statutory explanation is the wrong first response.

Crisis detection here is a plain keyword/pattern match, not a model call: it
has to work with no API key configured and it has to be auditable. Because it
is a blunt instrument, it is deliberately tuned for recall over precision —
the patterns in `CRISIS_PATTERNS` favour looser, more colloquial phrasing
("he hurts me", "no point in living") over narrow, clinical wording. A false
positive costs the user one extra paragraph of helpline numbers before their
legal answer. A false negative can cost a life. When in doubt, this module is
built to err toward showing help.

Every dropped citation is logged. That log is both a safety metric — how
often is the system inventing citations, and for what kinds of questions —
and a labelled evaluation dataset produced free by production traffic. See
`build_hallucination_events` for what is and is not persisted, and why.
"""

import json
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openlaw.answers.validate import ValidatedAnswer
from openlaw.retrieval.context import RetrievalContext

NALSA_HELPLINE = "15100"


class CrisisKind(Enum):
    """Categories requiring an immediate helpline rather than legal analysis."""

    DOMESTIC_VIOLENCE = "domestic_violence"
    CUSTODIAL_ABUSE = "custodial_abuse"
    TRAFFICKING = "trafficking"
    SELF_HARM = "self_harm"


# Deliberately recall-biased: these match colloquial, softer phrasing as well
# as explicit terms. See the module docstring for why that trade-off is the
# right one for a crisis classifier with no confidence score to hedge on.
CRISIS_PATTERNS: dict[CrisisKind, tuple[str, ...]] = {
    CrisisKind.SELF_HARM: (
        r"\bend my life\b",
        r"\bkill myself\b",
        r"\bsuicide\b",
        r"\bno reason to live\b",
        r"\bno point in living\b",
        r"\bdon'?t want to live\b",
        r"\bwant to die\b",
        r"\btake my own life\b",
        r"\bharm(?:ing)? myself\b",
        r"\bhurt(?:ing)? myself\b",
    ),
    CrisisKind.TRAFFICKING: (
        r"\btrafficked\b",
        r"\btrafficking\b",
        r"\bsold into\b",
        r"\bforced (?:labour|labor|prostitution)\b",
        r"\bbonded labour\b",
    ),
    CrisisKind.CUSTODIAL_ABUSE: (
        r"\bpolice\s+(?:beat|beating|tortured|torture)\b",
        r"\bin (?:the )?lockup\b",
        r"\bcustodial (?:death|torture|violence)\b",
        r"\bbeaten in custody\b",
        r"\bthird[\s-]degree\b",
    ),
    CrisisKind.DOMESTIC_VIOLENCE: (
        r"\b(?:husband|wife|in-laws|partner)\s+"
        r"(?:beats?|beating|hits?|hitting|hurts?|hurting)\b",
        r"\bdomestic violence\b",
        r"\bdowry harassment\b",
        r"\bbeats? me\b",
        r"\bhits? me\b",
        r"\bhurts? me\b",
        r"\bscared of my (?:husband|wife|partner|in-laws)\b",
        r"\bafraid of my (?:husband|wife|partner|in-laws)\b",
    ),
}

_CRISIS_RESPONSES: dict[CrisisKind, str] = {
    CrisisKind.SELF_HARM: (
        "If you are thinking about harming yourself, please talk to someone now. "
        "Tele-MANAS is free and available 24 hours on 14416. "
        "You do not have to face this alone."
    ),
    CrisisKind.TRAFFICKING: (
        "If someone is in immediate danger, call 112. "
        "The helpline is 1098 for children and 181 for women. "
        "Both are free and available at any hour."
    ),
    CrisisKind.CUSTODIAL_ABUSE: (
        "If someone is being harmed in custody, this is urgent. "
        "Call 112, and contact the National Human Rights Commission on 14433. "
        "A free legal aid lawyer can be requested immediately on 15100."
    ),
    CrisisKind.DOMESTIC_VIOLENCE: (
        "If you are in danger right now, call 112. "
        "The women's helpline is 181, and the police women's helpline is 1091. "
        "Both are free and available 24 hours."
    ),
}

# Severity order — the most urgent category wins when a message matches
# several (fail-safe: resolve to whichever response gets help fastest rather
# than picking arbitrarily or refusing to classify).
_SEVERITY_ORDER: tuple[CrisisKind, ...] = (
    CrisisKind.SELF_HARM,
    CrisisKind.TRAFFICKING,
    CrisisKind.CUSTODIAL_ABUSE,
    CrisisKind.DOMESTIC_VIOLENCE,
)

_COMPILED: dict[CrisisKind, tuple[re.Pattern[str], ...]] = {
    kind: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for kind, patterns in CRISIS_PATTERNS.items()
}


def detect_crisis(text: str) -> CrisisKind | None:
    """Return the crisis category `text` signals, or None.

    Fails safe: patterns are recall-biased (see module docstring) and, when a
    message matches more than one category, the most urgent one wins rather
    than the first or an arbitrary one.
    """
    if not text:
        return None
    for kind in _SEVERITY_ORDER:
        if any(pattern.search(text) for pattern in _COMPILED[kind]):
            return kind
    return None


def crisis_response(kind: CrisisKind) -> str:
    """Return the helpline-first response for `kind`."""
    return _CRISIS_RESPONSES[kind]


def escalation_footer(state: str | None = None) -> str:
    """Return the free-legal-aid routing appended to every answer."""
    authority = (
        f"the {state} State Legal Services Authority"
        if state
        else "your District Legal Services Authority"
    )
    return (
        "This is legal information, not legal advice, and it cannot replace a "
        "lawyer who knows your case.\n\n"
        f"You may be entitled to a free lawyer. Contact {authority}, or call the "
        f"NALSA helpline on {NALSA_HELPLINE}. Around 80% of people in India "
        "qualify for free legal aid."
    )


class HallucinationEvent(BaseModel):
    """A record of citations the validator refused to render.

    This intentionally keeps `question` and `claim_text` verbatim rather than
    hashing or redacting them: the whole point of the log is to let a human
    look at *why* a citation was hallucinated, and a hashed or truncated
    question cannot be diagnosed or turned into an eval case. The trade-off
    this makes, and why it is the right one for this product, is written up
    in the accompanying task report rather than re-argued in code comments.
    """

    model_config = ConfigDict(frozen=True)

    question: str
    dropped_chunk_ids: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    claim_text: str
    timestamp: str


def build_hallucination_events(
    question: str, answer: ValidatedAnswer, context: RetrievalContext
) -> tuple[HallucinationEvent, ...]:
    """Return one event per dropped claim. Empty when nothing was dropped."""
    if not answer.dropped:
        return ()
    now = datetime.now(UTC).isoformat()
    retrieved = tuple(sorted(context.chunk_ids))
    return tuple(
        HallucinationEvent(
            question=question,
            dropped_chunk_ids=dropped.unresolvable_chunk_ids,
            retrieved_chunk_ids=retrieved,
            claim_text=dropped.text,
            timestamp=now,
        )
        for dropped in answer.dropped
    )


def append_events(events: tuple[HallucinationEvent, ...], path: Path) -> int:
    """Append `events` to the JSONL log at `path`. Returns the number written."""
    if not events:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.model_dump()) + "\n")
    return len(events)
