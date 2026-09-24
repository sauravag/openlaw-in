"""Escalation, crisis routing, and hallucination logging.

Routing someone to a human is a success state, not a failure state. Every
answer ends with a route to free legal aid, and questions signalling danger
get helpline numbers ahead of any legal analysis — these surface constantly in
legal-aid queries, and a statutory explanation is the wrong first response.

Crisis detection here is a plain keyword/pattern match, not a model call: it
has to work with no API key configured and it has to be auditable. Because it
is a blunt instrument, it is deliberately tuned for recall over precision —
the patterns in `CRISIS_PATTERNS` favour looser, more colloquial phrasing
("he hurts me", "no point in living") over narrow, clinical wording, allow
words to intervene between a subject and its verb ("my husband keeps beating
me"), catch reversed constructions ("tortured by police"), and include
transliterated Hindi/Hinglish phrasing alongside English — this product's
users routinely code-mix, and English-only coverage would silently exclude a
large share of the people this feature exists for. A false positive costs the
user one extra paragraph of helpline numbers before their legal answer. A
false negative can cost a life. When in doubt, this module is built to err
toward showing help.

Every dropped citation is logged. That log is both a safety metric — how
often is the system inventing citations, and for what kinds of questions —
and a labelled evaluation dataset produced free by production traffic. See
`build_hallucination_events` for what is and is not persisted, and why.
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta
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


# Shared verb alternation for "someone hurt/hurts/hurting me" phrasing, used
# both on its own (subject-free: "he keeps hurting me") and after a subject
# with an intervening gap ("husband ... beating"). Kept as one constant so
# the verb list is not maintained in two places.
_HURT_VERBS = r"(?:beats?|beating|hits?|hitting|hurts?|hurting)"

# Transliterated Hindi/Hinglish "hits/beats" verbs (masculine/feminine
# agreement: "maarta"/"maarti", "peeta"/"peeti"), used with the same
# subject-verb gap tolerance as `_HURT_VERBS` above.
_HINDI_HIT_VERBS = r"(?:maart[ai]|peet[ai])"

# Deliberately recall-biased: these match colloquial, softer phrasing as well
# as explicit terms, allow a gap between subject and verb rather than
# requiring strict adjacency, and include transliterated Hindi/Hinglish
# alongside English. See the module docstring for why that trade-off is the
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
        # Hindi/Hinglish (transliterated) — "marna/marne" (die/kill), "jeena"
        # (live), "aatmahatya" (suicide). "chahta"/"chahti" cover both
        # grammatical genders.
        r"\bmarna chaht[ai] hoon\b",
        r"\bmar jaana chaht[ai] hoon\b",
        r"\bmarne (?:ka|ki) (?:soch|ichha)\b",
        r"\bjeena nahi[n]? chaht[ai]\b",
        r"\bjeene ka mann nahi[n]?\b",
        r"\bzindagi khatam\b",
        r"\baatma ?hatya\b",
    ),
    CrisisKind.TRAFFICKING: (
        r"\btrafficked\b",
        r"\btrafficking\b",
        r"\bsold into\b",
        r"\bforced (?:labour|labor|prostitution)\b",
        r"\bbonded labour\b",
        # Trafficking without the word "traffick" itself.
        r"\bkidnapped\b.{0,30}\bmarriage\b",
        r"\bsold\b.{0,30}\b(?:family|work|labour|labor|marriage)\b",
    ),
    CrisisKind.CUSTODIAL_ABUSE: (
        r"\bpolice\s+(?:beat|beating|tortured|torture)\b",
        r"\bin (?:the )?lockup\b",
        r"\bcustodial (?:death|torture|violence)\b",
        r"\bbeaten in custody\b",
        r"\bthird[\s-]degree\b",
        # Reversed construction: victim-first, "by police/cops" at the end,
        # with no reliance on "lockup"/"custody" appearing at all.
        r"\b(?:tortured|beaten|assaulted|beat)\s+by\s+(?:the\s+)?(?:police|cops)\b",
    ),
    CrisisKind.DOMESTIC_VIOLENCE: (
        # Subject and verb allowed to drift apart by intervening words
        # ("husband keeps beating", not just "husband beats").
        rf"\b(?:husband|wife|in-laws|partner)\b.{{0,20}}\b{_HURT_VERBS}\b",
        # Subject-free: catches "he keeps hurting me" without needing
        # "husband"/"wife"/etc. to appear at all.
        rf"\b{_HURT_VERBS}\s+me\b",
        r"\bdomestic violence\b",
        r"\bdowry harassment\b",
        r"\bscared of my (?:husband|wife|partner|in-laws)\b",
        r"\bafraid of my (?:husband|wife|partner|in-laws)\b",
        # Hindi/Hinglish (transliterated) — "maarta/maarti hai" (hits, m/f),
        # "peeta/peeti hai" (beats, colloquial, m/f). Gap-tolerant like the
        # English pattern above, so "mujhe roz maarta hai" (hits me daily)
        # still matches.
        rf"\bmujhe\b.{{0,15}}\b{_HINDI_HIT_VERBS} hai\b",
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


#: Default retention window for the hallucination log, enforced by
#: `purge_stale_events`, not automatically by `append_events`. 90 days
#: balances two needs: the log carries verbatim, potentially sensitive user
#: text (abuse, immigration status, debt disclosures) that should not sit
#: around indefinitely, while still covering a normal release/eval cycle
#: long enough to be useful as a labelled dataset. A deployment that needs a
#: shorter window can pass its own `max_age_days` to `purge_stale_events`.
DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS = 90

#: Owner-only file mode the log is created (and re-asserted) with, since it
#: can carry verbatim, sensitive user text — see `HallucinationEvent`.
_LOG_FILE_MODE = 0o600


class HallucinationEvent(BaseModel):
    """A record of citations the validator refused to render.

    This intentionally keeps `question` and `claim_text` verbatim rather than
    hashing or redacting them: the whole point of the log is to let a human
    look at *why* a citation was hallucinated, and a hashed or truncated
    question cannot be diagnosed or turned into an eval case. The trade-off
    this makes, and why it is the right one for this product, is written up
    in the accompanying task report rather than re-argued in code comments.

    Because the payload can be sensitive, two controls apply at the storage
    layer instead of by weakening the payload: `append_events` creates (and
    re-asserts on every write) owner-only (`0600`) permissions on the log
    file, and `purge_stale_events` enforces a retention window — by default
    `DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS` days — so the log does not
    grow into an unbounded, indefinitely-retained store of disclosures. The
    log's path is expected to come from `Settings.hallucination_log_path`
    (configurable per deployment, e.g. onto a restricted-access volume)
    rather than a literal hard-coded in a call site.
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
    """Append `events` to the JSONL log at `path`. Returns the number written.

    The file is created (if absent) with owner-only `0600` permissions, and
    that mode is re-asserted after every write even if the file already
    existed with looser permissions — this log can carry verbatim user text,
    so it must never be group- or world-readable regardless of the
    filesystem's default umask.
    """
    if not events:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(
        path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, _LOG_FILE_MODE
    )
    with os.fdopen(file_descriptor, "a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.model_dump()) + "\n")
    path.chmod(_LOG_FILE_MODE)
    return len(events)


def _event_is_within_retention(line: str, cutoff: datetime) -> bool:
    """Return whether the JSONL `line`'s timestamp is at or after `cutoff`.

    A line that cannot be parsed (malformed JSON, missing/unparsable
    timestamp) is treated as NOT within retention: retention is a safety
    mechanism, and an entry nobody can date should not be the one exception
    that survives it indefinitely.
    """
    try:
        timestamp = datetime.fromisoformat(json.loads(line)["timestamp"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    return timestamp >= cutoff


def purge_stale_events(
    path: Path, max_age_days: int = DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS
) -> int:
    """Remove events older than `max_age_days` from the JSONL log at `path`.

    Returns the number of events removed (0 if the file is missing, empty,
    or every event is still within the retention window). Rewrites the file
    in place, keeping only the lines that pass, and re-asserts owner-only
    permissions on the result. Intended to be run periodically (e.g. from an
    operational cron/maintenance job) rather than on every `append_events`
    call, so a single log write stays a single, cheap append.
    """
    if not path.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in raw_lines if line.strip()]
    kept = [line for line in lines if _event_is_within_retention(line, cutoff)]
    removed = len(lines) - len(kept)
    if removed == 0:
        return 0
    file_descriptor = os.open(
        path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, _LOG_FILE_MODE
    )
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
        for line in kept:
            handle.write(line + "\n")
    path.chmod(_LOG_FILE_MODE)
    return removed
