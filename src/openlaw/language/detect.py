"""Language detection for incoming questions.

Script ranges are tried first because they are deterministic for Indic scripts —
Devanagari is Devanagari. Statistical detection is the fallback for Latin script,
where a short question is easy to misclassify.

An unsupported language falls back to English rather than being claimed as
supported: answering in a language we cannot serve well is worse than answering
in English.

Known limitation (see task-1-report.md for the full account): a question typed
in Latin-script transliteration of an Indian language (e.g. Hindi written as
"mera pati mujhe maarta hai") has no Indic-script characters for
`detect_by_script` to key on, so it falls through to the statistical detector.
`langdetect` has no Hindi-in-Latin-script model, so such text is either
misclassified as an unrelated Latin-script language or, more often, lands on a
code outside `SUPPORTED_LANGUAGES` and is silently treated as English. That is
a real, unresolved gap for transliterated input and is called out explicitly
in the task report rather than papered over here.
"""

DEFAULT_LANGUAGE = "en"

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "or": "Odia",
    "ur": "Urdu",
    "as": "Assamese",
}

# (language code, Unicode block start, Unicode block end)
SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("hi", 0x0900, 0x097F),  # Devanagari — shared with Marathi
    ("bn", 0x0980, 0x09FF),  # Bengali — shared with Assamese
    ("pa", 0x0A00, 0x0A7F),  # Gurmukhi
    ("gu", 0x0A80, 0x0AFF),  # Gujarati
    ("or", 0x0B00, 0x0B7F),  # Odia
    ("ta", 0x0B80, 0x0BFF),  # Tamil
    ("te", 0x0C00, 0x0C7F),  # Telugu
    ("kn", 0x0C80, 0x0CFF),  # Kannada
    ("ml", 0x0D00, 0x0D7F),  # Malayalam
    ("ur", 0x0600, 0x06FF),  # Arabic script
)

_MIN_SCRIPT_CHARS = 3


def detect_by_script(text: str) -> str | None:
    """Return the language implied by `text`'s script, or None when Latin.

    Devanagari resolves to Hindi and Bengali script to Bengali. Marathi and
    Assamese share those scripts and are distinguished only by an explicit user
    choice, which the interface supplies.
    """
    if not text:
        return None

    counts: dict[str, int] = {}
    for character in text:
        code = ord(character)
        for language, start, end in SCRIPT_RANGES:
            if start <= code <= end:
                counts[language] = counts.get(language, 0) + 1
                break

    if not counts:
        return None
    language, count = max(counts.items(), key=lambda item: item[1])
    return language if count >= _MIN_SCRIPT_CHARS else None


def detect_language(text: str) -> str:
    """Return the ISO code for `text`, falling back to English."""
    if not text or not text.strip():
        return DEFAULT_LANGUAGE

    by_script = detect_by_script(text)
    if by_script:
        return by_script

    try:
        from langdetect import detect  # imported lazily; slow to load

        detected = detect(text)
    except Exception:
        return DEFAULT_LANGUAGE

    return detected if detected in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
