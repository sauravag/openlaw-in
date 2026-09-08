# OpenLaw.in Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Indian legal knowledge graph in Neo4j from the corpus, and precompute citation authority and doctrinal clusters with GDS.

**Architecture:** An offline batch builder. Edges come from columns that already exist (`acts_referenced`, `defined_terms`, `judges`, `legal_subject`) and from regexes over judgment text (case citations appear in 84.8% of English Supreme Court chunks, statutory section references in 48.4%). No LLM extraction anywhere — it is rejected outright, not deferred (spec §5). After loading, two GDS algorithms run once per corpus release: PageRank over the citation network for authority ranking, and Louvain for doctrinal clustering that the weak `legal_subject` field cannot supply. The graph is read-only at request time.

**Tech Stack:** Python 3.12+, `neo4j` driver (Bolt), Neo4j Community Edition 2026.06.0 with the Graph Data Science plugin, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-openlaw-in-design.md` (§5)

**Depends on:** `docs/superpowers/plans/2026-09-08-corpus-foundation.md` — Tasks 1, 6, 7 (config, record schemas, Parquet readers) must be complete.

## Global Constraints

- **Deterministic only.** No LLM calls anywhere in this plan, and none planned later. Every edge traces to a column or a regex, so the whole graph is reproducible from the Parquet files with no model in the loop and no drift between rebuilds.
- **The graph is derived data.** Dropped and rebuilt per corpus release, never incrementally patched. No migration logic.
- **Read-only at request time.** Writes happen only in the batch builder, so there is no runtime dual-write consistency concern with Postgres.
- **1–2 hop expansion must return in under 100ms**, because it sits in the user's request path.
- **GDS Community Edition caps concurrency at 4 cores.** Irrelevant — PageRank and Louvain are offline batch precomputes over ~1.9M edges.
- **Expected scale:** ~1.3M nodes, ~1.9M edges. Measured edge sources: 543,390 `acts_referenced`, 296,875 `defined_terms`, ~200,000 deduped case citations, ~120,000 judgment→section links, ~105,000 judge/court links.
- **Python style:** type hints everywhere, `pathlib.Path`, f-strings, functions under 50 lines, files under 800 lines.
- **Immutability:** builders return new objects; never mutate inputs.
- **TDD mandatory**, minimum 80% coverage.

---

## File Structure

```
src/openlaw/graph/
├── connection.py      # Neo4j driver lifecycle
├── schema.py          # constraints and indexes (idempotent)
├── extract.py         # regex extraction of citations and section refs
├── nodes.py           # node payload builders (pure)
├── edges.py           # edge payload builders (pure)
├── loader.py          # batched UNWIND writes
├── algorithms.py      # GDS PageRank and Louvain
└── queries.py         # request-path traversal (1-2 hop)

scripts/
├── build_graph.py     # full builder driver
└── run_gds.py         # PageRank + Louvain driver

tests/graph/
├── test_schema.py
├── test_extract.py
├── test_nodes.py
├── test_edges.py
├── test_loader.py
├── test_algorithms.py
└── test_queries.py
```

`extract.py`, `nodes.py` and `edges.py` are pure functions and carry the bulk of the tests. `connection.py`, `loader.py`, `algorithms.py` and `queries.py` touch Neo4j; their Cypher is unit-tested as strings, and the drivers verify behaviour against a live container.

---

## Task 1: Neo4j connection and schema

**Files:**
- Create: `src/openlaw/graph/__init__.py`, `src/openlaw/graph/connection.py`, `src/openlaw/graph/schema.py`
- Create: `tests/graph/__init__.py`, `tests/graph/test_schema.py`
- Modify: `src/openlaw/config.py` — add Neo4j settings
- Modify: `pyproject.toml` — add the `neo4j` dependency
- Modify: `.env.example`

**Interfaces:**
- Consumes: `openlaw.config.Settings` (corpus-foundation Task 1)
- Produces:
  - `Settings.neo4j_uri: str`, `Settings.neo4j_user: str`, `Settings.neo4j_password: str`
  - `connection.graph_driver(settings: Settings) -> Iterator[neo4j.Driver]` — a context manager
  - `schema.CONSTRAINTS: tuple[str, ...]`
  - `schema.INDEXES: tuple[str, ...]`
  - `schema.apply_schema(driver: neo4j.Driver) -> int`

- [ ] **Step 1: Add the dependency and settings**

In `pyproject.toml`, add `"neo4j>=5.26"` to `dependencies`.

In `src/openlaw/config.py`, add three fields to the `Settings` class body:

```python
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "openlawdev"
```

Append to `.env.example`:

```bash
OPENLAW_NEO4J_URI=bolt://localhost:7687
OPENLAW_NEO4J_USER=neo4j
OPENLAW_NEO4J_PASSWORD=openlawdev
```

- [ ] **Step 2: Write the failing test**

Create `tests/graph/test_schema.py`:

```python
from openlaw.graph.schema import CONSTRAINTS, INDEXES


def test_every_node_label_has_a_uniqueness_constraint() -> None:
    """Rebuilds must not create duplicate nodes for the same identifier."""
    joined = " ".join(CONSTRAINTS)
    for label in (
        "Act", "Section", "Judgment", "Court", "Judge", "DefinedTerm", "LegalSubject"
    ):
        assert f":{label}" in joined, f"{label} has no uniqueness constraint"


def test_constraints_are_idempotent() -> None:
    """The builder reruns per corpus release, so DDL must be safe to repeat."""
    for statement in CONSTRAINTS:
        assert "IF NOT EXISTS" in statement


def test_indexes_are_idempotent() -> None:
    for statement in INDEXES:
        assert "IF NOT EXISTS" in statement


def test_judgment_is_indexed_on_the_fields_retrieval_filters_on() -> None:
    joined = " ".join(INDEXES)
    assert "court_code" in joined
    assert "decision_year" in joined


def test_section_is_indexed_for_act_and_number_lookup() -> None:
    joined = " ".join(INDEXES)
    assert "act_id" in joined
    assert "section_number" in joined


def test_pagerank_is_indexed_for_authority_ordering() -> None:
    assert "pagerank" in " ".join(INDEXES)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/graph/test_schema.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph'`

- [ ] **Step 4: Implement `src/openlaw/graph/schema.py`**

```python
"""Neo4j constraints and indexes for the legal knowledge graph.

All DDL is idempotent because the graph is rebuilt from scratch on every corpus
release. Uniqueness constraints prevent duplicate nodes across reruns; indexes
back the filters used by request-path traversal.
"""

import neo4j

CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT act_id IF NOT EXISTS "
    "FOR (a:Act) REQUIRE a.act_id IS UNIQUE",
    "CREATE CONSTRAINT section_id IF NOT EXISTS "
    "FOR (s:Section) REQUIRE s.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT judgment_id IF NOT EXISTS "
    "FOR (j:Judgment) REQUIRE j.case_id IS UNIQUE",
    "CREATE CONSTRAINT court_code IF NOT EXISTS "
    "FOR (c:Court) REQUIRE c.code IS UNIQUE",
    "CREATE CONSTRAINT judge_name IF NOT EXISTS "
    "FOR (g:Judge) REQUIRE g.name IS UNIQUE",
    "CREATE CONSTRAINT term_text IF NOT EXISTS "
    "FOR (t:DefinedTerm) REQUIRE t.term IS UNIQUE",
    "CREATE CONSTRAINT subject_name IF NOT EXISTS "
    "FOR (l:LegalSubject) REQUIRE l.name IS UNIQUE",
)

INDEXES: tuple[str, ...] = (
    "CREATE INDEX judgment_court IF NOT EXISTS FOR (j:Judgment) ON (j.court_code)",
    "CREATE INDEX judgment_year IF NOT EXISTS FOR (j:Judgment) ON (j.decision_year)",
    "CREATE INDEX judgment_authority IF NOT EXISTS FOR (j:Judgment) ON (j.pagerank)",
    "CREATE INDEX judgment_join_key IF NOT EXISTS FOR (j:Judgment) ON (j.join_key)",
    "CREATE INDEX section_act IF NOT EXISTS FOR (s:Section) ON (s.act_id)",
    "CREATE INDEX section_number IF NOT EXISTS FOR (s:Section) ON (s.section_number)",
)


def apply_schema(driver: neo4j.Driver) -> int:
    """Apply every constraint and index. Returns the number of statements run."""
    statements = CONSTRAINTS + INDEXES
    with driver.session() as session:
        for statement in statements:
            session.run(statement)
    return len(statements)
```

- [ ] **Step 5: Implement `src/openlaw/graph/connection.py`**

```python
"""Neo4j driver lifecycle.

The graph is reached over Bolt as a separate service. Neo4j Community Edition is
GPLv3; keeping it behind a network protocol is what lets OpenLaw's own code carry
a permissive licence.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import neo4j

from openlaw.config import Settings


@contextmanager
def graph_driver(settings: Settings) -> Iterator[neo4j.Driver]:
    """Yield a Neo4j driver built from `settings`, closing it on exit."""
    driver = neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        yield driver
    finally:
        driver.close()
```

Also create empty `src/openlaw/graph/__init__.py` and `tests/graph/__init__.py`.

- [ ] **Step 6: Run it to verify it passes**

Run: `pip install -e ".[dev]" && pytest tests/graph/test_schema.py -v --no-cov`
Expected: PASS (6 passed)

- [ ] **Step 7: Apply the schema against a live Neo4j**

```bash
docker compose up -d neo4j
python -c "
from openlaw.config import get_settings
from openlaw.graph.connection import graph_driver
from openlaw.graph.schema import apply_schema
with graph_driver(get_settings()) as driver:
    print(f'applied {apply_schema(driver)} statements')
"
```

Expected: `applied 13 statements`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example src/openlaw/config.py src/openlaw/graph tests/graph
git commit -m "feat: Neo4j connection and idempotent graph schema"
```

---

## Task 2: Citation and section-reference extraction

The two regex-derived edge families. Measured against real data: 84.8% of English Supreme Court chunks contain a case citation, 48.4% contain a statutory section reference.

**Files:**
- Create: `src/openlaw/graph/extract.py`
- Create: `tests/graph/test_extract.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `SectionReference` — frozen dataclass with `number: str`, `act_hint: str | None`
  - `extract_case_citations(text: str) -> tuple[str, ...]` — normalised `<year><reporter><number>`, deduped, first-appearance order
  - `extract_section_references(text: str) -> tuple[SectionReference, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_extract.py`:

```python
from openlaw.graph.extract import (
    SectionReference,
    extract_case_citations,
    extract_section_references,
)


def test_extracts_supreme_court_reports_citation() -> None:
    """The SCR form seen throughout the corpus, e.g. '[1979] 3 S.C.R. 453'."""
    text = "Case: UNION OF INDIA versus K. G. KHOSLA [[1979] 3 S.C.R. 453] (1979)"
    assert "1979SCR453" in extract_case_citations(text)


def test_extracts_neutral_citation() -> None:
    assert "2019INSC398" in extract_case_citations("as held in 2019 INSC 398")


def test_extracts_scc_citation() -> None:
    assert "2016SCC984" in extract_case_citations("reported at (2016) 8 SCC 984")


def test_extracts_air_citation() -> None:
    assert "1979AIR123" in extract_case_citations("see AIR 1979 SC 123")


def test_deduplicates_repeated_citations() -> None:
    text = "see [1979] 3 S.C.R. 453 and again [1979] 3 S.C.R. 453"
    assert extract_case_citations(text).count("1979SCR453") == 1


def test_preserves_first_appearance_order() -> None:
    text = "first 2019 INSC 398 then AIR 1979 SC 123"
    citations = extract_case_citations(text)
    assert citations.index("2019INSC398") < citations.index("1979AIR123")


def test_returns_empty_when_no_citation_present() -> None:
    assert extract_case_citations("the appeal is dismissed with costs") == ()


def test_handles_empty_text() -> None:
    assert extract_case_citations("") == ()


def test_extracts_single_section_reference() -> None:
    refs = extract_section_references("punishable under Section 302 of the Code")
    assert SectionReference(number="302", act_hint=None) in refs


def test_extracts_section_with_letter_suffix() -> None:
    refs = extract_section_references("an offence under Section 304B IPC")
    assert refs[0].number == "304B"


def test_captures_the_act_hint_when_named() -> None:
    refs = extract_section_references(
        "under Section 138 of the Negotiable Instruments Act"
    )
    assert refs[0].act_hint is not None
    assert "Negotiable Instruments" in refs[0].act_hint


def test_extracts_plural_section_references() -> None:
    refs = extract_section_references("under Sections 138 and 141")
    numbers = {ref.number for ref in refs}
    assert "138" in numbers
    assert "141" in numbers


def test_deduplicates_repeated_section_references() -> None:
    refs = extract_section_references("Section 302 ... again Section 302")
    assert len([ref for ref in refs if ref.number == "302"]) == 1


def test_returns_empty_when_no_section_reference() -> None:
    assert extract_section_references("the appeal is dismissed") == ()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_extract.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.extract'`

- [ ] **Step 3: Implement `src/openlaw/graph/extract.py`**

```python
"""Regex extraction of graph edges from judgment text.

Measured coverage on English Supreme Court chunks: 84.8% contain a case citation,
48.4% contain a statutory section reference. These two regexes therefore supply
the citation network and the judgment-to-statute links at no LLM cost.

Citations are normalised to `<year><reporter><number>` so the several printed
forms of one reference collapse to a single node key.
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

_CITATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_INSC, "INSC"),
    (_SCR, "SCR"),
    (_SCC, "SCC"),
    (_AIR, "AIR"),
)

# Section 302 / Sections 138 and 141 / Section 304B
_SECTION = re.compile(
    r"\bSections?\s+(\d+[A-Z]{0,2}(?:\s*(?:,|and)\s*\d+[A-Z]{0,2})*)"
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
    """Return normalised case citations found in `text`, deduped, in order."""
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
    """Return statutory section references found in `text`, deduped, in order."""
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_extract.py -v --no-cov`
Expected: PASS (14 passed)

- [ ] **Step 5: Measure extraction coverage against real data**

```bash
python -c "
from openlaw.config import get_settings
from openlaw.corpus.filters import english_only
from openlaw.datasets.vaquill import read_judgment_chunks
from openlaw.graph.extract import extract_case_citations, extract_section_references

settings = get_settings()
path = settings.vaquill_root / 'in_supreme-court_judgments.parquet'
total = cited = sectioned = 0
for chunk in english_only(read_judgment_chunks(path, settings.corpus_version)):
    total += 1
    if extract_case_citations(chunk.text): cited += 1
    if extract_section_references(chunk.text): sectioned += 1
    if total >= 50000: break
print(f'{total:,} chunks | citations {100*cited/total:.1f}% | sections {100*sectioned/total:.1f}%')
"
```

Expected: citations near **84.8%**, sections near **48.4%**. A large shortfall means the regexes need widening — investigate before proceeding, because the citation network is what PageRank ranks.

- [ ] **Step 6: Commit**

```bash
git add src/openlaw/graph/extract.py tests/graph/test_extract.py
git commit -m "feat: extract case citations and section references from judgment text"
```

---

## Task 3: Node builders

Pure functions turning corpus records into Neo4j node payloads. No I/O.

**Files:**
- Create: `src/openlaw/graph/nodes.py`
- Create: `tests/graph/test_nodes.py`

**Interfaces:**
- Consumes: `JudgmentChunk`, `Provision` (corpus-foundation Task 6)
- Produces:
  - `judgment_node(chunk: JudgmentChunk) -> dict[str, Any]`
  - `judge_nodes(chunk: JudgmentChunk) -> tuple[dict[str, Any], ...]`
  - `court_node(court_code: str) -> dict[str, Any]`
  - `act_node(provision: Provision) -> dict[str, Any]`
  - `section_node(provision: Provision) -> dict[str, Any]`
  - `term_nodes(provision: Provision) -> tuple[dict[str, Any], ...]`
  - `subject_nodes(provision: Provision) -> tuple[dict[str, Any], ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_nodes.py`:

```python
from openlaw.corpus.schema import JudgmentChunk, Provision
from openlaw.graph.nodes import (
    act_node,
    court_node,
    judge_nodes,
    judgment_node,
    section_node,
    subject_nodes,
    term_nodes,
)


def _chunk() -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR", "vide"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def _provision() -> Provision:
    return Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388",
            "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925",
            "section_number": "388",
            "text": "An order of a District Judge ...",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['tax_law', 'family_law']",
            "acts_referenced": ["Indian Succession Act, 1925"],
            "defined_terms": ["Executor", "administrator"],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )


def test_judgment_node_keys_on_case_id_and_carries_the_join_key() -> None:
    node = judgment_node(_chunk())
    assert node["case_id"] == "2016_INSC_1151"
    assert node["join_key"] == "2016INSC1151"


def test_judgment_node_carries_a_decision_year_for_index_filtering() -> None:
    assert judgment_node(_chunk())["decision_year"] == 2016


def test_judgment_node_year_is_none_when_the_date_is_absent() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "c", "case_id": "2016_INSC_1", "chunk_index": 0,
            "text": "t", "text_original": "t", "court_code": "SCI",
            "judges": [], "decision_date": "", "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    node = judgment_node(chunk)
    assert node["decision_year"] is None
    assert node["decision_date"] is None


def test_judge_nodes_exclude_parser_noise() -> None:
    """'vide' is real observed noise in the vaquill judges field."""
    assert {node["name"] for node in judge_nodes(_chunk())} == {"T.S. THAKUR"}


def test_section_node_keys_on_chunk_id_and_links_its_act() -> None:
    node = section_node(_provision())
    assert node["chunk_id"] == "IND_central_2385_388"
    assert node["act_id"] == "IND_central_2385"
    assert node["section_number"] == "388"


def test_section_node_carries_the_source_url() -> None:
    assert section_node(_provision())["source_url"].startswith("https://")


def test_act_node_keys_on_act_id() -> None:
    node = act_node(_provision())
    assert node["act_id"] == "IND_central_2385"
    assert node["title"] == "The Indian Succession Act, 1925"
    assert node["in_force"] is True


def test_court_node_keys_on_code() -> None:
    assert court_node("SCI")["code"] == "SCI"


def test_term_nodes_are_lowercased_for_matching() -> None:
    """'Executor' and 'executor' must collapse to one node."""
    assert {node["term"] for node in term_nodes(_provision())} == {
        "executor",
        "administrator",
    }


def test_subject_nodes_come_from_the_parsed_encoded_list() -> None:
    assert {node["name"] for node in subject_nodes(_provision())} == {
        "tax_law",
        "family_law",
    }


def test_builders_do_not_mutate_their_input() -> None:
    provision = _provision()
    section_node(provision)
    term_nodes(provision)
    assert provision.defined_terms == ("Executor", "administrator")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_nodes.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.nodes'`

- [ ] **Step 3: Implement `src/openlaw/graph/nodes.py`**

```python
"""Node payload builders — pure functions from corpus records to Neo4j rows.

Each builder returns a plain dict suitable for an `UNWIND` batch write. Node key
properties match the uniqueness constraints in `openlaw.graph.schema`.
"""

from typing import Any

from openlaw.corpus.schema import JudgmentChunk, Provision


def judgment_node(chunk: JudgmentChunk) -> dict[str, Any]:
    """Return the `Judgment` node for `chunk`'s case."""
    return {
        "case_id": chunk.case_id,
        "join_key": chunk.join_key,
        "court_code": chunk.court_code,
        "decision_date": (
            chunk.decision_date.isoformat() if chunk.decision_date else None
        ),
        "decision_year": chunk.decision_date.year if chunk.decision_date else None,
        "corpus_version": chunk.corpus_version,
    }


def judge_nodes(chunk: JudgmentChunk) -> tuple[dict[str, Any], ...]:
    """Return one `Judge` node per cleaned judge name on `chunk`."""
    return tuple({"name": name} for name in chunk.judges)


def court_node(court_code: str) -> dict[str, Any]:
    """Return the `Court` node for `court_code`."""
    return {"code": court_code}


def act_node(provision: Provision) -> dict[str, Any]:
    """Return the `Act` node that `provision` belongs to."""
    return {
        "act_id": provision.act_id,
        "title": provision.title,
        "jurisdiction": provision.jurisdiction,
        "in_force": provision.in_force,
        "corpus_version": provision.corpus_version,
    }


def section_node(provision: Provision) -> dict[str, Any]:
    """Return the `Section` node for `provision`."""
    return {
        "chunk_id": provision.chunk_id,
        "act_id": provision.act_id,
        "section_number": provision.section_number,
        "in_force": provision.in_force,
        "source_url": provision.source_url,
        "corpus_version": provision.corpus_version,
    }


def term_nodes(provision: Provision) -> tuple[dict[str, Any], ...]:
    """Return one `DefinedTerm` node per term defined by `provision`, lowercased."""
    return tuple(
        {"term": term.strip().lower()}
        for term in provision.defined_terms
        if term.strip()
    )


def subject_nodes(provision: Provision) -> tuple[dict[str, Any], ...]:
    """Return one `LegalSubject` node per subject tag on `provision`."""
    return tuple({"name": subject} for subject in provision.legal_subjects if subject)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_nodes.py -v --no-cov`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/graph/nodes.py tests/graph/test_nodes.py
git commit -m "feat: node payload builders for the knowledge graph"
```

---

## Task 4: Edge builders

**Files:**
- Create: `src/openlaw/graph/edges.py`
- Create: `tests/graph/test_edges.py`

**Interfaces:**
- Consumes: `JudgmentChunk`, `Provision` (corpus-foundation Task 6), `extract_case_citations`, `extract_section_references` (Task 2), `normalise_identifier` (corpus-foundation Task 3)
- Produces:
  - `EdgeKind` — enum: `CITES`, `INTERPRETS`, `REFERENCES`, `DEFINES`, `HAS_SUBJECT`, `DECIDED_BY`, `IN_COURT`, `PART_OF`
  - `Edge` — frozen dataclass with `source: str`, `target: str`, `kind: EdgeKind`
  - `judgment_edges(chunk: JudgmentChunk) -> tuple[Edge, ...]`
  - `provision_edges(provision: Provision) -> tuple[Edge, ...]`
  - `group_by_kind(edges: Iterable[Edge]) -> dict[EdgeKind, list[Edge]]`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_edges.py`:

```python
from openlaw.corpus.schema import JudgmentChunk, Provision
from openlaw.graph.edges import (
    Edge,
    EdgeKind,
    group_by_kind,
    judgment_edges,
    provision_edges,
)


def _chunk(text: str = "the appeal is dismissed") -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": text,
            "text_original": text,
            "court_code": "SCI",
            "judges": ["T.S. THAKUR"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def _provision() -> Provision:
    return Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388",
            "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925",
            "section_number": "388",
            "text": "text",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['family_law']",
            "acts_referenced": ["Code of Civil Procedure, 1908"],
            "defined_terms": ["Executor"],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )


def test_judgment_links_to_its_court() -> None:
    assert Edge("2016_INSC_1151", "SCI", EdgeKind.IN_COURT) in judgment_edges(_chunk())


def test_judgment_links_to_its_judges() -> None:
    assert Edge(
        "2016_INSC_1151", "T.S. THAKUR", EdgeKind.DECIDED_BY
    ) in judgment_edges(_chunk())


def test_judgment_cites_extracted_citations() -> None:
    edges = judgment_edges(_chunk("following [1979] 3 S.C.R. 453 this Court holds"))
    assert Edge("2016_INSC_1151", "1979SCR453", EdgeKind.CITES) in edges


def test_judgment_interprets_referenced_sections() -> None:
    edges = judgment_edges(_chunk("an offence under Section 302 of the Code"))
    assert Edge("2016_INSC_1151", "302", EdgeKind.INTERPRETS) in edges


def test_judgment_does_not_cite_itself() -> None:
    """A chunk quoting its own citation must not create a self-loop."""
    edges = judgment_edges(_chunk("this case, 2016 INSC 1151, holds"))
    assert not any(
        edge.kind is EdgeKind.CITES and edge.target == "2016INSC1151" for edge in edges
    )


def test_section_is_part_of_its_act() -> None:
    assert Edge(
        "IND_central_2385_388", "IND_central_2385", EdgeKind.PART_OF
    ) in provision_edges(_provision())


def test_section_references_other_acts() -> None:
    assert Edge(
        "IND_central_2385_388", "Code of Civil Procedure, 1908", EdgeKind.REFERENCES
    ) in provision_edges(_provision())


def test_section_defines_its_terms_lowercased() -> None:
    assert Edge(
        "IND_central_2385_388", "executor", EdgeKind.DEFINES
    ) in provision_edges(_provision())


def test_section_has_its_legal_subject() -> None:
    assert Edge(
        "IND_central_2385_388", "family_law", EdgeKind.HAS_SUBJECT
    ) in provision_edges(_provision())


def test_edges_are_deduplicated() -> None:
    edges = judgment_edges(_chunk("see [1979] 3 S.C.R. 453 and [1979] 3 S.C.R. 453"))
    cites = [edge for edge in edges if edge.kind is EdgeKind.CITES]
    assert len(cites) == len(set(cites))


def test_group_by_kind_partitions_edges() -> None:
    grouped = group_by_kind(judgment_edges(_chunk()))
    assert EdgeKind.IN_COURT in grouped
    assert EdgeKind.DECIDED_BY in grouped


def test_group_by_kind_of_empty_input_is_empty() -> None:
    assert group_by_kind([]) == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_edges.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.edges'`

- [ ] **Step 3: Implement `src/openlaw/graph/edges.py`**

```python
"""Edge payload builders — pure functions from corpus records to graph edges.

Edges come from two sources: columns that already exist (`acts_referenced`,
`defined_terms`, `judges`, `legal_subject`) and regexes over judgment text
(`extract_case_citations`, `extract_section_references`). No LLM extraction.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from openlaw.corpus.identifiers import normalise_identifier
from openlaw.corpus.schema import JudgmentChunk, Provision
from openlaw.graph.extract import extract_case_citations, extract_section_references


class EdgeKind(Enum):
    """Relationship types in the legal knowledge graph."""

    CITES = "CITES"
    INTERPRETS = "INTERPRETS"
    REFERENCES = "REFERENCES"
    DEFINES = "DEFINES"
    HAS_SUBJECT = "HAS_SUBJECT"
    DECIDED_BY = "DECIDED_BY"
    IN_COURT = "IN_COURT"
    PART_OF = "PART_OF"


@dataclass(frozen=True)
class Edge:
    """A directed relationship between two node keys."""

    source: str
    target: str
    kind: EdgeKind


def _deduplicate(edges: list[Edge]) -> tuple[Edge, ...]:
    """Return `edges` with duplicates removed, first-occurrence order kept."""
    seen: set[Edge] = set()
    ordered: list[Edge] = []
    for edge in edges:
        if edge in seen:
            continue
        seen.add(edge)
        ordered.append(edge)
    return tuple(ordered)


def judgment_edges(chunk: JudgmentChunk) -> tuple[Edge, ...]:
    """Return every edge derivable from `chunk`, deduplicated and ordered."""
    edges: list[Edge] = [Edge(chunk.case_id, chunk.court_code, EdgeKind.IN_COURT)]
    edges.extend(
        Edge(chunk.case_id, judge, EdgeKind.DECIDED_BY) for judge in chunk.judges
    )

    own_key = normalise_identifier(chunk.case_id)
    edges.extend(
        Edge(chunk.case_id, citation, EdgeKind.CITES)
        for citation in extract_case_citations(chunk.text)
        if normalise_identifier(citation) != own_key
    )
    edges.extend(
        Edge(chunk.case_id, reference.number, EdgeKind.INTERPRETS)
        for reference in extract_section_references(chunk.text)
    )

    return _deduplicate(edges)


def provision_edges(provision: Provision) -> tuple[Edge, ...]:
    """Return every edge derivable from `provision`, deduplicated and ordered."""
    edges: list[Edge] = [Edge(provision.chunk_id, provision.act_id, EdgeKind.PART_OF)]
    edges.extend(
        Edge(provision.chunk_id, act, EdgeKind.REFERENCES)
        for act in provision.acts_referenced
        if act.strip()
    )
    edges.extend(
        Edge(provision.chunk_id, term.strip().lower(), EdgeKind.DEFINES)
        for term in provision.defined_terms
        if term.strip()
    )
    edges.extend(
        Edge(provision.chunk_id, subject, EdgeKind.HAS_SUBJECT)
        for subject in provision.legal_subjects
        if subject
    )

    return _deduplicate(edges)


def group_by_kind(edges: Iterable[Edge]) -> dict[EdgeKind, list[Edge]]:
    """Group `edges` by relationship type for batched writes."""
    grouped: dict[EdgeKind, list[Edge]] = defaultdict(list)
    for edge in edges:
        grouped[edge.kind].append(edge)
    return dict(grouped)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_edges.py -v --no-cov`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/graph/edges.py tests/graph/test_edges.py
git commit -m "feat: edge builders deriving graph relationships from corpus records"
```

---

## Task 5: Batched graph loader and build driver

**Files:**
- Create: `src/openlaw/graph/loader.py`
- Create: `scripts/build_graph.py`
- Create: `tests/graph/test_loader.py`

**Interfaces:**
- Consumes: `Edge`, `EdgeKind` (Task 4), node builders (Task 3), `graph_driver` and `apply_schema` (Task 1)
- Produces:
  - `NODE_MERGE_CYPHER: dict[str, str]` — label → parameterised MERGE statement
  - `edge_merge_cypher(kind: EdgeKind) -> str`
  - `load_nodes(driver: neo4j.Driver, label: str, rows: list[dict], batch_size: int = 10_000) -> int`
  - `load_edges(driver: neo4j.Driver, kind: EdgeKind, edges: list[Edge], batch_size: int = 10_000) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_loader.py`:

```python
import pytest

from openlaw.graph.edges import EdgeKind
from openlaw.graph.loader import NODE_MERGE_CYPHER, edge_merge_cypher


def test_every_node_label_has_a_merge_statement() -> None:
    for label in (
        "Act", "Section", "Judgment", "Court", "Judge", "DefinedTerm", "LegalSubject"
    ):
        assert label in NODE_MERGE_CYPHER


def test_node_merges_use_unwind_for_batching() -> None:
    for statement in NODE_MERGE_CYPHER.values():
        assert "UNWIND $rows" in statement


def test_node_merges_use_merge_not_create() -> None:
    """Rebuilds must be idempotent — CREATE would duplicate on rerun."""
    for statement in NODE_MERGE_CYPHER.values():
        assert "MERGE" in statement
        assert "CREATE (" not in statement


def test_edge_merge_names_the_relationship_type() -> None:
    statement = edge_merge_cypher(EdgeKind.CITES)
    assert ":CITES" in statement
    assert "UNWIND $rows" in statement


def test_edge_merge_covers_every_kind() -> None:
    for kind in EdgeKind:
        assert f":{kind.value}" in edge_merge_cypher(kind)


def test_cites_edges_target_the_judgment_join_key() -> None:
    """Citation targets are normalised keys like '1979SCR453', not case_ids."""
    assert "join_key: row.target" in edge_merge_cypher(EdgeKind.CITES)


def test_edge_merge_rejects_a_non_edgekind() -> None:
    with pytest.raises(TypeError):
        edge_merge_cypher("CITES")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_loader.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.loader'`

- [ ] **Step 3: Implement `src/openlaw/graph/loader.py`**

```python
"""Batched writes into Neo4j using UNWIND.

Every statement uses MERGE rather than CREATE so a rebuild is idempotent. Edge
merges match endpoints on the key properties defined by the schema constraints;
a missing endpoint is created as a stub, which is correct for cited cases that
fall outside the indexed corpus.
"""

import neo4j

from openlaw.graph.edges import Edge, EdgeKind

NODE_MERGE_CYPHER: dict[str, str] = {
    "Act": (
        "UNWIND $rows AS row MERGE (a:Act {act_id: row.act_id}) "
        "SET a.title = row.title, a.jurisdiction = row.jurisdiction, "
        "a.in_force = row.in_force, a.corpus_version = row.corpus_version"
    ),
    "Section": (
        "UNWIND $rows AS row MERGE (s:Section {chunk_id: row.chunk_id}) "
        "SET s.act_id = row.act_id, s.section_number = row.section_number, "
        "s.in_force = row.in_force, s.source_url = row.source_url, "
        "s.corpus_version = row.corpus_version"
    ),
    "Judgment": (
        "UNWIND $rows AS row MERGE (j:Judgment {case_id: row.case_id}) "
        "SET j.join_key = row.join_key, j.court_code = row.court_code, "
        "j.decision_date = row.decision_date, j.decision_year = row.decision_year, "
        "j.corpus_version = row.corpus_version"
    ),
    "Court": "UNWIND $rows AS row MERGE (c:Court {code: row.code})",
    "Judge": "UNWIND $rows AS row MERGE (g:Judge {name: row.name})",
    "DefinedTerm": "UNWIND $rows AS row MERGE (t:DefinedTerm {term: row.term})",
    "LegalSubject": "UNWIND $rows AS row MERGE (l:LegalSubject {name: row.name})",
}

# (source_label, source_key, target_label, target_key) per relationship type.
_ENDPOINTS: dict[EdgeKind, tuple[str, str, str, str]] = {
    EdgeKind.CITES: ("Judgment", "case_id", "Judgment", "join_key"),
    EdgeKind.INTERPRETS: ("Judgment", "case_id", "Section", "section_number"),
    EdgeKind.REFERENCES: ("Section", "chunk_id", "Act", "title"),
    EdgeKind.DEFINES: ("Section", "chunk_id", "DefinedTerm", "term"),
    EdgeKind.HAS_SUBJECT: ("Section", "chunk_id", "LegalSubject", "name"),
    EdgeKind.DECIDED_BY: ("Judgment", "case_id", "Judge", "name"),
    EdgeKind.IN_COURT: ("Judgment", "case_id", "Court", "code"),
    EdgeKind.PART_OF: ("Section", "chunk_id", "Act", "act_id"),
}


def edge_merge_cypher(kind: EdgeKind) -> str:
    """Return the parameterised MERGE statement for edges of `kind`."""
    if not isinstance(kind, EdgeKind):
        raise TypeError(f"expected EdgeKind, got {type(kind).__name__}")

    source_label, source_key, target_label, target_key = _ENDPOINTS[kind]
    return (
        "UNWIND $rows AS row "
        f"MERGE (a:{source_label} {{{source_key}: row.source}}) "
        f"MERGE (b:{target_label} {{{target_key}: row.target}}) "
        f"MERGE (a)-[:{kind.value}]->(b)"
    )


def load_nodes(
    driver: neo4j.Driver, label: str, rows: list[dict], batch_size: int = 10_000
) -> int:
    """Merge `rows` as `label` nodes. Returns the number written."""
    statement = NODE_MERGE_CYPHER[label]
    written = 0
    with driver.session() as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            session.run(statement, rows=batch)
            written += len(batch)
    return written


def load_edges(
    driver: neo4j.Driver, kind: EdgeKind, edges: list[Edge], batch_size: int = 10_000
) -> int:
    """Merge `edges` of `kind`. Returns the number written."""
    statement = edge_merge_cypher(kind)
    rows = [{"source": edge.source, "target": edge.target} for edge in edges]
    written = 0
    with driver.session() as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            session.run(statement, rows=batch)
            written += len(batch)
    return written
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_loader.py -v --no-cov`
Expected: PASS (7 passed)

- [ ] **Step 5: Write the builder driver**

Create `scripts/build_graph.py`:

```python
"""Build the legal knowledge graph in Neo4j from the corpus.

Streams the English Supreme Court judgments and every legislation and regulator
file, derives nodes and edges deterministically, and merges them in batches.
Idempotent — safe to rerun. Run: python scripts/build_graph.py
"""

import sys
from collections import defaultdict

from openlaw.config import get_settings
from openlaw.corpus.filters import english_only
from openlaw.datasets.vaquill import read_judgment_chunks, read_provisions
from openlaw.graph.connection import graph_driver
from openlaw.graph.edges import Edge, EdgeKind, judgment_edges, provision_edges
from openlaw.graph.loader import load_edges, load_nodes
from openlaw.graph.nodes import (
    act_node,
    court_node,
    judge_nodes,
    judgment_node,
    section_node,
    subject_nodes,
    term_nodes,
)
from openlaw.graph.schema import apply_schema


def main() -> int:
    settings = get_settings()

    judgments: dict[str, dict] = {}
    courts: dict[str, dict] = {}
    judges: dict[str, dict] = {}
    acts: dict[str, dict] = {}
    sections: dict[str, dict] = {}
    terms: dict[str, dict] = {}
    subjects: dict[str, dict] = {}
    edges_by_kind: dict[EdgeKind, list[Edge]] = defaultdict(list)
    seen_edges: set[Edge] = set()

    def record(edge: Edge) -> None:
        if edge not in seen_edges:
            seen_edges.add(edge)
            edges_by_kind[edge.kind].append(edge)

    print("[Graph] reading English Supreme Court judgments")
    sc_path = settings.vaquill_root / "in_supreme-court_judgments.parquet"
    for chunk in english_only(
        read_judgment_chunks(sc_path, corpus_version=settings.corpus_version)
    ):
        judgments.setdefault(chunk.case_id, judgment_node(chunk))
        courts.setdefault(chunk.court_code, court_node(chunk.court_code))
        for node in judge_nodes(chunk):
            judges.setdefault(node["name"], node)
        for edge in judgment_edges(chunk):
            record(edge)

    print(f"[Graph] {len(judgments):,} judgments, {len(judges):,} judges")

    print("[Graph] reading legislation and regulator provisions")
    paths = sorted(settings.vaquill_root.glob("in_*_legislation.parquet")) + sorted(
        settings.vaquill_root.glob("in_*_regulations.parquet")
    )
    for path in paths:
        for provision in read_provisions(path, corpus_version=settings.corpus_version):
            sections.setdefault(provision.chunk_id, section_node(provision))
            acts.setdefault(provision.act_id, act_node(provision))
            for node in term_nodes(provision):
                terms.setdefault(node["term"], node)
            for node in subject_nodes(provision):
                subjects.setdefault(node["name"], node)
            for edge in provision_edges(provision):
                record(edge)

    print(f"[Graph] {len(sections):,} sections, {len(acts):,} acts")

    with graph_driver(settings) as driver:
        print(f"[Graph] applied {apply_schema(driver)} schema statements")

        for label, rows in (
            ("Court", list(courts.values())),
            ("Judge", list(judges.values())),
            ("Judgment", list(judgments.values())),
            ("Act", list(acts.values())),
            ("Section", list(sections.values())),
            ("DefinedTerm", list(terms.values())),
            ("LegalSubject", list(subjects.values())),
        ):
            print(f"[Graph] {label}: {load_nodes(driver, label, rows):,} nodes")

        for kind, edges in edges_by_kind.items():
            print(f"[Graph] {kind.value}: {load_edges(driver, kind, edges):,} edges")

    print("[Graph] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the build and check the edge counts**

```bash
docker compose up -d neo4j
python scripts/build_graph.py
```

Expected: roughly 1.3M nodes and 1.9M edges. `REFERENCES` should land near **543,390** and `DEFINES` near **296,875** — a large shortfall means the provision readers are dropping rows and should be investigated before running GDS.

- [ ] **Step 7: Commit**

```bash
git add src/openlaw/graph/loader.py tests/graph/test_loader.py scripts/build_graph.py
git commit -m "feat: batched idempotent graph loader and build driver"
```

---

## Task 6: GDS authority and doctrinal clustering

PageRank over the citation network gives authority ranking — landmark precedents surface by centrality rather than keyword match. Louvain recovers doctrinal clusters that `legal_subject` cannot supply, since 336,224 of 544,078 provisions carry only `general` and no consumer-law tag exists at all.

**Files:**
- Create: `src/openlaw/graph/algorithms.py`
- Create: `scripts/run_gds.py`
- Create: `tests/graph/test_algorithms.py`

**Interfaces:**
- Consumes: `graph_driver` (Task 1)
- Produces:
  - `CITATION_PROJECTION: str` — `"citation_graph"`
  - `PAGERANK_PROPERTY: str` — `"pagerank"`; `LOUVAIN_PROPERTY: str` — `"doctrine_cluster"`
  - `projection_cypher(name: str) -> str`, `pagerank_cypher(name: str) -> str`, `louvain_cypher(name: str) -> str`
  - `project_citation_graph(driver) -> int`, `run_pagerank(driver) -> int`, `run_louvain(driver) -> int`
  - `drop_projection(driver, name: str) -> None`
  - `most_authoritative(driver, limit: int = 10) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_algorithms.py`:

```python
from openlaw.graph.algorithms import (
    CITATION_PROJECTION,
    LOUVAIN_PROPERTY,
    PAGERANK_PROPERTY,
    louvain_cypher,
    pagerank_cypher,
    projection_cypher,
)


def test_projection_names_the_citation_relationship() -> None:
    statement = projection_cypher(CITATION_PROJECTION)
    assert "CITES" in statement
    assert "Judgment" in statement
    assert CITATION_PROJECTION in statement


def test_pagerank_writes_to_the_indexed_property() -> None:
    """schema.INDEXES declares judgment_authority on Judgment.pagerank."""
    assert PAGERANK_PROPERTY == "pagerank"
    assert PAGERANK_PROPERTY in pagerank_cypher(CITATION_PROJECTION)


def test_pagerank_uses_write_mode_not_stream() -> None:
    """Authority must persist on the node for request-path ordering."""
    assert "gds.pageRank.write" in pagerank_cypher(CITATION_PROJECTION)


def test_louvain_writes_a_doctrine_cluster_property() -> None:
    assert LOUVAIN_PROPERTY == "doctrine_cluster"
    assert "gds.louvain.write" in louvain_cypher(CITATION_PROJECTION)
    assert LOUVAIN_PROPERTY in louvain_cypher(CITATION_PROJECTION)


def test_both_algorithms_run_over_the_same_projection() -> None:
    assert CITATION_PROJECTION in pagerank_cypher(CITATION_PROJECTION)
    assert CITATION_PROJECTION in louvain_cypher(CITATION_PROJECTION)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_algorithms.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.algorithms'`

- [ ] **Step 3: Implement `src/openlaw/graph/algorithms.py`**

```python
"""Graph Data Science precomputes: citation authority and doctrinal clusters.

Both run offline, once per corpus release. GDS Community Edition caps concurrency
at 4 cores, which is irrelevant for a batch pass over ~1.9M edges.

PageRank over CITES gives authority — a judgment cited by many judgments that are
themselves heavily cited ranks highly, which is what "landmark" means in practice.

Louvain over the same projection groups judgments that cite each other densely,
recovering doctrinal areas. This matters because `legal_subject` is 62% `general`
and carries no consumer-law tag at all.
"""

import neo4j

CITATION_PROJECTION = "citation_graph"
PAGERANK_PROPERTY = "pagerank"
LOUVAIN_PROPERTY = "doctrine_cluster"


def projection_cypher(name: str) -> str:
    """Return the Cypher projecting the citation graph under `name`."""
    return (
        f"CALL gds.graph.project('{name}', 'Judgment', 'CITES') "
        "YIELD nodeCount RETURN nodeCount"
    )


def pagerank_cypher(name: str) -> str:
    """Return the Cypher writing PageRank scores onto Judgment nodes."""
    return (
        f"CALL gds.pageRank.write('{name}', "
        f"{{writeProperty: '{PAGERANK_PROPERTY}'}}) "
        "YIELD nodePropertiesWritten RETURN nodePropertiesWritten"
    )


def louvain_cypher(name: str) -> str:
    """Return the Cypher writing Louvain community ids onto Judgment nodes."""
    return (
        f"CALL gds.louvain.write('{name}', "
        f"{{writeProperty: '{LOUVAIN_PROPERTY}'}}) "
        "YIELD nodePropertiesWritten RETURN nodePropertiesWritten"
    )


def project_citation_graph(driver: neo4j.Driver) -> int:
    """Project the citation graph into the GDS catalog. Returns node count."""
    with driver.session() as session:
        record = session.run(projection_cypher(CITATION_PROJECTION)).single()
    return int(record["nodeCount"]) if record else 0


def run_pagerank(driver: neo4j.Driver) -> int:
    """Write PageRank authority scores. Returns properties written."""
    with driver.session() as session:
        record = session.run(pagerank_cypher(CITATION_PROJECTION)).single()
    return int(record["nodePropertiesWritten"]) if record else 0


def run_louvain(driver: neo4j.Driver) -> int:
    """Write Louvain doctrinal cluster ids. Returns properties written."""
    with driver.session() as session:
        record = session.run(louvain_cypher(CITATION_PROJECTION)).single()
    return int(record["nodePropertiesWritten"]) if record else 0


def drop_projection(driver: neo4j.Driver, name: str) -> None:
    """Drop the named GDS projection if it exists."""
    with driver.session() as session:
        session.run(f"CALL gds.graph.drop('{name}', false)")


def most_authoritative(driver: neo4j.Driver, limit: int = 10) -> list[dict]:
    """Return the highest-PageRank judgments — a sanity check on the precompute."""
    with driver.session() as session:
        result = session.run(
            "MATCH (j:Judgment) WHERE j.pagerank IS NOT NULL "
            "RETURN j.case_id AS case_id, j.pagerank AS pagerank "
            "ORDER BY j.pagerank DESC LIMIT $limit",
            limit=limit,
        )
        return [dict(record) for record in result]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_algorithms.py -v --no-cov`
Expected: PASS (5 passed)

- [ ] **Step 5: Write the GDS driver**

Create `scripts/run_gds.py`:

```python
"""Run the GDS precomputes: PageRank authority and Louvain doctrinal clusters.

Run after scripts/build_graph.py, once per corpus release.
Run: python scripts/run_gds.py
"""

import sys

from openlaw.config import get_settings
from openlaw.graph.algorithms import (
    CITATION_PROJECTION,
    drop_projection,
    most_authoritative,
    project_citation_graph,
    run_louvain,
    run_pagerank,
)
from openlaw.graph.connection import graph_driver


def main() -> int:
    with graph_driver(get_settings()) as driver:
        drop_projection(driver, CITATION_PROJECTION)

        print(f"[GDS] projected {project_citation_graph(driver):,} Judgment nodes")
        print(f"[GDS] PageRank wrote {run_pagerank(driver):,} properties")
        print(f"[GDS] Louvain wrote {run_louvain(driver):,} properties")

        print("[GDS] most authoritative judgments:")
        for row in most_authoritative(driver):
            print(f"    {row['case_id']:<24} {row['pagerank']:.4f}")

        drop_projection(driver, CITATION_PROJECTION)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the precomputes and sanity-check the output**

```bash
python scripts/run_gds.py
```

Expected: the ten highest-PageRank judgments should be recognisable constitutional landmarks. **If they are procedural or obscure, the `CITES` edges are wrong** — investigate before proceeding, because retrieval will inherit the error.

- [ ] **Step 7: Commit**

```bash
git add src/openlaw/graph/algorithms.py tests/graph/test_algorithms.py scripts/run_gds.py
git commit -m "feat: GDS PageRank authority and Louvain doctrinal clustering"
```

---

## Task 7: Request-path traversal queries

The only part of this plan that runs inside a user request, so it carries a latency budget: **1–2 hop expansion under 100ms**.

**Files:**
- Create: `src/openlaw/graph/queries.py`
- Create: `tests/graph/test_queries.py`

**Interfaces:**
- Consumes: `graph_driver` (Task 1)
- Produces:
  - `MAX_DEPTH: int` — `6`
  - `judgments_interpreting_cypher() -> str`, `sections_for_act_cypher() -> str`, `acts_referenced_by_cypher() -> str`, `expand_chunks_cypher() -> str`, `precedent_lineage_cypher(depth: int) -> str`
  - `judgments_interpreting(driver, section_number: str, limit: int = 20) -> list[dict]`
  - `sections_for_act(driver, act_id: str, limit: int = 50) -> list[dict]`
  - `acts_referenced_by(driver, chunk_id: str, limit: int = 20) -> list[dict]`
  - `expand_chunks(driver, chunk_ids: list[str], limit: int = 40) -> list[dict]` — the retrieval-time entry point, consumed by the retrieval plan
  - `precedent_lineage(driver, case_id: str, depth: int = 3, limit: int = 25) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/graph/test_queries.py`:

```python
import pytest

from openlaw.graph.queries import (
    MAX_DEPTH,
    expand_chunks_cypher,
    judgments_interpreting_cypher,
    precedent_lineage_cypher,
    sections_for_act_cypher,
)


def test_judgments_interpreting_orders_by_authority() -> None:
    """PageRank ordering is what makes landmark precedent surface first."""
    statement = judgments_interpreting_cypher()
    assert "INTERPRETS" in statement
    assert "ORDER BY j.pagerank DESC" in statement


def test_judgments_interpreting_is_bounded() -> None:
    assert "LIMIT $limit" in judgments_interpreting_cypher()


def test_sections_for_act_is_bounded() -> None:
    assert "LIMIT $limit" in sections_for_act_cypher()


def test_expand_chunks_stays_within_two_hops() -> None:
    """Request-path expansion must not walk the graph deeply."""
    statement = expand_chunks_cypher()
    assert "*1..2" in statement
    assert "LIMIT $limit" in statement


def test_precedent_lineage_accepts_a_bounded_depth() -> None:
    assert "CITES*1..3" in precedent_lineage_cypher(depth=3)


def test_precedent_lineage_rejects_depth_above_the_cap() -> None:
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=MAX_DEPTH + 1)


def test_precedent_lineage_rejects_non_positive_depth() -> None:
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=0)


def test_max_depth_is_capped_at_six() -> None:
    assert MAX_DEPTH == 6
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/graph/test_queries.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.graph.queries'`

- [ ] **Step 3: Implement `src/openlaw/graph/queries.py`**

```python
"""Request-path graph traversal.

These run inside a user request and must complete in under 100ms, so every query
is bounded in both depth and result count. Deep lineage is available but capped
at MAX_DEPTH.
"""

import neo4j

MAX_DEPTH = 6


def judgments_interpreting_cypher() -> str:
    """Return the Cypher finding judgments that interpret a section."""
    return (
        "MATCH (j:Judgment)-[:INTERPRETS]->"
        "(s:Section {section_number: $section_number}) "
        "RETURN j.case_id AS case_id, j.court_code AS court_code, "
        "j.decision_year AS decision_year, j.pagerank AS pagerank "
        "ORDER BY j.pagerank DESC LIMIT $limit"
    )


def sections_for_act_cypher() -> str:
    """Return the Cypher listing the sections of an Act."""
    return (
        "MATCH (s:Section)-[:PART_OF]->(a:Act {act_id: $act_id}) "
        "RETURN s.chunk_id AS chunk_id, s.section_number AS section_number, "
        "s.source_url AS source_url, s.in_force AS in_force "
        "ORDER BY s.section_number LIMIT $limit"
    )


def acts_referenced_by_cypher() -> str:
    """Return the Cypher listing Acts referenced by a provision."""
    return (
        "MATCH (s:Section {chunk_id: $chunk_id})-[:REFERENCES]->(a:Act) "
        "RETURN a.act_id AS act_id, a.title AS title LIMIT $limit"
    )


def expand_chunks_cypher() -> str:
    """Return the Cypher expanding retrieved chunks by one or two hops.

    The retrieval-time entry point. Depth is fixed at two, which is what keeps it
    inside the 100ms budget.
    """
    return (
        "MATCH (s:Section) WHERE s.chunk_id IN $chunk_ids "
        "MATCH (s)-[*1..2]-(related) "
        "WHERE related:Section OR related:Act OR related:Judgment "
        "RETURN DISTINCT "
        "  coalesce(related.chunk_id, related.act_id, related.case_id) AS id, "
        "  labels(related)[0] AS label, "
        "  related.source_url AS source_url "
        "LIMIT $limit"
    )


def precedent_lineage_cypher(depth: int) -> str:
    """Return the Cypher tracing a citation chain to `depth` hops.

    Raises ValueError when `depth` falls outside 1..MAX_DEPTH.
    """
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}, got {depth}")
    return (
        f"MATCH path = (j:Judgment {{case_id: $case_id}})"
        f"-[:CITES*1..{depth}]->(cited:Judgment) "
        "RETURN DISTINCT cited.case_id AS case_id, cited.pagerank AS pagerank, "
        "length(path) AS hops "
        "ORDER BY hops, cited.pagerank DESC LIMIT $limit"
    )


def _run(driver: neo4j.Driver, statement: str, **parameters: object) -> list[dict]:
    """Execute `statement` and return its records as dicts."""
    with driver.session() as session:
        return [dict(record) for record in session.run(statement, **parameters)]


def judgments_interpreting(
    driver: neo4j.Driver, section_number: str, limit: int = 20
) -> list[dict]:
    """Return judgments interpreting `section_number`, most authoritative first."""
    return _run(
        driver,
        judgments_interpreting_cypher(),
        section_number=section_number,
        limit=limit,
    )


def sections_for_act(driver: neo4j.Driver, act_id: str, limit: int = 50) -> list[dict]:
    """Return the sections belonging to `act_id`."""
    return _run(driver, sections_for_act_cypher(), act_id=act_id, limit=limit)


def acts_referenced_by(
    driver: neo4j.Driver, chunk_id: str, limit: int = 20
) -> list[dict]:
    """Return the Acts referenced by the provision `chunk_id`."""
    return _run(driver, acts_referenced_by_cypher(), chunk_id=chunk_id, limit=limit)


def expand_chunks(
    driver: neo4j.Driver, chunk_ids: list[str], limit: int = 40
) -> list[dict]:
    """Expand `chunk_ids` by one or two hops. The retrieval-time entry point."""
    if not chunk_ids:
        return []
    return _run(driver, expand_chunks_cypher(), chunk_ids=chunk_ids, limit=limit)


def precedent_lineage(
    driver: neo4j.Driver, case_id: str, depth: int = 3, limit: int = 25
) -> list[dict]:
    """Trace the citation chain from `case_id` to `depth` hops."""
    return _run(driver, precedent_lineage_cypher(depth), case_id=case_id, limit=limit)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/graph/test_queries.py -v --no-cov`
Expected: PASS (8 passed)

- [ ] **Step 5: Measure the latency gate**

```bash
python -c "
import time
from openlaw.config import get_settings
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks

with graph_driver(get_settings()) as driver:
    with driver.session() as session:
        ids = [r['id'] for r in session.run(
            'MATCH (s:Section) RETURN s.chunk_id AS id LIMIT 10')]
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        expand_chunks(driver, ids)
        timings.append((time.perf_counter() - start) * 1000)
    timings.sort()
    print(f'median {timings[10]:.1f}ms | p95 {timings[18]:.1f}ms')
"
```

Expected: **p95 under 100ms.** If it exceeds that, confirm the `section_act` and `section_number` indexes exist — a missing index is the usual cause.

- [ ] **Step 6: Run the full suite with coverage**

Run: `pytest`
Expected: all tests pass, coverage at or above 80%.

- [ ] **Step 7: Commit**

```bash
git add src/openlaw/graph/queries.py tests/graph/test_queries.py
git commit -m "feat: bounded request-path graph traversal within 100ms budget"
```

---

## Self-Review

**1. Spec coverage.**

| Spec §5 requirement | Covered by |
|---|---|
| Deterministic graph, no LLM extraction | Global constraints; Tasks 2–4 |
| Nodes: Act, Section, Judgment, Court, Judge, LegalSubject, DefinedTerm | Task 3 |
| Edges: REFERENCES, DEFINES, HAS_SUBJECT, CITES, INTERPRETS, DECIDED_BY, IN_COURT | Task 4 |
| 543,390 act-references, 296,875 defined-terms | Task 5 Step 6 checks against these figures |
| Citation network from 84.8% of chunks | Task 2, verified in Step 5 |
| Judgment→section links from 48.4% | Task 2, verified in Step 5 |
| Neo4j CE 2026.06.0 over Bolt | Task 1 |
| Variable-depth precedent lineage | Task 7 `precedent_lineage` |
| GDS PageRank authority | Task 6 |
| GDS Louvain doctrinal clustering | Task 6 |
| Read-only at request time | Task 7 — all queries are MATCH/RETURN |
| Rebuilt per release, no migrations | Task 5 — MERGE throughout |
| 1–2 hop expansion under 100ms | Task 7 Step 5 measures it |
| GDS CE 4-core cap is irrelevant | Global constraints; Task 6 runs offline |

The spec's `PART_OF` relationship is not named in §5's edge list but is required to attach a Section to its Act; added in Task 4 and documented here as an addition rather than a silent extra.

**2. Placeholder scan.** No `TBD`, `TODO`, "implement later", or "similar to Task N". Every code step is runnable; every test carries real assertions.

**3. Type consistency.**
- `EdgeKind` / `Edge` — defined Task 4; used Task 5 and the build driver. Consistent.
- `judgment_edges` / `provision_edges` return `tuple[Edge, ...]`; `load_edges` takes `list[Edge]`. The driver accumulates into lists, so this holds.
- `NODE_MERGE_CYPHER` keys are exactly the seven labels constrained in `schema.CONSTRAINTS`. Verified matching.
- `PAGERANK_PROPERTY` is `"pagerank"`, matching the `judgment_authority` index in `schema.INDEXES` and `ORDER BY j.pagerank DESC` in `queries.judgments_interpreting_cypher`. Consistent across three modules.
- `graph_driver` is a context manager in Task 1 and used as one in every driver script.
- `extract_case_citations` returns normalised keys such as `1979SCR453`; `EdgeKind.CITES` targets `Judgment.join_key`, which `judgment_node` sets from `normalise_identifier`. Consistent — this is what lets a cited case resolve to an indexed judgment, and it is why `judgment_join_key` is indexed in Task 1.
- `expand_chunks` is the interface the retrieval plan consumes; its signature is fixed here.

---

## Execution Handoff

**Plan 2 of 5 complete.** Depends on corpus-foundation Tasks 1, 6, 7.

Next: `2026-09-08-retrieval-and-answers.md` (spec §6).
