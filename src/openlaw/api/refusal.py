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

from openlaw.answers.escalation import crisis_response, detect_crisis, escalation_footer
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


def crisis_extras(question: str, state: str | None) -> tuple[str, str | None]:
    """Return `(escalation_footer, crisis_response)` for ANY refusal path.

    Always returns a non-empty footer. Returns a non-None `crisis_response`
    when the free regex layer fires, or -- the M3 fix -- when it stays
    silent but the question is written in a script that layer cannot match
    at all. See the module docstring for why this stops short of the paid
    CLASSIFIER-role check the main answer path uses.
    """
    footer = escalation_footer(state)
    kind = detect_crisis(question)
    if kind is not None:
        return footer, crisis_response(kind)
    if _looks_non_latin(question):
        return footer, GENERIC_SAFETY_NOTE
    return footer, None


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
