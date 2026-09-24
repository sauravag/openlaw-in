"""Model-based crisis classification — the second layer of crisis routing.

`openlaw.answers.escalation.detect_crisis` is a fast, free, deterministic
regex match, and it stays first in line: no model call, no cost, no latency,
no dependency on an API key at all. But no practical regex set reaches every
way a person in danger might phrase it — see that module's own docstring for
why the regex is deliberately recall-biased even so. A reviewer ran the
current patterns directly and measured concrete gaps: non-Latin scripts
entirely (Devanagari, e.g. "मुझे मारता है", "मरना चाहता हूँ"), SMS-compressed
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

Fail-safe direction: `classify_crisis` raises on any problem — a failed call
(network error, timeout, or anything else the adapter surfaces) or a
response that cannot be parsed into a known `CrisisKind` or "none". It does
not guess. `detect_crisis_with_classifier` is what decides what "the
classifier could not be consulted" should mean for routing: a missed crisis
can cost a life, while an unnecessary helpline costs a reader one extra
paragraph, so a classifier failure is treated as a crisis, never as "safe" —
it resolves to `CLASSIFIER_FAILURE_FALLBACK` (`CrisisKind.SELF_HARM`), the
same category `detect_crisis` already falls back to when a message
plausibly matches more than one kind (see `escalation._SEVERITY_ORDER`).
That keeps one "most urgent wins" policy for both kinds of uncertainty —
ambiguity between categories, and not knowing the category at all — instead
of inventing a second one just for this module.
"""

import json
import logging
from typing import Any

from openlaw.answers.escalation import CrisisKind, detect_crisis
from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole

logger = logging.getLogger(__name__)

#: Fail-safe category returned when the classifier cannot be consulted at
#: all (the call raised, or its response could not be parsed) — see the
#: module docstring for why this specific category, and not a bare "treat as
#: unsafe" flag, is the right shape for the fallback.
CLASSIFIER_FAILURE_FALLBACK: CrisisKind = CrisisKind.SELF_HARM

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

_CRISIS_BY_VALUE: dict[str, CrisisKind] = {kind.value: kind for kind in CrisisKind}

#: Small and cheap: the classifier's entire job is to pick one of five words.
MAX_CLASSIFIER_TOKENS = 32

CRISIS_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "crisis": {
            "type": "string",
            "enum": [*sorted(_CRISIS_BY_VALUE), "none"],
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
    if isinstance(raw, str) and raw in _CRISIS_BY_VALUE:
        return _CRISIS_BY_VALUE[raw]
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


def detect_crisis_with_classifier(
    text: str, adapter: LLMAdapter, registry: ModelRegistry
) -> CrisisKind | None:
    """Run both crisis-detection layers, in order: regex first, then classifier.

    Never raises: a classifier failure fails toward showing help (see the
    module docstring) rather than crashing the request or silently letting
    the question through as ordinary legal information.
    """
    regex_hit = detect_crisis(text)
    if regex_hit is not None:
        return regex_hit
    try:
        return classify_crisis(text, adapter, registry)
    except Exception:
        logger.warning(
            "crisis classifier failed; failing safe to %s",
            CLASSIFIER_FAILURE_FALLBACK,
            exc_info=True,
        )
        return CLASSIFIER_FAILURE_FALLBACK
