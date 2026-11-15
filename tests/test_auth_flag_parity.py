"""The frontend reads the auth flag exactly as the backend does (final review H3a).

`OPENLAW_AUTH_ENABLED` is a pydantic `bool`, which accepts several truthy
spellings. Compose passes the same raw string to the web build, so a frontend
that accepted only "true" built Mode A beside a Mode B backend: 24-hour expiry
on, retention notice off. `web/lib/authFlag.ts` holds the frontend's list;
this pins it to pydantic's actual behaviour, not to a copy of its docs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

_TS = Path(__file__).resolve().parents[1] / "web" / "lib" / "authFlag.ts"
_BOOL = TypeAdapter(bool)

#: Spellings to probe beyond the frontend's own list, so a value pydantic
#: accepts that the frontend forgot is caught too.
_CANDIDATES = (
    "true", "false", "1", "0", "yes", "no", "on", "off", "t", "f", "y", "n",
    "enabled", "disabled", "enable", "2", "-1", "", " true", "true ", "yes please",
    "si", "ja", "ok", "none", "null",
)


def _frontend_truthy() -> list[str]:
    source = _TS.read_text(encoding="utf-8")
    match = re.search(r"TRUTHY_AUTH_FLAG_VALUES\s*=\s*(\[[^\]]*\])", source)
    assert match is not None, "TRUTHY_AUTH_FLAG_VALUES not found in authFlag.ts"
    return json.loads(match.group(1))


def _pydantic_reads_true(value: str) -> bool:
    try:
        return _BOOL.validate_python(value) is True
    except ValidationError:
        return False


def _case_variants(value: str) -> set[str]:
    return {value, value.upper(), value.capitalize()}


def test_the_frontend_list_is_lowercase() -> None:
    assert all(value == value.lower() for value in _frontend_truthy())


@pytest.mark.parametrize("value", sorted({v for c in _CANDIDATES for v in _case_variants(c)}))
def test_frontend_and_backend_agree_on_every_spelling(value: str) -> None:
    frontend = value.lower() in _frontend_truthy()

    assert frontend is _pydantic_reads_true(value)


def test_every_frontend_spelling_is_one_pydantic_reads_as_true() -> None:
    for value in _frontend_truthy():
        for variant in _case_variants(value):
            assert _pydantic_reads_true(variant), variant
