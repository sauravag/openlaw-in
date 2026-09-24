"""Pinned Indian legal terminology.

Some terms have no faithful equivalent outside Indian law. "Anticipatory bail" is
the clearest case: a model asked to render it in Hindi can produce something that
reads plausibly and means something materially different. Terms marked
`preserve_english` stay in English with the accepted local rendering beside them.

This is a correctness measure, not a stylistic one. It does not license rewriting
quoted statutory text: the English text of a statute remains the authoritative
law, and any translation shown alongside it is illustrative only.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_GLOSSARY_PATH = Path("data/glossary/legal_terms.json")


class GlossaryTerm(BaseModel):
    """One pinned legal term and its accepted renderings."""

    model_config = ConfigDict(frozen=True)

    term: str
    preserve_english: bool
    translations: dict[str, str] = Field(default_factory=dict)


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

        lines = [self._instruction_line(term, language) for term in self._terms]
        lines = [line for line in lines if line]

        if not lines:
            return ""
        return "Legal terminology rules:\n" + "\n".join(lines)

    def _instruction_line(self, term: GlossaryTerm, language: str) -> str | None:
        """Return the pinning instruction for one term, or None if it has none."""
        rendering = term.translations.get(language)
        if term.preserve_english:
            if rendering:
                return (
                    f'- Keep "{term.term}" in English, optionally followed by '
                    f'"{rendering}" in brackets.'
                )
            return f'- Keep "{term.term}" in English.'
        if rendering:
            return f'- Translate "{term.term}" as "{rendering}".'
        return None


def load_glossary(path: Path = DEFAULT_GLOSSARY_PATH) -> Glossary:
    """Load the glossary from the JSON file at `path`."""
    if not path.exists():
        raise FileNotFoundError(f"glossary not found at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Glossary(tuple(GlossaryTerm.model_validate(entry) for entry in raw))
