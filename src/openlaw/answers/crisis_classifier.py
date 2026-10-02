"""Model-based crisis classification — the second layer of crisis routing.

`openlaw.answers.escalation.detect_crisis` is a fast, free, deterministic
regex match, and it stays first in line: no model call, no cost, no latency,
no dependency on an API key at all. But no practical regex set reaches every
way a person in danger might phrase it — see that module's own docstring for
why the regex is deliberately recall-biased even so. A reviewer ran the
current patterns directly and measured concrete gaps: non-Latin scripts
entirely (Devanagari, e.g. "मुझे मारता है", "मरना चाहता हूँ" -- Plan 6 Task 10
ruling 2 has since added a narrow, precision-tuned Devanagari set for
exactly those two phrasings, which narrows this gap without closing it and
leaves every other Indic script untouched), SMS-compressed
and misspelled transliteration ("mera pati mujhe marta h"), euphemism ("I
want to end it all", "he raises his hand at me"), third-person reports ("my
sister is being beaten", "my sister was beaten by her husband"), and every
Indian language other than Hindi in transliteration (Tamil, Bengali, Telugu,
Marathi, ...). For a legal-aid service, a person in danger typing any of
those today gets a statutory citation instead of a helpline.

This module asks the CLASSIFIER-role model (see
`openlaw.answers.models.ModelRole.CLASSIFIER`, default `claude-haiku-4-5` —
cheap by design, matching this product's per-question cost budget) to catch
exactly that: euphemism, third-person framing, script, and language
variation that no realistic regex set covers. It returns one of the existing
`CrisisKind` values or `None` — never a parallel taxonomy of its own — so
every downstream consumer of a crisis result (routing, responses, tests)
stays oblivious to which layer produced it.

`detect_crisis_with_classifier` wires both layers together in a fixed order:

1. Run the regex first. If it fires, return immediately — the classifier is
   never asked, so a crisis question caught this way costs nothing extra and
   needs no API key at all.
2. Only when the regex is silent does the classifier get asked, keeping the
   common case (an ordinary legal question) to a single cheap model call.

Fail-safe direction (revised): `classify_crisis` raises on any problem — a
failed call (network error, timeout, or anything else the adapter surfaces)
or a response that cannot be parsed into a known `CrisisKind` or "none". It
does not guess. `detect_crisis_with_classifier` decides what "the classifier
could not be consulted" should mean for routing, and here is the reasoning
behind that decision, since an earlier version of this module got it wrong
in the other direction:

An earlier revision treated any classifier failure as a crisis (specifically
`CrisisKind.SELF_HARM`), reasoning that a missed crisis costs more than an
unwanted helpline. That reasoning holds for one question, but not at scale:
the regex has already run and found nothing by the time the classifier is
even asked, so a classifier *outage* — an expired key, a rate limit, a
transient network failure — would turn **every** ordinary question asked
during that outage into a fabricated self-harm crisis, for as long as the
outage lasts. Someone asking how to register a property would get suicide
helplines and no legal answer at all. That is a worse failure mode than the
one it was trying to prevent, and unlike a single missed crisis it is
silent, sustained, and affects every user, not one.

The decisive fact: **every answer already carries the standard escalation
footer regardless of crisis status** (see `escalation_footer`, called
unconditionally in `openlaw.answers.generate`). A question the classifier
could not be consulted about is therefore not a question left without
help — it still gets the NALSA helpline (15100) and DLSA routing on the
ordinary path. So on a classifier failure, `detect_crisis_with_classifier`
now proceeds on the regex verdict alone (which, having reached the
classifier step at all, was already "no crisis found") rather than
declaring a crisis it has no evidence for. The failure is never silently
swallowed, though: it is logged at `ERROR` (loud enough for an operator
watching ordinary logs to notice) and returned as a `ClassifierFailureEvent`,
which `openlaw.answers.generate.persist_answer_events` (called by
`answer_question` on every turn) appends to
`Settings.classifier_failure_log_path` via
`append_classifier_failure_events` — mirroring
`openlaw.answers.escalation.HallucinationEvent`'s "build now, persist later"
shape, so that a silent, sustained outage is countable and discoverable
after the fact even if nobody was watching the logs in the moment.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from openlaw.answers.escalation import (
    CRISIS_KIND_BY_VALUE,
    CrisisKind,
    append_jsonl_records,
    detect_crisis,
)
from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """\
You are a safety classifier for a free legal-aid service used by ordinary \
people across India, writing in any Indian language, script, or \
transliteration — including Devanagari and other Indic scripts, \
SMS-style/romanised spelling, and code-mixed Hindi-English (Hinglish).

Decide whether the user's message signals one of these situations, even when \
it is expressed euphemistically, in the third person (describing what is \
happening to someone else, such as a sibling or friend), or in a language or \
script other than English:

- domestic_violence: violence, hitting, beating, or threats from a spouse, \
partner, or in-laws — including euphemisms such as "he raises his hand at \
me" or "he loses his temper physically" — whether the speaker is the victim \
or is describing it happening to someone else.
- custodial_abuse: violence or torture by police, or while in custody or a \
lockup.
- trafficking: being sold, trafficked, or forced into labour, prostitution, \
or marriage.
- self_harm: suicidal thoughts, wanting to die, or self-harm, including \
euphemisms such as "I want to end it all" or "I don't want to be here \
anymore".

Respond with strict JSON only, matching this shape exactly:
{"crisis": "domestic_violence" | "custodial_abuse" | "trafficking" | \
"self_harm" | "none"}

Use "none" only when the message shows no sign of any of the above. When \
genuinely uncertain, prefer naming the closest category over "none": missing \
a real crisis is far worse than one unnecessary helpline."""

#: The classifier only ever emits one short JSON object, but 32 tokens (the
#: previous value) sat below the documented ~256 guidance for a
#: classification call and left no headroom: a truncated response is
#: unparseable, and `detect_crisis_with_classifier` reads an unparseable
#: response as a failure -- i.e. as "no crisis". Cheap insurance against a
#: silent miss on the one call path that routes a person in danger.
MAX_CLASSIFIER_TOKENS = 256

CRISIS_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "crisis": {
            "type": "string",
            "enum": [*sorted(CRISIS_KIND_BY_VALUE), "none"],
            "description": (
                "The crisis category signalled by the message, or 'none' if "
                "it shows no sign of one."
            ),
        }
    },
    "required": ["crisis"],
    "additionalProperties": False,
}


def _parse_classification(payload: str) -> CrisisKind | None:
    """Parse the classifier's JSON response into a `CrisisKind` or `None`.

    Raises `ValueError` when the payload is not valid JSON, is not an
    object, is missing "crisis", or names anything other than a known
    `CrisisKind` value or "none". Never guesses: an unrecognised response is
    exactly the kind of failure `detect_crisis_with_classifier` must fail
    safe on, not silently coerce to "no crisis".
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"classifier returned malformed JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("classifier response was not a JSON object")
    raw = data.get("crisis")
    if raw == "none":
        return None
    if isinstance(raw, str) and raw in CRISIS_KIND_BY_VALUE:
        return CRISIS_KIND_BY_VALUE[raw]
    raise ValueError(f"classifier returned an unrecognised crisis value: {raw!r}")


def classify_crisis(
    text: str, adapter: LLMAdapter, registry: ModelRegistry
) -> CrisisKind | None:
    """Ask the CLASSIFIER-role model whether `text` signals a crisis.

    Raises on any problem instead of guessing — see the module docstring for
    why the fail-safe decision belongs to the caller
    (`detect_crisis_with_classifier`), not here.
    """
    completion = adapter.complete(
        model=registry.for_role(ModelRole.CLASSIFIER),
        system=CLASSIFIER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        output_schema=CRISIS_CLASSIFIER_SCHEMA,
        max_tokens=MAX_CLASSIFIER_TOKENS,
    )
    return _parse_classification(completion.text)


class ClassifierFailureEvent(BaseModel):
    """A record of one crisis-classifier call that could not be completed.

    Kept verbatim (the question text and the error) for the same reason
    `openlaw.answers.escalation.HallucinationEvent` keeps its payload
    verbatim: diagnosing *why* a sustained outage is happening needs the
    actual failing input and error, not a hash or a bare counter. This event
    does not, itself, change crisis routing — see the module docstring for
    why a classifier failure proceeds on the regex verdict rather than
    declaring a crisis. It exists purely so the failure is countable and
    diagnosable after the fact, via `append_classifier_failure_events`.
    """

    model_config = ConfigDict(frozen=True)

    question: str
    error: str
    timestamp: str


def build_classifier_failure_event(
    question: str, error: BaseException
) -> ClassifierFailureEvent:
    """Build the recordable event for one failed classifier call."""
    return ClassifierFailureEvent(
        question=question,
        error=f"{type(error).__name__}: {error}",
        timestamp=datetime.now(UTC).isoformat(),
    )


def append_classifier_failure_events(
    events: tuple[ClassifierFailureEvent, ...], path: Path
) -> int:
    """Append `events` to the JSONL log at `path`. Returns the number written.

    Delegates to `escalation.append_jsonl_records` — the same owner-only
    permission, append-only write already used for `HallucinationEvent`; a
    classifier-failure log carries the same verbatim, potentially sensitive
    question text and needs the same guarantees. `escalation.
    purge_stale_events` (already generic) applies to this log unmodified.
    """
    return append_jsonl_records(events, path)


class CrisisDetection(BaseModel):
    """The outcome of running both crisis-detection layers once.

    `failure` is populated only when the classifier could not be consulted
    at all (a failed call, or an unparseable/unrecognised response); it is
    always `None` when `crisis` came from the regex or from a successful
    classifier call. A classifier failure never sets `crisis` itself — see
    the module docstring for why.
    """

    model_config = ConfigDict(frozen=True)

    crisis: CrisisKind | None
    failure: ClassifierFailureEvent | None = None


def detect_crisis_with_classifier(
    text: str, adapter: LLMAdapter, registry: ModelRegistry
) -> CrisisDetection:
    """Run both crisis-detection layers, in order: regex first, then classifier.

    Never raises. On a classifier failure, proceeds on the regex verdict
    (already "no crisis" by the time the classifier is reached) rather than
    guessing at a crisis category with no evidence — see the module
    docstring for the full reasoning. The failure is logged loudly at
    `ERROR` and returned as `CrisisDetection.failure` so a caller can persist
    it with `append_classifier_failure_events`.
    """
    regex_hit = detect_crisis(text)
    if regex_hit is not None:
        return CrisisDetection(crisis=regex_hit)
    try:
        return CrisisDetection(crisis=classify_crisis(text, adapter, registry))
    except Exception as error:
        logger.error(
            "crisis classifier call failed; proceeding on the regex verdict "
            "(no crisis) rather than declaring an unevidenced crisis -- "
            "every answer still carries the standard escalation footer. "
            "error=%r",
            error,
            exc_info=True,
        )
        return CrisisDetection(
            crisis=None, failure=build_classifier_failure_event(text, error)
        )
