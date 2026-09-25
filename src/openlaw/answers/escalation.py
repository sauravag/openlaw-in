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

This regex is deliberately only half of crisis routing. A second,
complementary layer using the CLASSIFIER-role model lives in
`openlaw.answers.crisis_classifier` — it catches euphemism, third-person
reports, and non-Latin scripts/other Indian languages that no realistic
regex set reaches. `crisis_classifier.detect_crisis_with_classifier` runs
this module's `detect_crisis` first and only falls through to the model when
it is silent; see that module's docstring for the full rationale and its
fail-safe behaviour when the model call fails.

Every dropped citation is logged. That log is both a safety metric — how
often is the system inventing citations, and for what kinds of questions —
and a labelled evaluation dataset produced free by production traffic. See
`build_hallucination_events` for what is and is not persisted, and why.
"""

import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openlaw.answers.validate import RejectedClaim
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

# Hindi crisis responses (Task 6 fix round, H3). STATIC, hand-authored pairs
# -- never a live model translation -- for the same reason
# `openlaw.api.refusal._REFUSAL_DETAIL` is static: this is the text that
# tells someone which number to call, and a translation failure (or a
# mistranslated number) is a worse outcome than staying in English. Every
# phone number below is written in plain ASCII digits (0-9), not Devanagari
# numerals (०-९) -- this matches how phone numbers are actually printed in
# Hindi-language government and NALSA material. (Final review, L2: an
# earlier version of this comment pointed at `web/lib/helplineNumbers.ts`'s
# digit-matching regex as the reason -- that file was DELETED in the Task 6
# C2 fix, which replaced prose pattern-matching with the structured
# `Helpline`/`helplines` field below. The ASCII-digit rule is enforced today
# solely by `tests/answers/test_helpline_consistency.py`, which renders
# these strings and fails if a number ever stops appearing in ASCII form.)
#
# Best-effort Hindi pending a native-speaker review pass, same caveat the
# project already carries for `_REFUSAL_DETAIL` and for glossary
# translations without one yet -- flagged, not claimed verified.
_CRISIS_RESPONSES_HI: dict[CrisisKind, str] = {
    CrisisKind.SELF_HARM: (
        "अगर आप खुद को नुकसान पहुँचाने के बारे में सोच रहे हैं, तो अभी किसी से बात करें। "
        "टेली-मानस निःशुल्क है और 14416 पर चौबीसों घंटे उपलब्ध है। "
        "आपको यह अकेले नहीं झेलना है।"
    ),
    CrisisKind.TRAFFICKING: (
        "अगर कोई तुरंत खतरे में है, तो 112 पर कॉल करें। "
        "बच्चों के लिए हेल्पलाइन 1098 है और महिलाओं के लिए 181। "
        "दोनों निःशुल्क हैं और हर समय उपलब्ध हैं।"
    ),
    CrisisKind.CUSTODIAL_ABUSE: (
        "अगर किसी को हिरासत में नुकसान पहुँचाया जा रहा है, तो यह तुरंत ध्यान देने योग्य है। "
        "112 पर कॉल करें, और राष्ट्रीय मानवाधिकार आयोग से 14433 पर संपर्क करें। "
        "मुफ़्त कानूनी सहायता वकील के लिए तुरंत 15100 पर अनुरोध किया जा सकता है।"
    ),
    CrisisKind.DOMESTIC_VIOLENCE: (
        "अगर आप अभी खतरे में हैं, तो 112 पर कॉल करें। "
        "महिला हेल्पलाइन 181 है, और पुलिस महिला हेल्पलाइन 1091 है। "
        "दोनों निःशुल्क हैं और चौबीसों घंटे उपलब्ध हैं।"
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


class Helpline(BaseModel):
    """One phone number safe to render as a tap-to-call `tel:` link, with a
    short label naming what it is for.

    Task 6 fix round, C2. A prior version had the frontend pattern-match a
    fixed, hand-copied allowlist of numbers (`web/lib/helplineNumbers.ts`)
    against the free-text `crisis_response`/`escalation_footer` prose to
    decide what to linkify -- reviewed as correct-but-fragile: it could never
    display a WRONG number, but it depended on every number in every crisis
    string staying in ASCII digits forever, including under future
    localisation, with nothing enforcing that except a test. This model is
    the fix: the backend says explicitly which numbers are helplines and
    what to call them, and the frontend renders tap-to-call links straight
    from this list, never by scanning prose for digits.
    """

    model_config = ConfigDict(frozen=True)

    number: str
    label: str


#: (label in English, label in Hindi) for every phone number this module or
#: `openlaw.api.refusal` ever cites. Static and hand-authored, same
#: "pending native-speaker review" caveat as the crisis prose itself --
#: see the Hindi crisis-response dict's comment below.
_HELPLINE_LABELS: dict[str, tuple[str, str]] = {
    "112": ("Emergency", "आपातकालीन सेवा"),
    "181": ("Women's helpline", "महिला हेल्पलाइन"),
    "1091": ("Police women's helpline", "पुलिस महिला हेल्पलाइन"),
    "1098": ("Child helpline", "बाल हेल्पलाइन"),
    "14416": ("Tele-MANAS (mental health support)", "टेली-मानस (मानसिक स्वास्थ्य सहायता)"),
    "14433": ("National Human Rights Commission", "राष्ट्रीय मानवाधिकार आयोग"),
    NALSA_HELPLINE: ("Free legal aid (NALSA)", "मुफ़्त कानूनी सहायता (NALSA)"),
}

#: Which numbers `crisis_response` actually cites for each category -- kept
#: as data, one place, rather than re-deriving it from the prose strings
#: (which `tests/answers/test_helpline_consistency.py` cross-checks stay in
#: sync with the labels above and the frontend's own former allowlist).
_CRISIS_HELPLINE_NUMBERS: dict[CrisisKind, tuple[str, ...]] = {
    CrisisKind.SELF_HARM: ("14416",),
    CrisisKind.TRAFFICKING: ("112", "1098", "181"),
    CrisisKind.CUSTODIAL_ABUSE: ("112", "14433", NALSA_HELPLINE),
    CrisisKind.DOMESTIC_VIOLENCE: ("112", "181", "1091"),
}


def helpline_for(number: str, language: str = "en") -> Helpline:
    """Return the structured `Helpline` for a known `number`, labelled in
    `language`. Shared by this module and `openlaw.api.refusal` so a label
    is written once, not duplicated per caller."""
    label_en, label_hi = _HELPLINE_LABELS[number]
    return Helpline(number=number, label=label_hi if language == "hi" else label_en)


def crisis_helplines(kind: CrisisKind, language: str = "en") -> tuple[Helpline, ...]:
    """Return the structured, tap-to-call numbers `crisis_response(kind)`
    cites, in `language` (Task 6 fix round, C2)."""
    return tuple(
        helpline_for(number, language) for number in _CRISIS_HELPLINE_NUMBERS[kind]
    )


def nalsa_helpline(language: str = "en") -> Helpline:
    """The one number every `escalation_footer` mentions, structured."""
    return helpline_for(NALSA_HELPLINE, language)


def crisis_response(kind: CrisisKind, language: str = "en") -> str:
    """Return the helpline-first response for `kind`, in `language`.

    Falls back to English for any language other than `"hi"` -- same rule as
    `openlaw.api.refusal.refusal_detail`, and for the same reason: only
    English and Hindi have a static, human-reviewable pair today (Plan 4's
    launch gate), and there is never a live-translation fallback for crisis
    text (see the module-level Hindi dict's comment above).
    """
    return _CRISIS_RESPONSES_HI[kind] if language == "hi" else _CRISIS_RESPONSES[kind]


def escalation_footer(state: str | None = None, language: str = "en") -> str:
    """Return the free-legal-aid routing appended to every answer, in `language`.

    Falls back to English for any language other than `"hi"` -- see
    `crisis_response`'s docstring; the same static-pair rule applies here.
    """
    if language == "hi":
        authority = (
            f"{state} राज्य विधिक सेवा प्राधिकरण"
            if state
            else "अपने जिला विधिक सेवा प्राधिकरण"
        )
        return (
            "यह कानूनी जानकारी है, कानूनी सलाह नहीं, और यह आपके मामले को जानने "
            "वाले वकील की जगह नहीं ले सकती।\n\n"
            "भारत में मुफ़्त कानूनी सहायता एक वैधानिक अधिकार है। इसके लिए पात्रता "
            "विधिक सेवा प्राधिकरण अधिनियम, 1987 की धारा 12 में दी गई है — इसमें "
            "महिलाएं और बच्चे, अनुसूचित जाति और अनुसूचित जनजाति के सदस्य, हिरासत "
            "में मौजूद लोग, और वे सभी लोग शामिल हैं जिनकी आय उनके राज्य द्वारा तय "
            "सीमा से कम है।\n\n"
            f"यह जानने के लिए कि आप पात्र हैं या नहीं, {authority} से संपर्क करें, "
            f"या NALSA हेल्पलाइन {NALSA_HELPLINE} पर कॉल करें।"
        )

    authority = (
        f"the {state} State Legal Services Authority"
        if state
        else "your District Legal Services Authority"
    )
    return (
        "This is legal information, not legal advice, and it cannot replace a "
        "lawyer who knows your case.\n\n"
        "Free legal aid is a statutory right in India. Who qualifies is set "
        "out in section 12 of the Legal Services Authorities Act, 1987 — it "
        "covers women and children, members of the Scheduled Castes and "
        "Scheduled Tribes, people in custody, and anyone whose income is "
        "below the limit their State prescribes, among others.\n\n"
        f"To find out whether you qualify, contact {authority}, or call the "
        f"NALSA helpline on {NALSA_HELPLINE}."
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
    question: str, rejected: tuple[RejectedClaim, ...], context: RetrievalContext
) -> tuple[HallucinationEvent, ...]:
    """Return one event per rejected claim. Empty when nothing was rejected.

    Takes `openlaw.answers.validate.rejected_claims(draft, context)` rather
    than the `ValidatedAnswer`: the verbatim text of a refused claim is
    deliberately absent from `ValidatedAnswer` (see `DroppedClaim`), so this
    log-only path is the only thing that ever holds it.
    """
    if not rejected:
        return ()
    now = datetime.now(UTC).isoformat()
    retrieved = tuple(sorted(context.chunk_ids))
    return tuple(
        HallucinationEvent(
            question=question,
            dropped_chunk_ids=claim.unresolvable_chunk_ids,
            retrieved_chunk_ids=retrieved,
            claim_text=claim.text,
            timestamp=now,
        )
        for claim in rejected
    )


def append_jsonl_records(records: Sequence[BaseModel], path: Path) -> int:
    """Append `records` (any frozen pydantic model) to the JSONL log at `path`.

    Returns the number written. The file is created (if absent) with
    owner-only `0600` permissions, and that mode is re-asserted after every
    write even if the file already existed with looser permissions — these
    logs can carry verbatim user text, so they must never be group- or
    world-readable regardless of the filesystem's default umask.

    Shared by `append_events` (`HallucinationEvent`) and
    `openlaw.answers.crisis_classifier.append_classifier_failure_events`
    (`ClassifierFailureEvent`) — both need the identical append-only,
    owner-only-permission JSONL write, and `purge_stale_events` below is
    already generic enough (it only reads a `"timestamp"` field) to work
    unmodified against either log.
    """
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(
        path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, _LOG_FILE_MODE
    )
    with os.fdopen(file_descriptor, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.model_dump()) + "\n")
    path.chmod(_LOG_FILE_MODE)
    return len(records)


def append_events(events: tuple[HallucinationEvent, ...], path: Path) -> int:
    """Append `events` to the JSONL log at `path`. Returns the number written.

    A thin, type-named wrapper over `append_jsonl_records` — see that
    function for the write mechanics and why they must be shared rather than
    duplicated per event type.
    """
    return append_jsonl_records(events, path)


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
