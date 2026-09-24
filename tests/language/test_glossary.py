import json
from pathlib import Path

import pytest

from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import Glossary, GlossaryTerm, load_glossary


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


def test_glossary_terms_are_immutable() -> None:
    term = GlossaryTerm(term="bail", preserve_english=False, translations={})
    with pytest.raises(Exception):
        term.term = "something else"  # type: ignore[misc]
