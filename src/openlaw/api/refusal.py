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
regex layer (`openlaw.answers.escalation.detect_crisis`) cannot match most
non-Latin script. Plan 6 Task 10 (ruling 2) narrowed that for Devanagari
with a deliberately narrow, precision-tuned pattern set, and narrowing is
all it did: a Hindi phrasing outside those patterns, and every other Indic
script entirely, still reaches this module with the regex silent. The hedge
below therefore stays exactly as it is. `_looks_non_latin` closes the gap
cheaply, with no model call: when the regex is silent AND the question
contains non-Latin-script characters (a proxy for "this matcher is known not
to fire here"), the caller still gets `GENERIC_SAFETY_NOTE` -- the full set
of helplines, un-triaged by category -- rather than silence.

**The upload surface's refusals go out through the same helper (Plan 6 Task
15).** `POST /documents` can fail five new ways -- too large, unsupported
type, unreadable, too many documents, uploads disabled -- and `DELETE
/documents/{id}` and `/ask` can both 404 on an id the caller does not own.
Every one of those is a terminal response for somebody who may be in danger,
so every one is raised by `raise_refusal` below, which assembles
`crisis_extras` and `refusal_detail` into one body shape shared with the 500
and 422 handlers in `app.py`. There is deliberately no path on that surface
that returns a bare status code.

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

from typing import NoReturn

from fastapi import HTTPException

from openlaw.accounts.notes import RETAINED_NOTES

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
    # Plan 6 Task 15: the upload and conversation surface's own terminal
    # responses. Each one is somebody's last screen -- a woman who has just
    # photographed an FIR on a borrowed phone and been told "no" -- so each
    # goes out through `raise_refusal` below with the full escalation footer
    # and helpline list, exactly like the quota refusal above. Hindi is
    # best-effort pending a native-speaker review pass, the same caveat the
    # rest of this module already carries.
    "upload_too_large": {
        "en": (
            "This file is too large to read. Please upload a smaller file, "
            "or type out the part you have a question about."
        ),
        "hi": (
            "यह फ़ाइल पढ़ने के लिए बहुत बड़ी है। कृपया छोटी फ़ाइल भेजें, या जिस "
            "हिस्से के बारे में सवाल है उसे टाइप कर दें।"
        ),
    },
    "upload_unsupported_type": {
        "en": (
            "This file type is not supported. Please upload a PDF, a Word "
            "(.docx) document, or a plain text file."
        ),
        "hi": (
            "यह फ़ाइल प्रकार समर्थित नहीं है। कृपया PDF, Word (.docx) या सादा "
            "टेक्स्ट फ़ाइल भेजें।"
        ),
    },
    "upload_too_many_pages": {
        "en": (
            "This document has too many pages to read. Please upload only "
            "the pages your question is about."
        ),
        "hi": (
            "इस दस्तावेज़ में पढ़ने के लिए बहुत अधिक पृष्ठ हैं। कृपया केवल वे पृष्ठ "
            "भेजें जिनके बारे में आपका सवाल है।"
        ),
    },
    "upload_no_text": {
        "en": (
            "We could not read any text in this file. If it is a scan or a "
            "photo, please type out what it says instead."
        ),
        "hi": (
            "इस फ़ाइल में हमें कोई टेक्स्ट नहीं मिला। अगर यह स्कैन या फ़ोटो है, तो "
            "कृपया उसमें लिखी बात टाइप कर दें।"
        ),
    },
    "upload_corrupt": {
        "en": (
            "This file could not be opened. Please try uploading it again, "
            "or type out the part you have a question about."
        ),
        "hi": (
            "यह फ़ाइल खोली नहीं जा सकी। कृपया इसे दोबारा भेजें, या जिस हिस्से के "
            "बारे में सवाल है उसे टाइप कर दें।"
        ),
    },
    "uploads_disabled": {
        "en": (
            "File uploads are turned off on this service right now. You can "
            "still ask your question in your own words."
        ),
        "hi": (
            "इस सेवा पर अभी फ़ाइल अपलोड बंद है। आप अपना सवाल अपने शब्दों में "
            "अब भी पूछ सकते हैं।"
        ),
    },
    "too_many_documents": {
        "en": (
            "This conversation already has as many files as it can hold. "
            "Remove one, or start a new conversation."
        ),
        "hi": (
            "इस बातचीत में जितनी फ़ाइलें रखी जा सकती हैं, उतनी पहले से हैं। एक "
            "हटाएँ, या नई बातचीत शुरू करें।"
        ),
    },
    # Returned by a sign-in route when the frontend was built with sign-in
    # but the backend runs without auth (or the reverse). A configuration
    # error, but the person still needs to be told the truth in plain words
    # and handed a helpline: the anonymous product keeps working.
    "auth_unavailable": {
        "en": (
            "Signing in is not available on this service right now. You can "
            "still ask your question without an account."
        ),
        "hi": (
            "इस सेवा पर अभी साइन इन उपलब्ध नहीं है। आप बिना खाते के अपना सवाल "
            "अब भी पूछ सकते हैं।"
        ),
    },
    # Returned by `POST /account/migrate` for a missing, invalid or expired
    # bearer. Migration has no anonymous meaning, so unlike `/ask` there is no
    # fall-back: the person is told plainly, and the callback keeps them signed
    # in regardless (see the frontend's `?migration=failed`).
    "sign_in_required": {
        "en": (
            "We could not confirm your sign-in, so your earlier conversations "
            "were not moved to your account. Please sign in again."
        ),
        "hi": (
            "हम आपके साइन इन की पुष्टि नहीं कर सके, इसलिए आपकी पहले की बातचीत आपके "
            "खाते में नहीं ले जाई गई। कृपया फिर से साइन इन करें।"
        ),
    },
    # `DELETE /account` for a missing, invalid or expired bearer. Unlike the
    # migration copy above, it must say nothing was deleted.
    "account_delete_sign_in_required": {
        "en": (
            "We could not confirm your sign-in, so nothing was deleted. "
            "Please sign in again."
        ),
        "hi": (
            "हम आपके साइन इन की पुष्टि नहीं कर सके, इसलिए कुछ भी नहीं हटाया गया। "
            "कृपया फिर से साइन इन करें।"
        ),
    },
    # `POST /account/migrate` when the verified email already belongs to a
    # different account. Signing in again cannot help, so this must not say so;
    # the earlier material is still in this browser until it expires.
    "account_email_conflict": {
        "en": (
            "This email is already linked to another account, so your earlier "
            "conversations were not moved. They are still in this browser until "
            "they expire."
        ),
        "hi": (
            "यह ईमेल पहले से किसी दूसरे खाते से जुड़ा है, इसलिए आपकी पहले की बातचीत "
            "नहीं ले जाई गई। वह इस ब्राउज़र में तब तक मौजूद है जब तक उसकी अवधि समाप्त "
            "नहीं हो जाती।"
        ),
    },
    # --- Plan 7 Task 12: one entry per authentication / account terminal state.
    # Every sentence that tells a person they can carry on without an account
    # is true only because the anonymous product has no auth dependency.
    # `auth_rate_limited` names the wait of the frontend sign-in limiter
    # (`SIGN_IN_WINDOW_SECONDS` in `web/lib/auth/rateLimit.ts`); a test derives
    # the minutes from that constant. The frontend mirrors each string verbatim
    # in `web/lib/strings.ts`, also pinned by test.
    "auth_link_sent": {
        "en": (
            "If that address can receive email, a sign-in link is on its way. "
            "Check your inbox."
        ),
        "hi": (
            "अगर इस पते पर ईमेल पहुँच सकता है, तो साइन इन लिंक भेजा जा रहा है। "
            "अपना इनबॉक्स देखें।"
        ),
    },
    "auth_link_expired": {
        "en": (
            "This sign-in link has expired. Please ask for a new link and try "
            "again. You can still ask your question without an account."
        ),
        "hi": (
            "इस साइन इन लिंक की अवधि समाप्त हो गई है। कृपया नया लिंक मँगवाकर "
            "दोबारा कोशिश करें। आप बिना खाते के अपना सवाल अब भी पूछ सकते हैं।"
        ),
    },
    "auth_failed": {
        "en": (
            "We could not sign you in just now. Please try again in a little "
            "while. You can still ask your question without an account."
        ),
        "hi": (
            "हम अभी आपको साइन इन नहीं करा सके। कृपया थोड़ी देर बाद फिर कोशिश "
            "करें। आप बिना खाते के अपना सवाल अब भी पूछ सकते हैं।"
        ),
    },
    "auth_rate_limited": {
        "en": (
            "Too many sign-in attempts. Please try again in 10 minutes. You "
            "can still ask your question without an account."
        ),
        "hi": (
            "साइन इन की बहुत ज़्यादा कोशिशें हुईं। कृपया 10 मिनट बाद दोबारा "
            "कोशिश करें। आप बिना खाते के अपना सवाल अब भी पूछ सकते हैं।"
        ),
    },
    "auth_migration_failed": {
        "en": (
            "You are signed in, but your earlier conversations could not be "
            "moved to your account. They are still in this browser until they "
            "expire."
        ),
        "hi": (
            "आप साइन इन हैं, लेकिन आपकी पहले की बातचीत आपके खाते में नहीं ले "
            "जाई जा सकी। वह इस ब्राउज़र में तब तक मौजूद है जब तक उसकी अवधि "
            "समाप्त नहीं हो जाती।"
        ),
    },
    "account_deleted": {
        "en": "Your account has been deleted. " + RETAINED_NOTES[0],
        "hi": "आपका खाता हटा दिया गया है। " + RETAINED_NOTES[1],
    },
    "account_delete_failed": {
        "en": (
            "Your account was not deleted: something went wrong on our side, "
            "and you are still signed in. Please try again in a little while."
        ),
        "hi": (
            "आपका खाता नहीं हटाया गया: हमारी ओर से कुछ गड़बड़ हुई, और आप अब भी "
            "साइन इन हैं। कृपया थोड़ी देर बाद फिर कोशिश करें।"
        ),
    },
    # Returned for a conversation or document the caller does not own, AND
    # for one that does not exist -- deliberately the same response, so the
    # reply cannot be used to enumerate real ids (see `openlaw.db
    # .conversations.AccessDenied`). Never 403: a 403 confirms the id is
    # real.
    "not_found": {
        "en": "We could not find that. It may have been deleted, or it may have expired.",
        "hi": "हमें वह नहीं मिला। हो सकता है वह मिटा दिया गया हो या उसकी अवधि बीत गई हो।",
    },
}

#: The HTTP status each refusal key goes out with. Only "too large" is 413
#: (the one failure that is genuinely about the request entity's size);
#: everything else describing a file we could not use is a 422, and an
#: unowned/absent id is a 404. Kept as data beside the copy above so adding a
#: refusal means adding both halves in one place, and `raise_refusal` can
#: never be called with a key that has no status.
REFUSAL_STATUS: dict[str, int] = {
    "upload_too_large": 413,
    "upload_unsupported_type": 422,
    "upload_too_many_pages": 422,
    "upload_no_text": 422,
    "upload_corrupt": 422,
    "uploads_disabled": 422,
    "too_many_documents": 422,
    "not_found": 404,
    "auth_unavailable": 503,
    # Missing, invalid or expired bearer: 401 (+ `WWW-Authenticate`), never a
    # 503, which would read as "the server is down" to proxies and monitors.
    "sign_in_required": 401,
    "account_email_conflict": 409,
    "account_delete_sign_in_required": 401,
    # Task 12. Statuses mirror what the frontend's analogous responses use;
    # the two success-shaped keys (`auth_link_sent`, `account_deleted`) are
    # carried in a 2xx body, not raised.
    "auth_link_sent": 202,
    "auth_link_expired": 410,
    "auth_failed": 503,
    "auth_rate_limited": 429,
    "auth_migration_failed": 502,
    "account_deleted": 200,
    "account_delete_failed": 502,
}

#: `openlaw.documents.extract.ExtractionError.reason` -> refusal key. The
#: extraction layer deliberately exposes a closed set of reasons rather than
#: a message to parse (see that exception's docstring); this is the single
#: place that set is turned into user-facing copy and a status code, so a new
#: reason surfaces as a `KeyError` at the boundary during development rather
#: than as a bare 500 for a citizen.
EXTRACTION_REFUSAL_KEYS: dict[str, str] = {
    "too_large": "upload_too_large",
    "unsupported_type": "upload_unsupported_type",
    "too_many_pages": "upload_too_many_pages",
    "no_text": "upload_no_text",
    "corrupt": "upload_corrupt",
}


def refusal_body(
    key: str, question: str, state: str | None, language: str | None
) -> dict[str, object]:
    """Return the full JSON body for refusal `key` -- help included, always.

    One function, so "every terminal response delivers help" is a property of
    this module rather than of each call site remembering to assemble it.
    The shape matches `openlaw.api.app._unhandled_error_content` field for
    field (`detail`, `escalation_footer`, `crisis_response`, `helplines`), so
    a client has one error shape to render across the 404, 413, 422 and 500
    paths. `helplines` is serialised to plain dicts here for the same reason
    it is there: `JSONResponse` encodes with the stdlib `json` module, which
    cannot encode a pydantic model.
    """
    footer, crisis, helplines = crisis_extras(question, state, language)
    return {
        "detail": refusal_detail(key, language),
        "escalation_footer": footer,
        "crisis_response": crisis,
        "helplines": [helpline.model_dump() for helpline in helplines],
    }


def raise_refusal(
    key: str,
    question: str = "",
    state: str | None = None,
    language: str | None = None,
    headers: dict[str, str] | None = None,
) -> NoReturn:
    """Raise the HTTPException for refusal `key`, carrying help.

    `question` defaults to the empty string so a path with no question in
    hand (a `DELETE /documents/{id}`) still gets the footer and the NALSA
    number -- a narrower safety net than a question-bearing path, never a
    missing one, exactly as `app._validation_error` documents for a body that
    failed to parse.
    """
    raise HTTPException(
        status_code=REFUSAL_STATUS[key],
        detail=refusal_body(key, question, state, language),
        headers=headers,
    )


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
