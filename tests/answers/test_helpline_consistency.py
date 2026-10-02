"""Consistency between crisis/footer prose and the structured helplines the
API sends alongside it (`openlaw.answers.escalation.Helpline`).

Task 6's review (C2) originally found the frontend pattern-matching a fixed,
hand-copied allowlist of numbers against free-text prose to decide what to
render as a `tel:` link (`PhoneLinkedText`, `web/lib/helplineNumbers.ts` --
both since removed). The fix adopted was the review's Option B: the backend
now sends `helplines: list[Helpline]` (`{number, label}`) alongside
`crisis_response`/`escalation_footer`, and the frontend (`HelplineList`)
renders tap-to-call links straight from that structured list, never by
scanning prose for digits. That closes the localisation hazard structurally
(a Hindi crisis message with Devanagari digits still carries a plain ASCII
`number` field), but it opens a *different* drift class this test guards
against: `crisis_helplines`/`nalsa_helpline`/`generic_safety_helplines`
(`escalation.py`, `refusal.py`) are hand-maintained data, separate from the
prose strings they describe, and nothing else checks the two stay in sync.

Two directions, both real:

- A number the prose actually cites but the structured helpline list omits
  is invisible to `HelplineList` entirely -- the reader sees a number
  mentioned in the sentence with no way to tap it, which is *worse* than the
  old regex approach for that number, not better.
- A structured helpline that does not correspond to anything the prose
  cites is a stray tap-to-call button with no context explaining why it is
  there.

A third check guards the reason Option B was worth doing at all: Hindi
crisis/footer text is static and hand-authored precisely so every phone
number in it stays ASCII digits (0-9), not Devanagari numerals (०-९) -- see
`openlaw.answers.escalation`'s Hindi dict comment. Even though tap-to-call no
longer depends on this (the `Helpline.number` field is why), the displayed
prose itself should still be legible and consistent between languages.
"""

import re

from openlaw.answers.escalation import (
    CrisisKind,
    Helpline,
    crisis_helplines,
    crisis_response,
    escalation_footer,
    nalsa_helpline,
)
from openlaw.api.refusal import (
    GENERIC_SAFETY_NOTE,
    _GENERIC_SAFETY_NOTE_HI,
    generic_safety_helplines,
)

#: Matches a run of digits long enough to be a helpline (3-5 digits) -- long
#: enough to skip a bare statute section number ("section 12"), short enough
#: to still catch every real helpline here.
_NUMBER_PATTERN = re.compile(r"\d{3,5}")

#: Not a helpline number -- the year in `escalation_footer`'s "the Legal
#: Services Authorities Act, 1987" reference, present in every rendering of
#: that footer in both languages.
_KNOWN_NON_HELPLINE_NUMBERS = frozenset({"1987"})


def _numbers_in(text: str) -> set[str]:
    """Return every ASCII-digit run in `text` that is not a known non-helpline
    number (see `_KNOWN_NON_HELPLINE_NUMBERS`)."""
    return {
        match
        for match in _NUMBER_PATTERN.findall(text)
        if match not in _KNOWN_NON_HELPLINE_NUMBERS
    }


def _numbers_of(helplines: tuple[Helpline, ...]) -> set[str]:
    return {helpline.number for helpline in helplines}


def test_every_crisis_helpline_number_actually_appears_in_the_crisis_prose() -> None:
    """A structured helpline with no matching mention in `crisis_response`
    would be a tap-to-call button the reader has no sentence to connect it
    to."""
    for kind in CrisisKind:
        for language in ("en", "hi"):
            prose_numbers = _numbers_in(crisis_response(kind, language))
            structured_numbers = _numbers_of(crisis_helplines(kind, language))
            assert structured_numbers <= prose_numbers, (
                f"{kind}/{language}: crisis_helplines() returns "
                f"{sorted(structured_numbers)}, but crisis_response() only "
                f"cites {sorted(prose_numbers)} -- a structured helpline has "
                "nothing to point to in the actual sentence"
            )


def test_every_number_in_crisis_prose_has_a_matching_structured_helpline() -> None:
    """A number the prose cites but `crisis_helplines` omits is invisible to
    `HelplineList` -- worse than the old regex approach for that number."""
    for kind in CrisisKind:
        for language in ("en", "hi"):
            prose_numbers = _numbers_in(crisis_response(kind, language))
            structured_numbers = _numbers_of(crisis_helplines(kind, language))
            assert prose_numbers <= structured_numbers, (
                f"{kind}/{language}: crisis_response() cites "
                f"{sorted(prose_numbers)}, but crisis_helplines() only "
                f"returns {sorted(structured_numbers)} -- add the missing "
                "number to _CRISIS_HELPLINE_NUMBERS"
            )


def test_generic_safety_helplines_match_the_generic_safety_note() -> None:
    for language, note in (("en", GENERIC_SAFETY_NOTE), ("hi", _GENERIC_SAFETY_NOTE_HI)):
        prose_numbers = _numbers_in(note)
        structured_numbers = _numbers_of(generic_safety_helplines(language))
        assert structured_numbers == prose_numbers, (
            f"{language}: GENERIC_SAFETY_NOTE cites {sorted(prose_numbers)}, "
            f"generic_safety_helplines() returns {sorted(structured_numbers)}"
        )


def test_nalsa_helpline_number_matches_the_escalation_footer_for_every_state() -> None:
    for language in ("en", "hi"):
        for state in (None, "Maharashtra"):
            footer_numbers = _numbers_in(escalation_footer(state, language))
            assert nalsa_helpline(language).number in footer_numbers


def test_hindi_crisis_text_uses_ascii_digits_matching_the_english_version() -> None:
    """Guards the Devanagari-digit hazard that motivated adopting structured
    helplines in the first place (see the module docstring)."""
    for kind in CrisisKind:
        en_numbers = _numbers_in(crisis_response(kind, "en"))
        hi_numbers = _numbers_in(crisis_response(kind, "hi"))
        assert hi_numbers == en_numbers, (
            f"{kind}: Hindi crisis text's numbers {sorted(hi_numbers)} do not "
            f"match the English version's {sorted(en_numbers)} -- a number "
            "may have been rendered in Devanagari numerals instead of ASCII"
        )


def test_hindi_escalation_footer_uses_ascii_digits_matching_the_english_version() -> None:
    for state in (None, "Maharashtra"):
        en_numbers = _numbers_in(escalation_footer(state, "en"))
        hi_numbers = _numbers_in(escalation_footer(state, "hi"))
        assert hi_numbers == en_numbers


def test_hindi_generic_safety_note_uses_ascii_digits_matching_the_english_version() -> None:
    en_numbers = _numbers_in(GENERIC_SAFETY_NOTE)
    hi_numbers = _numbers_in(_GENERIC_SAFETY_NOTE_HI)
    assert hi_numbers == en_numbers
