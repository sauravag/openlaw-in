"""Regex extraction of graph edges from judgment text.

Plan-measured coverage on English Supreme Court chunks: 84.8% contain a case
citation, 48.4% contain a statutory section reference (that section figure was
measured against "Section"/"Sections" only). Re-measured here on random
500-chunk samples of the live `sc_judgments_v1` collection: ~84% citations
(matches the plan closely), ~57% section references — the section rate is
higher because judgments also cite sections via the "s.", "ss.", and "Secs."
abbreviations, each confirmed genuine (not a false positive) by manual
inspection of matched spans. Those abbreviations are also confirmed NOT to
collide with unrelated "Rs.", "Exs.", or "Messrs." abbreviations, or with a
sentence-ending period followed by an unrelated number — see the regression
tests in `tests/graph/test_extract.py`. These two regexes therefore supply the
citation network and the judgment-to-statute links at no LLM cost.

Citations are normalised to `<year><reporter><number>` so the several printed
forms of one reference collapse to a single node key. Extraction is pure regex
over the given text — no I/O, no network calls, no model calls — so results are
deterministic and safe to run over the full corpus offline.
"""

import re
from dataclasses import dataclass

# [1979] 3 S.C.R. 453  ->  1979SCR453
_SCR = re.compile(r"\[?(\d{4})\]?\s*\d*\s*S\.?\s?C\.?\s?R\.?\s*(\d+)", re.IGNORECASE)
# 2019 INSC 398  ->  2019INSC398
_INSC = re.compile(r"(\d{4})\s*_?\s*INSC\s*_?\s*(\d+)", re.IGNORECASE)
# (2016) 8 SCC 984  ->  2016SCC984
_SCC = re.compile(r"\(?(\d{4})\)?\s*\d*\s*SCC\s*(\d+)", re.IGNORECASE)
# AIR 1979 SC 123  ->  1979AIR123
_AIR = re.compile(r"AIR\s*(\d{4})\s*[A-Z]{2,4}\s*(\d+)", re.IGNORECASE)

# Order matters only as a tie-breaker when two patterns match at the same
# text offset; final ordering is otherwise driven by match start position.
_CITATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_INSC, "INSC"),
    (_SCR, "SCR"),
    (_SCC, "SCC"),
    (_AIR, "AIR"),
)

# Section 302 / Sections 138 and 141 / Section 304B / s. 138 / ss. 34 and 149
# / Secs. 34. The `\b` before each abbreviation requires a non-word character
# (or string start) immediately before it, which is what keeps this from
# matching the trailing "s."/"ss." inside "Rs.", "Exs.", or "Messrs." — in all
# three, the letter before the final s/ss is itself a word character, so no
# boundary exists there.
_SECTION = re.compile(
    r"\b(?:Sections?|[Ss]\.|[Ss]s\.|Secs\.)\s+"
    r"(\d+[A-Z]{0,2}(?:\s*(?:,|and)\s*\d+[A-Z]{0,2})*)"
    r"(?:\s+of\s+the\s+([A-Z][A-Za-z\s]{3,60}?Act))?",
    re.IGNORECASE,
)
_SECTION_NUMBER = re.compile(r"\d+[A-Z]{0,2}", re.IGNORECASE)


@dataclass(frozen=True)
class SectionReference:
    """A statutory section referred to in judgment text."""

    number: str
    act_hint: str | None


def extract_case_citations(text: str) -> tuple[str, ...]:
    """Return normalised case citations found in `text`, deduped, in order.

    Each citation is normalised to `<year><reporter><number>` (e.g.
    `1979SCR453`), so the printed forms of one reference collapse to a single
    key. Order follows first appearance in `text`.
    """
    if not text:
        return ()

    found: list[tuple[int, str]] = []
    for pattern, reporter in _CITATION_PATTERNS:
        for match in pattern.finditer(text):
            year, number = match.group(1), match.group(2)
            found.append((match.start(), f"{year}{reporter}{number}"))

    seen: set[str] = set()
    ordered: list[str] = []
    for _, citation in sorted(found):
        if citation not in seen:
            seen.add(citation)
            ordered.append(citation)
    return tuple(ordered)


def extract_section_references(text: str) -> tuple[SectionReference, ...]:
    """Return statutory section references found in `text`, deduped, in order.

    A single match such as "Sections 138 and 141" yields one `SectionReference`
    per number, sharing the same act hint (if any). Order follows first
    appearance in `text`; repeated (number, act_hint) pairs are collapsed.
    """
    if not text:
        return ()

    seen: set[tuple[str, str | None]] = set()
    ordered: list[SectionReference] = []

    for match in _SECTION.finditer(text):
        numbers = _SECTION_NUMBER.findall(match.group(1))
        act_hint = match.group(2).strip() if match.group(2) else None
        for number in numbers:
            key = (number.upper(), act_hint)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(SectionReference(number=number.upper(), act_hint=act_hint))

    return tuple(ordered)
