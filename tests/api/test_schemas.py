"""Tests for `openlaw.api.schemas` -- request validation and response shaping.

The launch-language gate (final whole-plan review, H4) belongs here: Plan 4's
review restricted launch to English and Hindi because only Hindi has every
glossary term verified by a native speaker
(`scripts/review_translations.py`). Before this fix that restriction existed
ONLY in `web/lib/languages.ts` -- a UI affordance a plain `fetch` to `/ask`
could simply bypass, e.g. `{"language": "ta"}`, and receive Tamil legal prose
translated with zero pinned terminology, wearing a real government citation.
`AskRequest.language`'s validator (`schemas.LAUNCH_LANGUAGES`) is the actual
enforcement; `test_frontend_language_list_matches_the_launch_gate` below is
the boundary test that keeps the UI list from silently drifting wider than
what the server will actually accept -- the mirror image of
`tests/answers/test_fallback_helpline_consistency.py`, which does the same
kind of Python-reads-TypeScript check for the helpline fallback data.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from openlaw.api.schemas import LAUNCH_LANGUAGES, AskRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LANGUAGES_TS_PATH = _REPO_ROOT / "web" / "lib" / "languages.ts"

#: Matches `{ code: 'xx', label: '...' }` entries inside `LANGUAGES`'s array
#: literal in `languages.ts`.
_CODE_PATTERN = re.compile(r"code:\s*'([a-z]{2})'")


def _frontend_language_codes() -> frozenset[str]:
    source = _LANGUAGES_TS_PATH.read_text(encoding="utf-8")
    # The declaration is `export const LANGUAGES: readonly Language[] = [...]`
    # -- `Language[]` itself contains a `[]` pair, so the match must anchor on
    # the `=` that starts the actual array literal, not merely "the first
    # `[...]` after LANGUAGES".
    match = re.search(r"LANGUAGES[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match is not None, (
        f"could not find LANGUAGES's array literal in {_LANGUAGES_TS_PATH} "
        "-- did its declaration shape change?"
    )
    return frozenset(_CODE_PATTERN.findall(match.group(1)))


def test_languages_ts_file_is_where_this_test_expects_it() -> None:
    assert _LANGUAGES_TS_PATH.is_file()


def test_frontend_language_list_matches_the_launch_gate() -> None:
    """The UI's language picker must offer exactly the languages the server
    will accept -- no more (an unglossaried language reachable through the
    UI, H4's exact failure mode) and no less (a language the server accepts
    but the UI never offers, a silently unreachable feature)."""
    assert _frontend_language_codes() == LAUNCH_LANGUAGES


@pytest.mark.parametrize("language", sorted(LAUNCH_LANGUAGES))
def test_ask_request_accepts_every_launch_language(language: str) -> None:
    AskRequest(question="q", language=language)


def test_ask_request_rejects_a_glossary_incomplete_language() -> None:
    """Reproduces the H4 finding directly: a plain request for Tamil -- no
    auth, no special client -- must be rejected at this boundary, not routed
    to a translator with no pinned terminology."""
    with pytest.raises(ValidationError, match="language must be one of"):
        AskRequest(question="q", language="ta")


def test_ask_request_rejects_every_non_launch_supported_language() -> None:
    """Every one of `openlaw.language.detect.SUPPORTED_LANGUAGES` other than
    the launch set must still be rejected here -- the gate is the API
    boundary, not `SUPPORTED_LANGUAGES` itself (which the translation
    pipeline's detector still recognises for its own, separate purposes)."""
    from openlaw.language.detect import SUPPORTED_LANGUAGES

    for language in SUPPORTED_LANGUAGES:
        if language in LAUNCH_LANGUAGES:
            continue
        with pytest.raises(ValidationError):
            AskRequest(question="q", language=language)
