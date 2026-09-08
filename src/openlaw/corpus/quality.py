"""Quality filters for measured defects in the vaquill judgment corpus.

Two defects are handled:

1. Judge extraction emits parser noise. Observed real values include
   ``['which']`` and ``['SH. ANIL KUMAR SISODIA', 'vide']``.
2. English Supreme Court text carries stray Supreme Court Reports margin letters
   (``A``-``H``) inline, an artifact of the reporter's page layout.

Margin-letter removal is conservative by design. ``Section 302 A`` and
``Exhibit A`` are legitimate, so a letter is removed only when isolated between
lowercase words and not preceded by a legal-reference keyword. Callers must
retain ``text_original`` unmodified for audit.
"""

import re

# Lowercase function words that indicate a mis-parsed judge field.
_JUDGE_NOISE: frozenset[str] = frozenset(
    {
        "which", "vide", "and", "the", "of", "for", "with", "that", "this",
        "hon", "honble", "before", "coram", "per", "dated", "order", "judgment",
    }
)
_MIN_JUDGE_LENGTH = 3

# An isolated A-H between two lowercase words: the SCR margin-marker shape.
_MARGIN_LETTER = re.compile(r"(?<=[a-z])\s+([A-H])\s+(?=[a-z])")
# Keywords after which a lone capital is a real reference, not a margin marker.
_REFERENCE_KEYWORDS = re.compile(
    r"\b(section|sections|exhibit|annexure|annexures|schedule|part|clause|"
    r"chapter|appendix|form|table|article)\s+(?:\d+\s+)?[A-H]\b",
    re.IGNORECASE,
)


def is_plausible_judge_name(name: str | None) -> bool:
    """Return True when `name` looks like a judge's name rather than parser noise."""
    if not name:
        return False
    candidate = name.strip()
    if len(candidate) < _MIN_JUDGE_LENGTH:
        return False
    if candidate.lower().strip(".") in _JUDGE_NOISE:
        return False
    # A real name carries at least one uppercase letter; noise is lowercase prose.
    return any(character.isupper() for character in candidate)


def clean_judge_names(names: list[str] | None) -> tuple[str, ...]:
    """Return only the plausible judge names in `names`, order preserved."""
    if not names:
        return ()
    return tuple(name.strip() for name in names if is_plausible_judge_name(name))


def scrub_margin_letters(text: str) -> str:
    """Remove isolated Supreme Court Reports margin letters (A-H) from `text`.

    Conservative: a letter preceded by a legal-reference keyword is preserved.
    Returns a new string; `text` is not modified.
    """
    if not text:
        return text

    protected: list[str] = [
        match.group(0) for match in _REFERENCE_KEYWORDS.finditer(text)
    ]
    placeholders: dict[str, str] = {
        f"\x00{index}\x00": phrase for index, phrase in enumerate(protected)
    }

    guarded = text
    for placeholder, phrase in placeholders.items():
        guarded = guarded.replace(phrase, placeholder, 1)

    scrubbed = _MARGIN_LETTER.sub(" ", guarded)

    for placeholder, phrase in placeholders.items():
        scrubbed = scrubbed.replace(placeholder, phrase)

    return scrubbed
