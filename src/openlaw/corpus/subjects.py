"""Parsing of the `legal_subject` field.

Stored as a string that renders like a Python list — ``"['tax_law', 'family_law']"``.
Iterating it directly walks characters, so it must be parsed. Measured distribution
across 544,078 provisions: 336,224 are ``general``, leaving 13 substantive tags.
There is no consumer-law tag, a documented corpus gap.
"""

import ast

SUBSTANTIVE_SUBJECTS: frozenset[str] = frozenset(
    {
        "tax_law",
        "property_law",
        "administrative_law",
        "criminal_law",
        "labour_law",
        "environmental_law",
        "banking_finance",
        "civil_procedure",
        "family_law",
        "corporate_law",
        "constitutional_law",
        "information_technology",
        "intellectual_property",
    }
)


def parse_legal_subject(raw: str | list[str] | None) -> tuple[str, ...]:
    """Return the subject tags in `raw` as a tuple of strings.

    Handles a real list, a string-encoded list, and a bare single tag. Returns an
    empty tuple when there is nothing to parse.
    """
    if not raw:
        return ()
    if isinstance(raw, list):
        return tuple(str(tag) for tag in raw if tag)

    text = raw.strip()
    if not text:
        return ()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return (text,)

    if isinstance(parsed, (list, tuple)):
        return tuple(str(tag) for tag in parsed if tag)
    return (str(parsed),) if parsed else ()
