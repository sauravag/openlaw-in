"""Consistency between the frontend's static, client-side helpline fallback
(`web/lib/fallbackHelplines.ts`) and the Python source of truth for those
same numbers and labels (`openlaw.answers.escalation`, `openlaw.api.refusal`).

Task 6's second fix round (re-review HIGH): `askQuestion` (`web/lib/api.ts`)
falls back to a static, client-bundled helpline set whenever the server told
it nothing at all -- a network failure, a timeout, or a non-2xx response
whose body is not recognisable `RefusalData` JSON (a proxy/CDN/load
balancer's own error page). That fallback is deliberately NOT the mechanism
Task 6's first fix round (C2) removed: C2 deleted a client-side allowlist
that pattern-matched digits out of SERVER-SUPPLIED prose (a mechanism that
silently produced nothing under Hindi/Devanagari text); the fallback here is
a last-resort constant for the case where there is no server text to
pattern-match in the first place. See `fallbackHelplines.ts`'s own docstring
for the full distinction -- this test file only guards that the constant
does not drift from the numbers it claims to mirror.

A stale fallback citing a decommissioned emergency number would be worse
than showing none at all, since by construction this is the ONE list the
frontend ever renders with no server round-trip to catch a mistake. This is
what makes the duplication acceptable rather than a second, silently
drifting copy: checked bidirectionally, in both languages, on every run of
the existing coverage-gated suite.

Generating `fallbackHelplines.ts` from this Python data at build time was
considered and rejected: it would make the Next.js build depend on a Python
interpreter and virtualenv being available and correctly configured in every
environment that builds the frontend (local dev, CI, a contributor's
machine) merely to emit six string literals -- exactly the "fragile
tooling" the task's own instructions warned against introducing. A
hand-maintained constant plus this test is the cheaper, equally-safe design:
both need a human to hit a failing test before shipping unnoticed drift.
"""

import re
from pathlib import Path

from openlaw.answers.escalation import Helpline, nalsa_helpline
from openlaw.api.refusal import generic_safety_helplines

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FALLBACK_TS_PATH = _REPO_ROOT / "web" / "lib" / "fallbackHelplines.ts"

#: Matches one `{ number: "...", label: "..." }` object literal inside an
#: `EN_HELPLINES`/`HI_HELPLINES` array -- labels may contain an apostrophe
#: ("Women's helpline"), which is why the TS source double-quotes them and
#: this pattern captures everything up to the closing double quote.
_ENTRY_PATTERN = re.compile(r'\{\s*number:\s*"(\d+)",\s*label:\s*"([^"]*)"\s*\}')


def _parse_array(source: str, array_name: str) -> tuple[Helpline, ...]:
    """Extract the `Helpline`-shaped entries from `array_name`'s TS array
    literal in `source`, in declaration order."""
    match = re.search(rf"{array_name}[^\[]*\[(.*?)\];", source, re.DOTALL)
    assert match is not None, (
        f"could not find {array_name}'s array literal in {_FALLBACK_TS_PATH} "
        "-- did its declaration shape change?"
    )
    return tuple(
        Helpline(number=number, label=label)
        for number, label in _ENTRY_PATTERN.findall(match.group(1))
    )


def _frontend_fallback_helplines(language: str) -> tuple[Helpline, ...]:
    source = _FALLBACK_TS_PATH.read_text(encoding="utf-8")
    array_name = "HI_HELPLINES" if language == "hi" else "EN_HELPLINES"
    return _parse_array(source, array_name)


def _backend_generic_helplines(language: str) -> tuple[Helpline, ...]:
    """The set the backend's own `crisis_extras` sends for the "no specific
    crisis, but be safe" case: `generic_safety_helplines` plus the NALSA
    number every footer cites -- the fallback mirrors exactly this set."""
    return generic_safety_helplines(language) + (nalsa_helpline(language),)


def test_fallback_ts_file_is_where_this_test_expects_it() -> None:
    assert _FALLBACK_TS_PATH.is_file()


def test_english_fallback_helplines_match_the_backend_generic_safety_set() -> None:
    frontend = _frontend_fallback_helplines("en")
    backend = _backend_generic_helplines("en")
    assert set(frontend) == set(backend), (
        f"web/lib/fallbackHelplines.ts's EN_HELPLINES {frontend} does not "
        f"match generic_safety_helplines('en') + nalsa_helpline('en') "
        f"{backend} -- keep the client-side fallback in sync with the "
        "Python source of truth"
    )


def test_hindi_fallback_helplines_match_the_backend_generic_safety_set() -> None:
    frontend = _frontend_fallback_helplines("hi")
    backend = _backend_generic_helplines("hi")
    assert set(frontend) == set(backend), (
        f"web/lib/fallbackHelplines.ts's HI_HELPLINES {frontend} does not "
        f"match generic_safety_helplines('hi') + nalsa_helpline('hi') "
        f"{backend} -- keep the client-side fallback in sync with the "
        "Python source of truth"
    )


def test_fallback_helpline_numbers_are_identical_across_languages() -> None:
    """The set of *numbers* must not vary by language -- only the labels
    should differ -- otherwise a Hindi-selecting user in an outage would see
    a different (and unreviewed) set of emergency numbers than an
    English-selecting one."""
    en_numbers = {helpline.number for helpline in _frontend_fallback_helplines("en")}
    hi_numbers = {helpline.number for helpline in _frontend_fallback_helplines("hi")}
    assert en_numbers == hi_numbers
