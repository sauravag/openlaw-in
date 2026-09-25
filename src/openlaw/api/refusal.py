"""What every non-2xx `/ask` response carries, in common.

**Every terminal response a caller can receive -- not only a quota refusal --
must run crisis detection and carry the escalation footer.** Task 4's review
(H1, M4) found this applied to the quota refusal alone: the rate-limit 429
and the 500 handler were both bare dead ends, and a woman typing "my husband
beats me" into a rate-limited or momentarily-broken deployment got nothing
but "try again". `crisis_extras` below is the one function every refusal
path calls, so the property holds everywhere by construction rather than by
each call site remembering to reimplement it.

**Crisis detection on a refusal path still cannot afford the paid,
CLASSIFIER-role model call.** The review's M3 asked for "the same crisis
detection the main path uses" (that full two-layer check); this module
does NOT do that literally, and the reason is a cost/abuse argument, not an
oversight -- flagged explicitly in the Task 4 follow-up report. A refusal
happens precisely when a caller has exhausted or exceeded their allowance;
calling a paid model on every subsequent request from that same caller would
let them spend money indefinitely simply by continuing to POST after being
refused, with no quota gate in front of it (the gate is what just refused
them). What IS adopted from M3 is the actual gap it identified: the free
regex layer (`openlaw.answers.escalation.detect_crisis`) is blind to
Devanagari and other Indic scripts. `_looks_non_latin` closes that gap
cheaply, with no model call: when the regex is silent AND the question
contains non-Latin-script characters (a proxy for "this matcher is known not
to fire here"), the caller still gets `GENERIC_SAFETY_NOTE` -- the full set
of helplines, un-triaged by category -- rather than silence.

**Refusal `detail` strings are static and bilingual, not translated.**
`refusal_detail` selects from a fixed, pre-written English/Hindi pair rather
than calling the translation pipeline: an error path must not depend on the
very system that may have just failed (a model outage is exactly when a 500
fires). Hindi and English are the two languages Plan 4's review cleared for
launch; other `SUPPORTED_LANGUAGES` fall back to English here until their
own static pairs are written -- a deliberate, narrower promise than the
answer pipeline's full language coverage, and documented as such rather than
silently mistranslating.
"""

from openlaw.answers.escalation import (
    Helpline,
    crisis_helplines,
    crisis_response,
    detect_crisis,
    escalation_footer,
    helpline_for,
    nalsa_helpline,
)
from openlaw.language.detect import SCRIPT_RANGES

#: Shown on a refusal when the free regex found nothing but the question is
#: written in a script the regex cannot match at all (see the module
#: docstring). Deliberately not attributed to one crisis category -- with no
#: reliable classification, the full set of helplines is safer than a guess.
GENERIC_SAFETY_NOTE = (
    "If you are in immediate danger, call 112. The women's helpline is 181, "
    "the police women's helpline is 1091, the child helpline is 1098, and "
    "Tele-MANAS (mental health support) is 14416. All are free and "
    "available 24 hours."
)

#: Hindi pair for `GENERIC_SAFETY_NOTE` (Task 6 fix round, H3) -- static,
#: hand-authored, ASCII digits, same rules as
#: `openlaw.answers.escalation._CRISIS_RESPONSES_HI`. Worth having precisely
#: because this note fires when the question LOOKS non-Latin-script -- the
#: single case most likely to actually be a Hindi speaker -- yet an explicit
#: `language="hi"` still wins over the script heuristic, same as everywhere
#: else. Best-effort pending a native-speaker review pass.
_GENERIC_SAFETY_NOTE_HI = (
    "अगर आप तुरंत खतरे में हैं, तो 112 पर कॉल करें। महिला हेल्पलाइन 181 है, "
    "पुलिस महिला हेल्पलाइन 1091 है, बाल हेल्पलाइन 1098 है, और टेली-मानस "
    "(मानसिक स्वास्थ्य सहायता) 14416 है। सभी निःशुल्क हैं और चौबीसों घंटे "
    "उपलब्ध हैं।"
)

#: The numbers `GENERIC_SAFETY_NOTE`/`_GENERIC_SAFETY_NOTE_HI` actually cite,
#: as data (Task 6 fix round, C2) -- mirrors
#: `escalation._CRISIS_HELPLINE_NUMBERS`'s role for the categorised prose.
_GENERIC_SAFETY_NUMBERS: tuple[str, ...] = ("112", "181", "1091", "1098", "14416")


def generic_safety_helplines(language: str = "en") -> tuple[Helpline, ...]:
    """Structured, tap-to-call numbers for `GENERIC_SAFETY_NOTE`, in `language`."""
    return tuple(helpline_for(number, language) for number in _GENERIC_SAFETY_NUMBERS)


def _looks_non_latin(text: str) -> bool:
    """Return True if `text` contains any character in an Indic script block.

    Reuses `openlaw.language.detect.SCRIPT_RANGES` -- the same ranges the
    main detection path uses -- rather than a second, drifting copy of the
    same table.
    """
    return any(
        start <= ord(character) <= end
        for character in text
        for _, start, end in SCRIPT_RANGES
    )


def crisis_extras(
    question: str, state: str | None, language: str | None = None
) -> tuple[str, str | None, tuple[Helpline, ...]]:
    """Return `(escalation_footer, crisis_response, helplines)` for ANY
    refusal path.

    Always returns a non-empty footer and at least one helpline (the NALSA
    number the footer itself always cites). Returns a non-None
    `crisis_response`, plus its category-specific helplines, when the free
    regex layer fires, or -- the M3 fix -- when it stays silent but the
    question is written in a script that layer cannot match at all. See the
    module docstring for why this stops short of the paid CLASSIFIER-role
    check the main answer path uses.

    `language` selects between the static English/Hindi pairs (Task 6 fix
    round, H3) -- same "explicit caller choice wins" rule as everywhere else
    in this codebase, never a live translation. `None` and anything other
    than `"hi"` fall back to English.

    `helplines` is structured data (Task 6 fix round, C2), not prose the
    caller must pattern-match: the frontend renders tap-to-call links
    directly from it rather than scanning `crisis_response`/`footer` text
    for digits.
    """
    lang = language or "en"
    footer = escalation_footer(state, lang)
    base_helplines = (nalsa_helpline(lang),)
    kind = detect_crisis(question)
    if kind is not None:
        return footer, crisis_response(kind, lang), crisis_helplines(kind, lang) + base_helplines
    if _looks_non_latin(question):
        note = _GENERIC_SAFETY_NOTE_HI if lang == "hi" else GENERIC_SAFETY_NOTE
        return footer, note, generic_safety_helplines(lang) + base_helplines
    return footer, None, base_helplines


#: Static, pre-written refusal copy -- English and Hindi only (see the
#: module docstring). Best-effort Hindi pending a native-speaker review pass,
#: same caveat the project already carries for glossary translations
#: (`openlaw.language.glossary`) and for every language without one yet.
_REFUSAL_DETAIL: dict[str, dict[str, str]] = {
    "quota_exceeded": {
        "en": "Free allowance used for this period.",
        "hi": "इस अवधि के लिए मुफ़्त उपयोग सीमा समाप्त हो चुकी है।",
    },
    "rate_limited": {
        "en": "Too many requests. Please wait a moment and try again.",
        "hi": "बहुत अधिक अनुरोध भेजे गए हैं। कृपया थोड़ी देर बाद फिर से प्रयास करें।",
    },
    "server_error": {
        "en": "Something went wrong on our end. Please try again.",
        "hi": "हमारी तरफ़ से कुछ गड़बड़ हो गई। कृपया दोबारा प्रयास करें।",
    },
}


def refusal_detail(key: str, language: str | None) -> str:
    """Return the static refusal string for `key`, in `language`.

    Falls back to English for any language without a static pair yet --
    never to a model translation (see the module docstring). `language` is
    expected to already be validated (see
    `openlaw.api.schemas.AskRequest`'s `language` allowlist); an
    unrecognised value falls back to English rather than raising, since this
    function runs on error paths that must not themselves be able to fail.
    """
    translations = _REFUSAL_DETAIL[key]
    return translations.get(language or "en", translations["en"])
