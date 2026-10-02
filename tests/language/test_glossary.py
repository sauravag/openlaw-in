import json
from pathlib import Path

import pytest

from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import Glossary, GlossaryTerm, load_glossary

# Unicode block each language's script occupies, mirroring the ranges in
# openlaw.language.detect.SCRIPT_RANGES. Marathi shares Devanagari with Hindi
# and Assamese shares its script with Bengali, exactly as noted there.
_SCRIPT_RANGE_FOR_LANGUAGE: dict[str, tuple[int, int]] = {
    "hi": (0x0900, 0x097F),
    "mr": (0x0900, 0x097F),
    "bn": (0x0980, 0x09FF),
    "as": (0x0980, 0x09FF),
    "pa": (0x0A00, 0x0A7F),
    "gu": (0x0A80, 0x0AFF),
    "or": (0x0B00, 0x0B7F),
    "ta": (0x0B80, 0x0BFF),
    "te": (0x0C00, 0x0C7F),
    "kn": (0x0C80, 0x0CFF),
    "ml": (0x0D00, 0x0D7F),
    "ur": (0x0600, 0x06FF),
}


def _is_neutral_character(character: str) -> bool:
    """Return True for a character allowed in any translation regardless of script.

    A translation may legitimately preserve an ASCII word, digit, or ordinary
    punctuation (a hyphen, for instance), so those do not count as script
    violations.
    """
    return ord(character) < 128 or character.isspace()


def _glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत"},
            ),
            GlossaryTerm(
                term="cognizable offence",
                preserve_english=True,
                translations={"hi": "संज्ञेय अपराध"},
            ),
            GlossaryTerm(
                term="maintenance",
                preserve_english=False,
                translations={"hi": "भरण-पोषण"},
            ),
        )
    )


def test_lookup_finds_a_term() -> None:
    found = _glossary().lookup("anticipatory bail")
    assert found is not None
    assert found.preserve_english is True


def test_lookup_is_case_insensitive() -> None:
    assert _glossary().lookup("Anticipatory Bail") is not None


def test_lookup_returns_none_for_an_unknown_term() -> None:
    assert _glossary().lookup("landlord") is None


def test_finds_glossary_terms_present_in_text() -> None:
    found = _glossary().terms_in("Can I apply for anticipatory bail before arrest?")
    assert [term.term for term in found] == ["anticipatory bail"]


def test_finds_multiple_terms() -> None:
    found = _glossary().terms_in(
        "Is this a cognizable offence, and can I get anticipatory bail?"
    )
    assert len(found) == 2


def test_finds_no_terms_in_ordinary_text() -> None:
    assert _glossary().terms_in("my landlord kept my deposit") == ()


def test_instruction_pins_preserved_terms_in_english() -> None:
    instruction = _glossary().instruction_for("hi")
    assert "anticipatory bail" in instruction
    assert "अग्रिम जमानत" in instruction


def test_instruction_gives_a_fixed_rendering_for_translatable_terms() -> None:
    assert "भरण-पोषण" in _glossary().instruction_for("hi")


def test_instruction_for_english_is_empty() -> None:
    """No pinning needed when the answer is already in English."""
    assert _glossary().instruction_for("en") == ""


def test_instruction_for_a_language_without_translations_still_pins_english() -> None:
    assert "anticipatory bail" in _glossary().instruction_for("ta")


def test_loads_glossary_from_json(tmp_path: Path) -> None:
    path = tmp_path / "terms.json"
    path.write_text(
        json.dumps(
            [
                {
                    "term": "lok adalat",
                    "preserve_english": True,
                    "translations": {"hi": "लोक अदालत"},
                }
            ]
        ),
        encoding="utf-8",
    )
    assert load_glossary(path).lookup("lok adalat") is not None


def test_loading_a_missing_glossary_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_glossary(tmp_path / "absent.json")


def test_the_shipped_glossary_loads_and_pins_anticipatory_bail() -> None:
    """Guards against a malformed data file reaching production."""
    glossary = load_glossary(Path("data/glossary/legal_terms.json"))
    term = glossary.lookup("anticipatory bail")
    assert term is not None
    assert term.preserve_english is True


def test_shipped_glossary_translations_only_use_supported_language_codes() -> None:
    """A translation under a code the language module does not recognize would be
    an unvetted claim about a language this project does not otherwise support."""
    raw = json.loads(Path("data/glossary/legal_terms.json").read_text(encoding="utf-8"))
    for entry in raw:
        for code in entry["translations"]:
            assert code in SUPPORTED_LANGUAGES


def test_every_glossary_translation_uses_its_language_expected_script() -> None:
    """A single stray codepoint from another Indic script renders as visibly
    broken text to a native reader, yet it survives a visual read-through of a
    JSON file. Catch it mechanically instead: every non-neutral character in a
    translation must fall inside that language's Unicode script block."""
    raw = json.loads(
        Path("data/glossary/legal_terms.json").read_text(encoding="utf-8")
    )
    for entry in raw:
        for code, text in entry["translations"].items():
            script_range = _SCRIPT_RANGE_FOR_LANGUAGE.get(code)
            if script_range is None:
                continue
            start, end = script_range
            for character in text:
                if _is_neutral_character(character):
                    continue
                assert start <= ord(character) <= end, (
                    f"{entry['term']!r} translation for {code!r} contains "
                    f"{character!r} (U+{ord(character):04X}), outside the "
                    f"expected script for {code!r}"
                )


def test_glossary_terms_are_immutable() -> None:
    term = GlossaryTerm(term="bail", preserve_english=False, translations={})
    with pytest.raises(Exception):
        term.term = "something else"  # type: ignore[misc]
