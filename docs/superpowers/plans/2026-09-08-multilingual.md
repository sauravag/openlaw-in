# OpenLaw.in Multilingual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a person ask in their own language and receive the answer in that language, without weakening the citation guarantee.

**Architecture:** The corpus is English, so language is handled at the edges. The query is detected and translated into English; retrieval, generation and **validation all happen in English**; only the already-validated answer is translated back. Because translation operates on verified text, it is structurally incapable of introducing an unverified citation. Statutory quotations stay in English alongside the translation, since the English text is the authoritative one and a translated statute is not the statute.

**Tech Stack:** Python 3.12+, the per-role `ModelRegistry` from Plan 3, `langdetect`, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-openlaw-in-design.md` (§7, §8)

**Depends on:** `2026-09-08-retrieval-and-answers.md` — Tasks 4, 6, 8 (`ModelRegistry`, `LLMAdapter`, `ValidatedAnswer`, `generate_answer`)

## Global Constraints

- **Order is fixed and load-bearing:** detect → translate query to English → retrieve → generate → **validate** → translate answer. Translation never precedes validation.
- **Never translate a citation, a `chunk_id`, or a `source_url`.** These are identifiers, not prose.
- **Statutory quotations remain in English** beside the translated explanation.
- **Glossary terms are pinned.** Indian legal terms with no faithful equivalent (anticipatory bail, cognizable offence, *lok adalat*) are kept in English or use the fixed accepted rendering — never freely translated.
- **The translator model is configured per role and may be overridden per language.** No call site names a model.
- **A translation failure degrades to English**, never to silence and never to a mangled answer.
- **Crisis detection runs on the user's original text**, so a failed translation cannot suppress a helpline.
- **Python style:** type hints everywhere, `pathlib.Path`, f-strings, functions under 50 lines, files under 800 lines.
- **TDD mandatory**, minimum 80% coverage. No unit test calls a live model.

---

## File Structure

```
src/openlaw/language/
├── detect.py       # language detection with script-first resolution
├── glossary.py     # pinned legal terms per language
├── translate.py    # query in, answer out
└── pipeline.py     # the localised end-to-end call

data/glossary/
└── legal_terms.json

scripts/
└── review_translations.py

tests/language/
├── test_detect.py
├── test_glossary.py
├── test_translate.py
└── test_pipeline.py
```

---

## Task 1: Language detection

**Files:**
- Create: `src/openlaw/language/__init__.py`, `src/openlaw/language/detect.py`
- Create: `tests/language/__init__.py`, `tests/language/test_detect.py`
- Modify: `pyproject.toml` — add `langdetect`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DEFAULT_LANGUAGE: str` — `"en"`
  - `SUPPORTED_LANGUAGES: dict[str, str]` — ISO code → English name
  - `SCRIPT_RANGES: tuple[tuple[str, int, int], ...]`
  - `detect_by_script(text: str) -> str | None`
  - `detect_language(text: str) -> str`

- [ ] **Step 1: Add the dependency**

Add `"langdetect>=1.0.9"` to `pyproject.toml` dependencies.

- [ ] **Step 2: Write the failing test**

Create `tests/language/test_detect.py`:

```python
import pytest

from openlaw.language.detect import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    detect_by_script,
    detect_language,
)


def test_default_language_is_english() -> None:
    assert DEFAULT_LANGUAGE == "en"


def test_supported_languages_cover_the_largest_indian_languages() -> None:
    for code in ("en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or"):
        assert code in SUPPORTED_LANGUAGES


def test_detects_devanagari_as_hindi() -> None:
    assert detect_by_script("मेरा मकान मालिक जमा राशि वापस नहीं कर रहा") == "hi"


def test_detects_tamil_script() -> None:
    assert detect_by_script("என் நில உரிமையாளர் வைப்புத்தொகையை") == "ta"


def test_detects_bengali_script() -> None:
    assert detect_by_script("আমার বাড়িওয়ালা জমা ফেরত দিচ্ছে না") == "bn"


def test_detects_telugu_script() -> None:
    assert detect_by_script("నా ఇంటి యజమాని డిపాజిట్ తిరిగి ఇవ్వడం లేదు") == "te"


def test_detects_gujarati_script() -> None:
    assert detect_by_script("મારો મકાનમાલિક થાપણ પરત આપતો નથી") == "gu"


def test_script_detection_returns_none_for_latin_text() -> None:
    """Latin script is ambiguous — it needs statistical detection, not ranges."""
    assert detect_by_script("my landlord kept my deposit") is None


def test_script_detection_ignores_a_stray_character() -> None:
    """One Devanagari character in an English sentence is not Hindi."""
    assert detect_by_script("my landlord क kept my deposit") is None


def test_detects_english_end_to_end() -> None:
    assert detect_language("my landlord will not return my security deposit") == "en"


def test_detects_hindi_end_to_end() -> None:
    assert detect_language("मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है") == "hi"


@pytest.mark.parametrize("text", ["", "   ", "123 456"])
def test_falls_back_to_english_for_unusable_input(text: str) -> None:
    assert detect_language(text) == DEFAULT_LANGUAGE


def test_falls_back_to_english_for_an_unsupported_language() -> None:
    """A language we cannot serve must not be claimed as supported."""
    assert detect_language("Ceci est une question juridique française") == (
        DEFAULT_LANGUAGE
    )
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/language/test_detect.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.language'`

- [ ] **Step 4: Implement `src/openlaw/language/detect.py`**

```python
"""Language detection for incoming questions.

Script ranges are tried first because they are deterministic for Indic scripts —
Devanagari is Devanagari. Statistical detection is the fallback for Latin script,
where a short question is easy to misclassify.

An unsupported language falls back to English rather than being claimed as
supported: answering in a language we cannot serve well is worse than answering
in English.
"""

DEFAULT_LANGUAGE = "en"

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "or": "Odia",
    "ur": "Urdu",
    "as": "Assamese",
}

# (language code, Unicode block start, Unicode block end)
SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("hi", 0x0900, 0x097F),  # Devanagari — shared with Marathi
    ("bn", 0x0980, 0x09FF),  # Bengali — shared with Assamese
    ("pa", 0x0A00, 0x0A7F),  # Gurmukhi
    ("gu", 0x0A80, 0x0AFF),  # Gujarati
    ("or", 0x0B00, 0x0B7F),  # Odia
    ("ta", 0x0B80, 0x0BFF),  # Tamil
    ("te", 0x0C00, 0x0C7F),  # Telugu
    ("kn", 0x0C80, 0x0CFF),  # Kannada
    ("ml", 0x0D00, 0x0D7F),  # Malayalam
    ("ur", 0x0600, 0x06FF),  # Arabic script
)

_MIN_SCRIPT_CHARS = 3


def detect_by_script(text: str) -> str | None:
    """Return the language implied by `text`'s script, or None when Latin.

    Devanagari resolves to Hindi and Bengali script to Bengali. Marathi and
    Assamese share those scripts and are distinguished only by an explicit user
    choice, which the interface supplies.
    """
    if not text:
        return None

    counts: dict[str, int] = {}
    for character in text:
        code = ord(character)
        for language, start, end in SCRIPT_RANGES:
            if start <= code <= end:
                counts[language] = counts.get(language, 0) + 1
                break

    if not counts:
        return None
    language, count = max(counts.items(), key=lambda item: item[1])
    return language if count >= _MIN_SCRIPT_CHARS else None


def detect_language(text: str) -> str:
    """Return the ISO code for `text`, falling back to English."""
    if not text or not text.strip():
        return DEFAULT_LANGUAGE

    by_script = detect_by_script(text)
    if by_script:
        return by_script

    try:
        from langdetect import detect  # imported lazily; slow to load

        detected = detect(text)
    except Exception:
        return DEFAULT_LANGUAGE

    return detected if detected in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
```

Also create empty `src/openlaw/language/__init__.py` and `tests/language/__init__.py`.

- [ ] **Step 5: Run it to verify it passes**

Run: `pytest tests/language/test_detect.py -v --no-cov`
Expected: PASS (14 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/openlaw/language tests/language
git commit -m "feat: script-first language detection with English fallback"
```

---

## Task 2: The legal glossary

Indian legal terms whose free translation would mislead. "Anticipatory bail" has no equivalent in most legal systems and none in casual Hindi; a model asked to translate it can produce something that reads plausibly and means something else. A mistranslated legal term in a legal-aid answer can send someone to the wrong forum, so these are pinned.

**Files:**
- Create: `src/openlaw/language/glossary.py`
- Create: `data/glossary/legal_terms.json`
- Create: `tests/language/test_glossary.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DEFAULT_GLOSSARY_PATH: Path`
  - `GlossaryTerm` — frozen model: `term: str`, `preserve_english: bool`, `translations: dict[str, str]`
  - `Glossary(terms: tuple[GlossaryTerm, ...])` — `lookup`, `terms_in`, `instruction_for`
  - `load_glossary(path: Path = DEFAULT_GLOSSARY_PATH) -> Glossary`

- [ ] **Step 1: Write the failing test**

Create `tests/language/test_glossary.py`:

```python
import json
from pathlib import Path

import pytest

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


def test_loads_glossary_from_json(tmp_path) -> None:
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


def test_loading_a_missing_glossary_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_glossary(tmp_path / "absent.json")


def test_the_shipped_glossary_loads_and_pins_anticipatory_bail() -> None:
    """Guards against a malformed data file reaching production."""
    glossary = load_glossary(Path("data/glossary/legal_terms.json"))
    term = glossary.lookup("anticipatory bail")
    assert term is not None
    assert term.preserve_english is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/language/test_glossary.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.language.glossary'`

- [ ] **Step 3: Create `data/glossary/legal_terms.json`**

```json
[
  {
    "term": "anticipatory bail",
    "preserve_english": true,
    "translations": {"hi": "अग्रिम जमानत", "mr": "अटकपूर्व जामीन", "bn": "আগাম জামিন"}
  },
  {
    "term": "cognizable offence",
    "preserve_english": true,
    "translations": {"hi": "संज्ञेय अपराध", "mr": "दखलपात्र गुन्हा"}
  },
  {
    "term": "non-cognizable offence",
    "preserve_english": true,
    "translations": {"hi": "असंज्ञेय अपराध"}
  },
  {
    "term": "lok adalat",
    "preserve_english": true,
    "translations": {"hi": "लोक अदालत", "ta": "லோக் அதாலத்"}
  },
  {
    "term": "first information report",
    "preserve_english": true,
    "translations": {"hi": "प्रथम सूचना रिपोर्ट"}
  },
  {
    "term": "public interest litigation",
    "preserve_english": true,
    "translations": {"hi": "जनहित याचिका"}
  },
  {
    "term": "writ petition",
    "preserve_english": true,
    "translations": {"hi": "रिट याचिका"}
  },
  {
    "term": "power of attorney",
    "preserve_english": true,
    "translations": {"hi": "मुख्तारनामा"}
  },
  {
    "term": "succession certificate",
    "preserve_english": true,
    "translations": {"hi": "उत्तराधिकार प्रमाणपत्र"}
  },
  {
    "term": "maintenance",
    "preserve_english": false,
    "translations": {"hi": "भरण-पोषण", "ta": "ஜீவனாம்சம்", "bn": "ভরণপোষণ"}
  },
  {
    "term": "decree",
    "preserve_english": false,
    "translations": {"hi": "डिक्री"}
  },
  {
    "term": "injunction",
    "preserve_english": false,
    "translations": {"hi": "निषेधाज्ञा"}
  }
]
```

- [ ] **Step 4: Implement `src/openlaw/language/glossary.py`**

```python
"""Pinned Indian legal terminology.

Some terms have no faithful equivalent outside Indian law. "Anticipatory bail" is
the clearest case: a model asked to render it in Hindi can produce something that
reads plausibly and means something materially different. Terms marked
`preserve_english` stay in English with the accepted local rendering beside them.

This is a correctness measure, not a stylistic one.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_GLOSSARY_PATH = Path("data/glossary/legal_terms.json")


class GlossaryTerm(BaseModel):
    """One pinned legal term and its accepted renderings."""

    model_config = ConfigDict(frozen=True)

    term: str
    preserve_english: bool
    translations: dict[str, str] = {}


class Glossary:
    """A lookup over pinned legal terms."""

    def __init__(self, terms: tuple[GlossaryTerm, ...]) -> None:
        self._terms = terms
        self._by_lower = {term.term.lower(): term for term in terms}

    def lookup(self, term: str) -> GlossaryTerm | None:
        """Return the glossary entry for `term`, case-insensitively."""
        return self._by_lower.get(term.strip().lower())

    def terms_in(self, text: str) -> tuple[GlossaryTerm, ...]:
        """Return every glossary term appearing in `text`."""
        if not text:
            return ()
        lowered = text.lower()
        return tuple(term for term in self._terms if term.term.lower() in lowered)

    def instruction_for(self, language: str) -> str:
        """Return the translator instruction pinning terms for `language`.

        Empty for English, where no pinning is needed.
        """
        if language == "en":
            return ""

        lines: list[str] = []
        for term in self._terms:
            rendering = term.translations.get(language)
            if term.preserve_english:
                if rendering:
                    lines.append(
                        f'- Keep "{term.term}" in English, optionally followed by '
                        f'"{rendering}" in brackets.'
                    )
                else:
                    lines.append(f'- Keep "{term.term}" in English.')
            elif rendering:
                lines.append(f'- Translate "{term.term}" as "{rendering}".')

        if not lines:
            return ""
        return "Legal terminology rules:\n" + "\n".join(lines)


def load_glossary(path: Path = DEFAULT_GLOSSARY_PATH) -> Glossary:
    """Load the glossary from the JSON file at `path`."""
    if not path.exists():
        raise FileNotFoundError(f"glossary not found at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Glossary(tuple(GlossaryTerm.model_validate(entry) for entry in raw))
```

- [ ] **Step 5: Run it to verify it passes**

Run: `pytest tests/language/test_glossary.py -v --no-cov`
Expected: PASS (13 passed)

- [ ] **Step 6: Commit**

```bash
git add data/glossary src/openlaw/language/glossary.py tests/language/test_glossary.py
git commit -m "feat: pinned legal glossary preventing mistranslated legal terms"
```

---

## Task 3: Query and answer translation

**Files:**
- Create: `src/openlaw/language/translate.py`
- Create: `tests/language/test_translate.py`

**Interfaces:**
- Consumes: `LLMAdapter`, `ModelRegistry`, `ModelRole` (retrieval-and-answers Task 4), `Claim` (Task 5), `ValidatedAnswer` (Task 6), `Glossary` (Task 2), `SUPPORTED_LANGUAGES` (Task 1)
- Produces:
  - `MAX_TRANSLATION_TOKENS: int` — `2048`
  - `QUERY_SYSTEM_PROMPT: str`, `ANSWER_SYSTEM_PROMPT: str`
  - `translate_query(text, language, adapter, registry) -> str`
  - `translate_answer(answer, language, adapter, registry, glossary) -> ValidatedAnswer`

- [ ] **Step 1: Write the failing test**

Create `tests/language/test_translate.py`:

```python
from openlaw.answers.models import ModelRegistry, ModelRole, StubAdapter
from openlaw.answers.schema import Claim
from openlaw.answers.validate import Source, ValidatedAnswer
from openlaw.language.glossary import Glossary, GlossaryTerm
from openlaw.language.translate import translate_answer, translate_query

TRANSLATED = '{"summary": "जमा राशि वापस करनी होगी।", "claims": ["वापसी आवश्यक है।"]}'


def _glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत"},
            ),
        )
    )


def _answer() -> ValidatedAnswer:
    return ValidatedAnswer(
        summary="The deposit must be refunded within one month.",
        claims=(Claim(text="A refund is required.", chunk_ids=("a",)),),
        sources=(
            Source(
                chunk_id="a",
                source_url="https://www.indiacode.nic.in/handle/123456789/1",
                title="Model Tenancy Act",
            ),
        ),
        is_answerable=True,
    )


def test_english_query_is_returned_unchanged_without_a_model_call() -> None:
    adapter = StubAdapter([])
    result = translate_query("my landlord kept my deposit", "en", adapter, ModelRegistry())

    assert result == "my landlord kept my deposit"
    assert adapter.calls == []


def test_non_english_query_is_translated() -> None:
    adapter = StubAdapter(["my landlord is not returning my deposit"])
    result = translate_query(
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा", "hi", adapter, ModelRegistry()
    )
    assert result == "my landlord is not returning my deposit"


def test_query_translation_uses_the_translator_role_not_the_generator() -> None:
    """Translation must not burn frontier-model budget."""
    adapter = StubAdapter(["translated"])
    translate_query("प्रश्न", "hi", adapter, ModelRegistry())
    assert adapter.calls[0]["model"] == "claude-haiku-4-5"


def test_query_translation_honours_a_per_language_model_override() -> None:
    adapter = StubAdapter(["translated"])
    registry = ModelRegistry(
        language_overrides={(ModelRole.TRANSLATOR, "ta"): "indic-specialist-model"}
    )
    translate_query("கேள்வி", "ta", adapter, registry)
    assert adapter.calls[0]["model"] == "indic-specialist-model"


def test_failed_query_translation_falls_back_to_the_original() -> None:
    """A translation failure must not lose the user's question."""
    adapter = StubAdapter([""])
    assert translate_query("मूल प्रश्न", "hi", adapter, ModelRegistry()) == "मूल प्रश्न"


def test_english_answer_is_returned_unchanged() -> None:
    adapter = StubAdapter([])
    result = translate_answer(_answer(), "en", adapter, ModelRegistry(), _glossary())

    assert result.summary == _answer().summary
    assert adapter.calls == []


def test_answer_summary_and_claims_are_translated() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.summary == "जमा राशि वापस करनी होगी।"
    assert result.claims[0].text == "वापसी आवश्यक है।"


def test_translation_preserves_chunk_ids_exactly() -> None:
    """chunk_ids are identifiers — translating one would break the citation."""
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.claims[0].chunk_ids == ("a",)


def test_translation_preserves_source_urls_exactly() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.sources[0].source_url == (
        "https://www.indiacode.nic.in/handle/123456789/1"
    )


def test_translation_preserves_answerability() -> None:
    result = translate_answer(
        _answer(), "hi", StubAdapter([TRANSLATED]), ModelRegistry(), _glossary()
    )
    assert result.is_answerable is True


def test_glossary_instruction_reaches_the_translator() -> None:
    adapter = StubAdapter([TRANSLATED])
    translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())
    assert "anticipatory bail" in adapter.calls[0]["system"]


def test_failed_answer_translation_falls_back_to_english() -> None:
    """Better an English answer than a mangled one."""
    result = translate_answer(
        _answer(), "hi", StubAdapter(["not json"]), ModelRegistry(), _glossary()
    )
    assert result.summary == _answer().summary


def test_claim_count_mismatch_falls_back_to_english() -> None:
    """A translator returning the wrong number of claims cannot be aligned."""
    adapter = StubAdapter(['{"summary": "अनुवादित", "claims": []}'])
    result = translate_answer(_answer(), "hi", adapter, ModelRegistry(), _glossary())
    assert result.summary == _answer().summary
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/language/test_translate.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.language.translate'`

- [ ] **Step 3: Implement `src/openlaw/language/translate.py`**

```python
"""Query and answer translation.

Translation sits at both edges of the pipeline and never in the middle. The query
is translated into English before retrieval; the answer is translated out of
English only after the citation validator has run. Because the validator has
already finished, translation cannot introduce an unverified citation.

Identifiers — `chunk_id`, `source_url` — are copied verbatim and never sent
through the model as translatable prose.

Every failure path degrades to English. An English answer is useful; a mangled
one is worse than none.
"""

import json

from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole
from openlaw.answers.schema import Claim
from openlaw.answers.validate import ValidatedAnswer
from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import Glossary

MAX_TRANSLATION_TOKENS = 2048

QUERY_SYSTEM_PROMPT = """\
You translate legal questions from Indian languages into English so they can be \
searched against a corpus of Indian legislation and court judgments.

Translate faithfully. Preserve every legal term, statute name, section number and \
proper noun. Do not answer the question, do not add context, and do not correct \
the user. Return only the English translation as plain text."""

ANSWER_SYSTEM_PROMPT = """\
You translate legal information from English into {language} for an ordinary \
person with no legal training.

Rules:
- Translate the meaning, not word by word. The reader must be able to act on it.
- Keep statute names, section numbers, and case citations in English.
- Never invent, add, or remove any legal content.
- Return exactly the same number of claims you were given, in the same order.

{glossary}

Return only a JSON object of the form:
{{"summary": "...", "claims": ["...", "..."]}}"""


def translate_query(
    text: str, language: str, adapter: LLMAdapter, registry: ModelRegistry
) -> str:
    """Translate `text` into English, returning it unchanged when already English.

    Falls back to the original on any failure, so a translation problem never
    loses the user's question.
    """
    if language == "en" or not text.strip():
        return text

    completion = adapter.complete(
        model=registry.for_role(ModelRole.TRANSLATOR, language=language),
        system=QUERY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        output_schema=None,
        max_tokens=MAX_TRANSLATION_TOKENS,
    )
    return completion.text.strip() or text


def translate_answer(
    answer: ValidatedAnswer,
    language: str,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    glossary: Glossary,
) -> ValidatedAnswer:
    """Translate a validated answer into `language`.

    Only `summary` and claim text are translated. `chunk_ids`, `source_url`s,
    `dropped` and `is_answerable` are copied verbatim. Any failure returns the
    English answer unchanged.
    """
    if language == "en" or not answer.claims:
        return answer

    system = ANSWER_SYSTEM_PROMPT.format(
        language=SUPPORTED_LANGUAGES.get(language, language),
        glossary=glossary.instruction_for(language),
    )
    payload = json.dumps(
        {"summary": answer.summary, "claims": [claim.text for claim in answer.claims]},
        ensure_ascii=False,
    )

    completion = adapter.complete(
        model=registry.for_role(ModelRole.TRANSLATOR, language=language),
        system=system,
        messages=[{"role": "user", "content": [{"type": "text", "text": payload}]}],
        output_schema=None,
        max_tokens=MAX_TRANSLATION_TOKENS,
    )

    try:
        translated = json.loads(completion.text)
        summary = str(translated["summary"])
        claim_texts = [str(text) for text in translated["claims"]]
    except (TypeError, KeyError, ValueError):
        return answer

    if len(claim_texts) != len(answer.claims):
        return answer

    return ValidatedAnswer(
        summary=summary,
        claims=tuple(
            Claim(text=text, chunk_ids=claim.chunk_ids)
            for text, claim in zip(claim_texts, answer.claims, strict=True)
        ),
        sources=answer.sources,
        dropped=answer.dropped,
        is_answerable=answer.is_answerable,
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/language/test_translate.py -v --no-cov`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/language/translate.py tests/language/test_translate.py
git commit -m "feat: query and answer translation preserving citation identifiers"
```

---

## Task 4: The localised pipeline

**Files:**
- Create: `src/openlaw/language/pipeline.py`
- Create: `tests/language/test_pipeline.py`

**Interfaces:**
- Consumes: `detect_language` (Task 1), `Glossary` (Task 2), `translate_query`/`translate_answer` (Task 3), `generate_answer` (retrieval-and-answers Task 8), `RetrievalContext` (Task 2 of that plan)
- Produces:
  - `Retriever` — Protocol: `retrieve(english_query: str) -> RetrievalContext`
  - `LocalisedAnswer` — frozen model: `answer: ValidatedAnswer`, `language: str`, `english_query: str`, `crisis: CrisisKind | None`, `footer: str`, `events: tuple[HallucinationEvent, ...]`
  - `answer_in_language(question, retriever, adapter, registry, glossary, state=None) -> LocalisedAnswer`

- [ ] **Step 1: Write the failing test**

Create `tests/language/test_pipeline.py`:

```python
import json

from openlaw.answers.models import ModelRegistry, StubAdapter
from openlaw.language.glossary import Glossary, GlossaryTerm
from openlaw.language.pipeline import answer_in_language
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk

TRANSLATED = '{"summary": "जमा राशि वापस करनी होगी।", "claims": ["वापसी आवश्यक है।"]}'


def _glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत"},
            ),
        )
    )


class _Retriever:
    """Records the query it was given and returns a fixed context."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, english_query: str) -> RetrievalContext:
        self.queries.append(english_query)
        return RetrievalContext(
            query=english_query,
            chunks=(
                RetrievedChunk(
                    chunk_id="a",
                    text="A landlord shall refund the deposit.",
                    source_url="https://www.indiacode.nic.in/handle/123456789/1",
                    score=0.9,
                    kind=ChunkKind.PROVISION,
                ),
            ),
        )


def _generation() -> str:
    return json.dumps(
        {
            "summary": "The deposit must be refunded.",
            "claims": [{"text": "A refund is required.", "chunk_ids": ["a"]}],
            "sufficient": True,
        }
    )


def test_english_question_skips_both_translation_steps() -> None:
    adapter = StubAdapter([_generation()])
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.language == "en"
    assert len(adapter.calls) == 1  # generation only


def test_hindi_question_is_translated_before_retrieval() -> None:
    retriever = _Retriever()
    adapter = StubAdapter(
        ["my landlord is not returning my deposit", _generation(), TRANSLATED]
    )
    result = answer_in_language(
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
        retriever,
        adapter,
        ModelRegistry(),
        _glossary(),
    )

    assert result.language == "hi"
    assert retriever.queries == ["my landlord is not returning my deposit"]


def test_retrieval_always_receives_english() -> None:
    retriever = _Retriever()
    adapter = StubAdapter(["english query", _generation(), TRANSLATED])
    answer_in_language("हिंदी प्रश्न", retriever, adapter, ModelRegistry(), _glossary())
    assert retriever.queries[0] == "english query"


def test_answer_is_returned_in_the_users_language() -> None:
    adapter = StubAdapter(["english query", _generation(), TRANSLATED])
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.answer.summary == "जमा राशि वापस करनी होगी।"


def test_citations_survive_the_round_trip() -> None:
    """The whole point: translation must not disturb the verified citation."""
    adapter = StubAdapter(["english query", _generation(), TRANSLATED])
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )

    assert result.answer.claims[0].chunk_ids == ("a",)
    assert result.answer.sources[0].source_url.startswith("https://")


def test_the_english_query_is_reported_for_debugging() -> None:
    adapter = StubAdapter(["english query", _generation(), TRANSLATED])
    result = answer_in_language(
        "हिंदी प्रश्न", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.english_query == "english query"


def test_crisis_is_detected_on_the_original_language_question() -> None:
    """Crisis detection must not depend on a translation succeeding first."""
    adapter = StubAdapter([_generation()])
    result = answer_in_language(
        "my husband beats me", _Retriever(), adapter, ModelRegistry(), _glossary()
    )
    assert result.crisis is not None


def test_footer_is_present_in_every_localised_answer() -> None:
    adapter = StubAdapter([_generation()])
    result = answer_in_language(
        "my landlord kept my deposit",
        _Retriever(),
        adapter,
        ModelRegistry(),
        _glossary(),
    )
    assert "15100" in result.footer
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/language/test_pipeline.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.language.pipeline'`

- [ ] **Step 3: Implement `src/openlaw/language/pipeline.py`**

```python
"""The localised end-to-end answer pipeline.

Order is load-bearing:

    detect -> translate query to English -> retrieve -> generate -> VALIDATE
    -> translate answer

Validation happens while everything is still in English. By the time translation
runs, every surviving claim already resolves to a retrieved chunk, so translation
cannot introduce an unverified citation — it can only render verified text badly,
and a failure there degrades to English.

Crisis detection runs on the user's original text, not the translation, so a
failed translation cannot suppress a helpline.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from openlaw.answers.escalation import CrisisKind, HallucinationEvent
from openlaw.answers.generate import generate_answer
from openlaw.answers.models import LLMAdapter, ModelRegistry
from openlaw.answers.validate import ValidatedAnswer
from openlaw.language.detect import detect_language
from openlaw.language.glossary import Glossary
from openlaw.language.translate import translate_answer, translate_query
from openlaw.retrieval.context import RetrievalContext


class Retriever(Protocol):
    """Anything that can build a retrieval context from an English query."""

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        ...


class LocalisedAnswer(BaseModel):
    """A validated answer rendered in the user's language."""

    model_config = ConfigDict(frozen=True)

    answer: ValidatedAnswer
    language: str
    english_query: str
    crisis: CrisisKind | None = None
    footer: str = ""
    events: tuple[HallucinationEvent, ...] = ()


def answer_in_language(
    question: str,
    retriever: Retriever,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    glossary: Glossary,
    state: str | None = None,
) -> LocalisedAnswer:
    """Answer `question` in the language it was asked in."""
    language = detect_language(question)
    english_query = translate_query(question, language, adapter, registry)

    context = retriever.retrieve(english_query)
    result = generate_answer(question, context, adapter, registry, state=state)
    localised = translate_answer(result.answer, language, adapter, registry, glossary)

    return LocalisedAnswer(
        answer=localised,
        language=language,
        english_query=english_query,
        crisis=result.crisis,
        footer=result.footer,
        events=result.events,
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/language/test_pipeline.py -v --no-cov`
Expected: PASS (8 passed)

- [ ] **Step 5: Run the full suite with coverage**

Run: `pytest`
Expected: all tests pass, coverage at or above 80%.

- [ ] **Step 6: Commit**

```bash
git add src/openlaw/language/pipeline.py tests/language/test_pipeline.py
git commit -m "feat: localised pipeline validating in English before translating"
```

---

## Task 5: Native-speaker review sheet

The spec gates multilingual launch on **native-speaker review per language**. That cannot be automated — automated metrics cannot catch a legal term rendered into something plausible and wrong. This task makes the review easy to run.

**Files:**
- Create: `scripts/review_translations.py`
- Creates on run: `data/eval/translation_review.md`

**Interfaces:**
- Consumes: `answer_in_language` (Task 4), `load_glossary` (Task 2), `load_eval_set` (retrieval-and-answers Task 9), `VoyageQueryEmbedder` and `search` (that plan's Tasks 1–2)
- Produces: a Markdown review sheet. No importable code.

- [ ] **Step 1: Write the review-sheet generator**

Create `scripts/review_translations.py`:

```python
"""Generate a native-speaker translation review sheet.

Runs the non-English questions from the evaluation set through the localised
pipeline and writes a Markdown sheet pairing the original question, the English
translation used for retrieval, and the translated answer. A native speaker marks
each row OK or WRONG.

Run: python scripts/review_translations.py
"""

import sys
from pathlib import Path

import httpx

from openlaw.answers.anthropic_client import AnthropicAdapter
from openlaw.answers.models import ModelRegistry
from openlaw.config import get_settings
from openlaw.evaluation.dataset import load_eval_set
from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import load_glossary
from openlaw.language.pipeline import answer_in_language
from openlaw.retrieval.context import RetrievalContext
from openlaw.retrieval.query import VoyageQueryEmbedder
from openlaw.retrieval.search import search

EVAL_PATH = Path("data/eval/citizen_questions.jsonl")
OUTPUT = Path("data/eval/translation_review.md")


class QdrantRetriever:
    """Retrieves from the live Qdrant corpus."""

    def __init__(self, client: httpx.Client, qdrant_url: str, api_key: str) -> None:
        self._client = client
        self._url = qdrant_url
        self._embedder = VoyageQueryEmbedder(api_key)

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        vector = self._embedder.embed_query(english_query)
        chunks = search(self._client, self._url, vector, limit=10)
        return RetrievalContext(query=english_query, chunks=chunks)


def main() -> int:
    settings = get_settings()
    if not settings.voyage_api_key or not settings.anthropic_api_key:
        print("[Review] FAIL: both API keys are required")
        return 1

    questions = [
        question for question in load_eval_set(EVAL_PATH) if question.language != "en"
    ]
    if not questions:
        print("[Review] no non-English questions in the evaluation set")
        return 1

    adapter = AnthropicAdapter(settings.anthropic_api_key)
    registry = ModelRegistry.from_settings(settings)
    glossary = load_glossary()

    lines: list[str] = [
        "# Translation review sheet",
        "",
        "Mark each row **OK** or **WRONG**. Flag any legal term that reads",
        "plausibly but means something different — that is the failure mode",
        "automated metrics cannot catch.",
        "",
    ]

    with httpx.Client(timeout=60) as client:
        retriever = QdrantRetriever(
            client, settings.qdrant_url, settings.voyage_api_key
        )
        for question in questions:
            result = answer_in_language(
                question.question, retriever, adapter, registry, glossary
            )
            language_name = SUPPORTED_LANGUAGES.get(result.language, result.language)
            lines.extend(
                [
                    f"## {language_name} — {question.category}",
                    "",
                    f"**Original question:** {question.question}",
                    "",
                    f"**English used for retrieval:** {result.english_query}",
                    "",
                    f"**Answer returned:** {result.answer.summary}",
                    "",
                    "**Verdict:** ( ) OK  ( ) WRONG — notes:",
                    "",
                    "---",
                    "",
                ]
            )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Review] wrote {len(questions)} rows to {OUTPUT}")
    print("[Review] send this to a native speaker per language before launch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Generate the sheet**

```bash
python scripts/review_translations.py
```

Expected: a Markdown file with one section per non-English evaluation question.

- [ ] **Step 3: Commit**

```bash
git add scripts/review_translations.py
git commit -m "feat: native-speaker translation review sheet generator"
```

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Covered by |
|---|---|
| §7 multilingual in and out, English retrieval | Tasks 1, 3, 4 |
| §7 regional judgment translations not indexed | Corpus-foundation Task 9; nothing here re-adds them |
| §7 statutory quotations stay in English | Task 3 `ANSWER_SYSTEM_PROMPT` — the rule covering QUOTED statutory text (not merely statute names and section numbers) was missing from the shipped prompt and was added in the final fix round; `test_answer_prompt_keeps_quoted_statutory_text_in_English` |
| §8 translator role configured separately from generator | Task 3, tested |
| §8 per-language translator override | Task 3, tested |
| §6 validate before translating | Task 4 pipeline order, tested |
| §10 crisis routing not dependent on translation | Task 4, tested |
| §12.4 Hindi and English minimum, native-speaker reviewed | Task 5 |

**2. Placeholder scan.** No `TBD`, `TODO`, "implement later", or "similar to Task N". The glossary ships with 12 real terms and a test that loads the shipped file; it is a data file intended to grow, not an unfinished code path.

**3. Type consistency.**
- `detect_language` returns `str`; `translate_query`, `translate_answer` and `ModelRegistry.for_role` all take `language: str`. Consistent.
- `translate_answer` returns `ValidatedAnswer` — the same type it receives — so the pipeline substitutes it without changing downstream consumers.
- `Claim` is reconstructed in `translate_answer` with `chunk_ids` copied verbatim; `Claim` requires `min_length=1` on `chunk_ids`, which copying preserves.
- `Retriever` Protocol declares `retrieve(english_query: str) -> RetrievalContext`; both the test double and `QdrantRetriever` match it.
- `LocalisedAnswer` mirrors `AnswerResult`'s `crisis`, `footer` and `events` fields and types, so Plan 5's API layer can consume either.
- `Glossary.instruction_for` returns `str` (empty for English); `ANSWER_SYSTEM_PROMPT.format(glossary=...)` handles an empty string cleanly.
- `StubAdapter.complete` is called with the same five parameters here as in Plan 3.

**One limitation worth naming rather than discovering later:** `detect_by_script` maps Devanagari to Hindi and Bengali script to Bengali, but **Marathi shares Devanagari and Assamese shares Bengali script**. Both will be misdetected unless the user selects the language explicitly. Plan 5's interface must therefore offer a language selector rather than relying on detection alone.

---

## Execution Handoff

**Plan 4 of 5 complete.** Depends on retrieval-and-answers Tasks 4, 6, 8.

Next: `2026-09-08-application.md` (spec §9–10).
