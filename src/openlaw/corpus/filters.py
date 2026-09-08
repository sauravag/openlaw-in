"""Language and duplication filters for judgment chunks.

The Supreme Court corpus is 33.3% English (371,247 of 1,113,313 chunks); the rest
are court-produced translations of the same judgments. Filtering to English retains
35,596 of 37,014 cases — 96.2% — while removing 67% of the volume. High Court
chunks are 100% English, so the filter is a no-op there.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from openlaw.corpus.schema import JudgmentChunk

ENGLISH_CODES: frozenset[str] = frozenset({"en", "eng"})


@dataclass(frozen=True)
class CorpusStats:
    """Counts describing a stream of judgment chunks."""

    total: int
    english: int
    unique_cases: int

    @property
    def english_fraction(self) -> float:
        """Fraction of chunks that are English. 0.0 when there are none."""
        return self.english / self.total if self.total else 0.0


def is_english(chunk: JudgmentChunk) -> bool:
    """Return True when `chunk` is in English."""
    return chunk.language_code.strip().lower() in ENGLISH_CODES


def english_only(chunks: Iterable[JudgmentChunk]) -> Iterator[JudgmentChunk]:
    """Yield only the English chunks in `chunks`."""
    return (chunk for chunk in chunks if is_english(chunk))


def deduplicate(chunks: Iterable[JudgmentChunk]) -> Iterator[JudgmentChunk]:
    """Yield chunks with repeated `chunk_id` values removed, first occurrence kept."""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        yield chunk


def summarise(chunks: Iterable[JudgmentChunk]) -> CorpusStats:
    """Return counts over `chunks`. Consumes the iterable."""
    total = 0
    english = 0
    cases: set[str] = set()
    for chunk in chunks:
        total += 1
        cases.add(chunk.case_id)
        if is_english(chunk):
            english += 1
    return CorpusStats(total=total, english=english, unique_cases=len(cases))
