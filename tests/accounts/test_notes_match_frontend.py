"""The backend's deletion note and the frontend's post-deletion notice must
state the same facts: `web/lib/accountDeletedStrings.ts` is Task 14's single
source, `openlaw.accounts.notes` is the API's. Text is compared verbatim."""

from __future__ import annotations

import re
from pathlib import Path

from openlaw.accounts.notes import deletion_notes

_TS = Path(__file__).resolve().parents[2] / "web" / "lib" / "accountDeletedStrings.ts"


def _literals() -> set[str]:
    return set(re.findall(r'"((?:[^"\\]|\\.)*)"', _TS.read_text(encoding="utf-8")))


def test_every_backend_sentence_appears_verbatim_in_the_frontend_strings() -> None:
    literals = _literals()
    for index in (0, 1):  # en, hi
        removed = deletion_notes(True)[index]
        remains = deletion_notes(False)[index]
        head = removed
        while not remains.startswith(head):
            head = head[:-1]
        # Common prefix may run into the tails' shared words; cut at a sentence end.
        head = head[: max(head.rfind("."), head.rfind("।")) + 1].strip()
        # The shared head is exactly the frontend's erased + access-log sentences.
        sentences = [lit for lit in literals if lit in head]
        assert " ".join(sorted(sentences, key=head.find)) == head
        # Each outcome-specific tail is a frontend string verbatim.
        assert removed[len(head):].strip() in literals
        assert remains[len(head):].strip() in literals


def test_the_key_facts_agree() -> None:
    en_removed, hi_removed = deletion_notes(True)
    en_remains, hi_remains = deletion_notes(False)
    for text in (en_removed, en_remains):
        assert "access log" in text and "without any document content" in text
    for text in (hi_removed, hi_remains):
        assert "एक्सेस लॉग" in text
    assert "also been removed" in en_removed and "may remain" not in en_removed
    assert "could not be removed and may remain" in en_remains
    assert "also been removed" not in en_remains
    literals = _literals()
    assert any("access log" in lit and "without any document content" in lit for lit in literals)
    assert any("एक्सेस लॉग" in lit for lit in literals)
