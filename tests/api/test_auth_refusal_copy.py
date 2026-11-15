"""Refusal copy for every authentication and account terminal state (Plan 7
Task 12): present in both languages, genuinely Hindi, help-carrying, identical
to the frontend's copy, and true about what it promises."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openlaw.api.refusal import (
    REFUSAL_STATUS,
    _REFUSAL_DETAIL,
    raise_refusal,
    refusal_body,
    refusal_detail,
)

_ROOT = Path(__file__).resolve().parents[2]
_STRINGS_TS = _ROOT / "web" / "lib" / "strings.ts"
_RATE_LIMIT_TS = _ROOT / "web" / "lib" / "auth" / "rateLimit.ts"

#: The eight keys Task 12 names.
BRIEF_KEYS = (
    "auth_link_sent",
    "auth_link_expired",
    "auth_failed",
    "auth_rate_limited",
    "auth_unavailable",
    "auth_migration_failed",
    "account_deleted",
    "account_delete_failed",
)
#: Earlier tasks' keys the brief does not list; held to the same standard.
EXISTING_AUTH_KEYS = (
    "sign_in_required",
    "account_delete_sign_in_required",
    "account_email_conflict",
    # Final review H1b: `GET /conversations` without a verified bearer.
    "conversations_sign_in_required",
)
ALL_KEYS = BRIEF_KEYS + EXISTING_AUTH_KEYS
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.capitalize() for part in rest)


@pytest.mark.parametrize("key", ALL_KEYS)
def test_key_resolves_in_both_languages_and_hindi_is_not_english(key: str) -> None:
    assert key in _REFUSAL_DETAIL and key in REFUSAL_STATUS
    english = refusal_detail(key, "en")
    hindi = refusal_detail(key, "hi")
    assert english and hindi
    assert hindi != english
    assert _DEVANAGARI.search(hindi)
    assert not _DEVANAGARI.search(english)


@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_body_carries_footer_and_helplines(key: str, language: str) -> None:
    body = refusal_body(key, "", None, language)

    assert body["escalation_footer"]
    assert any(line["number"] == "15100" for line in body["helplines"])  # type: ignore[index]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_a_crisis_question_still_gets_its_crisis_response(key: str) -> None:
    """Sign-in routes carry no question, so nothing composes auth refusals with
    a crisis question in production. The nearest real composition is the shared
    `refusal_body`, which every refusal goes through: it must still triage."""
    body = refusal_body(key, "my husband beats me every night", None, "en")

    assert body["crisis_response"]
    assert body["detail"] == refusal_detail(key, "en")


# --- wording rules from the brief ----------------------------------------------


def test_link_expired_names_the_remedy_and_does_not_blame() -> None:
    english = refusal_detail("auth_link_expired", "en").lower()

    assert "new link" in english
    assert "you did" not in english and "your fault" not in english and "mistake" not in english


def _limiter_minutes() -> int:
    source = _RATE_LIMIT_TS.read_text(encoding="utf-8")
    match = re.search(r"SIGN_IN_WINDOW_SECONDS\s*=\s*(\d+)", source)
    assert match is not None, "SIGN_IN_WINDOW_SECONDS not found in rateLimit.ts"
    seconds = int(match.group(1))
    assert seconds % 60 == 0, "the window must be whole minutes for the copy to say so"
    return seconds // 60


def test_rate_limited_wait_matches_the_limiter_constant() -> None:
    minutes = _limiter_minutes()

    assert f"in {minutes} minutes" in refusal_detail("auth_rate_limited", "en")
    assert f"{minutes} मिनट" in refusal_detail("auth_rate_limited", "hi")


def test_unavailable_says_everything_else_still_works() -> None:
    assert "still" in refusal_detail("auth_unavailable", "en")
    assert "without an account" in refusal_detail("auth_unavailable", "en")


def test_account_deleted_states_what_was_erased_and_what_is_kept() -> None:
    from openlaw.accounts.notes import RETAINED_NOTES

    english, hindi = RETAINED_NOTES
    assert english in refusal_detail("account_deleted", "en")
    assert hindi in refusal_detail("account_deleted", "hi")


def test_delete_failed_says_nothing_was_deleted() -> None:
    assert "not deleted" in refusal_detail("account_delete_failed", "en")


# --- the frontend copy is the same text -------------------------------------------


def _ts_strings(language: str) -> dict[str, str]:
    """`key: "literal"` pairs of one dictionary in strings.ts (one literal per
    key, so the TS source can be compared verbatim)."""
    source = _STRINGS_TS.read_text(encoding="utf-8")
    start = source.index("const EN: UIStrings")
    split = source.index("const HI: UIStrings")
    block = source[start:split] if language == "en" else source[split:]
    pairs = re.findall(r'^  (\w+):\s*("(?:[^"\\]|\\.)*"),$', block, re.MULTILINE)
    return {name: json.loads(literal) for name, literal in pairs}


@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_frontend_copy_is_verbatim_the_backend_copy(key: str, language: str) -> None:
    frontend = _ts_strings(language)

    assert frontend.get(_camel(key)) == refusal_detail(key, language)


@pytest.mark.parametrize("key", ["auth_link_sent", "account_deleted"])
def test_success_shaped_keys_cannot_be_raised(key: str) -> None:
    with pytest.raises(ValueError, match="success-shaped"):
        raise_refusal(key)
