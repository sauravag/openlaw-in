"""The delete-account page promises, in the future tense, what the backend
later reports in the past tense. The facts must not drift apart.

`web/app/account/delete/page.tsx` renders three `web/lib/strings.ts` entries
BEFORE the person confirms: what will be erased, that the access log is kept,
and that the sign-in identity may remain. `openlaw.accounts.notes` is what the
API says afterwards. The wording differs by tense, so this pins the facts, not
the sentences: if the backend starts keeping something else, or erasing the
access log, the page that asked for consent must change with it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openlaw.accounts.notes import deletion_notes

_STRINGS = Path(__file__).resolve().parents[2] / "web" / "lib" / "strings.ts"

_LANGUAGES = {"en": 0, "hi": 1}

#: The key facts, per language: (page string key, phrases both the page and
#: the backend note must contain).
_FACTS = {
    "en": {
        "deleteAccountWillErase": ["conversations, uploaded documents and excerpts"],
        "deleteAccountLogKept": [
            "A record of who accessed each document (the access log) is kept, "
            "without any document content."
        ],
        "deleteAccountIdentityMayRemain": [
            "Your sign-in identity (your email address at our sign-in provider)",
            "may remain",
            "site operator",
        ],
    },
    "hi": {
        "deleteAccountWillErase": ["बातचीत, अपलोड किए गए दस्तावेज़ और उनके अंश"],
        "deleteAccountLogKept": [
            "हर दस्तावेज़ को किसने देखा, इसका रिकॉर्ड (एक्सेस लॉग)",
            "बिना किसी दस्तावेज़ सामग्री के",
        ],
        "deleteAccountIdentityMayRemain": [
            "आपकी साइन इन पहचान (साइन इन प्रदाता के पास आपका ईमेल पता)",
            "बनी रह सकती है",
            "साइट संचालक",
        ],
    },
}


def _page_strings(language: str) -> dict[str, str]:
    """The `deleteAccount*` entries of the `en` or `hi` block of strings.ts."""
    text = _STRINGS.read_text(encoding="utf-8")
    # Each language block opens with its dictionary and holds the keys once.
    entries = re.findall(r'^\s*(deleteAccount\w+):\s*"((?:[^"\\]|\\.)*)",?\s*$', text, re.M)
    by_key: dict[str, list[str]] = {}
    for key, value in entries:
        by_key.setdefault(key, []).append(value)
    index = _LANGUAGES[language]
    return {key: values[index] for key, values in by_key.items() if len(values) > index}


@pytest.mark.parametrize("language", ["en", "hi"])
def test_page_strings_were_found_for_both_languages(language: str) -> None:
    strings = _page_strings(language)

    for key in _FACTS[language]:
        assert strings.get(key), f"{key} missing from the {language} strings"


@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize("identity_removed", [True, False])
def test_the_erased_and_access_log_facts_hold_in_the_backend_note(
    language: str, identity_removed: bool
) -> None:
    note = deletion_notes(identity_removed)[_LANGUAGES[language]]
    strings = _page_strings(language)

    for key in ("deleteAccountWillErase", "deleteAccountLogKept"):
        for phrase in _FACTS[language][key]:
            assert phrase in strings[key], (key, phrase)
            assert phrase in note, (key, phrase)


@pytest.mark.parametrize("language", ["en", "hi"])
def test_the_identity_may_remain_fact_holds_when_the_backend_could_not_remove_it(
    language: str,
) -> None:
    remains = deletion_notes(False)[_LANGUAGES[language]]
    removed = deletion_notes(True)[_LANGUAGES[language]]
    strings = _page_strings(language)

    for phrase in _FACTS[language]["deleteAccountIdentityMayRemain"]:
        assert phrase in strings["deleteAccountIdentityMayRemain"], phrase
        # The page promises nothing stronger than "may remain": the backend
        # must be able to say exactly that, and must not claim it always can.
        assert phrase in remains, phrase
    assert "may remain" not in removed.lower() or language == "hi"


def test_the_page_never_promises_the_identity_or_the_log_is_erased() -> None:
    en = _page_strings("en")

    assert "will be erased" not in en["deleteAccountLogKept"]
    assert "will be erased" not in en["deleteAccountIdentityMayRemain"]
    assert "will be deleted" not in en["deleteAccountIdentityMayRemain"]
