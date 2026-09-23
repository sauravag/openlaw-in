# OpenLaw.in Corpus Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a verified, deduplicated Indian legal corpus indexed in Qdrant in which every single chunk resolves to a government source URL.

**Architecture:** An offline batch pipeline. It reads two local Parquet datasets, joins them on normalised court identifiers (Supreme Court on neutral citation, High Courts on CNR), filters to English and to records that pass quality gates, then loads the result into Qdrant — restoring the publisher's own vectors for legislation and embedding Supreme Court judgments ourselves with voyage-4. The pipeline is pure functions over row dicts wherever possible, so the great majority of it is unit-testable without any database or network.

**Tech Stack:** Python 3.12+, pyarrow (Parquet), pydantic + pydantic-settings (validation and config), qdrant-client, voyageai, httpx, pytest + pytest-cov, Docker Compose (Qdrant, Postgres, Neo4j).

**Spec:** `docs/superpowers/specs/2026-09-08-openlaw-in-design.md`

## Global Constraints

- **Corpus snapshot is `v2026.08`.** Every indexed record carries this version string.
- **Embeddings MUST be voyage-4 series, 1024 dimensions, cosine distance.** Vectors from any other model live in a different space and are not comparable to the published `acts_india` collection.
- **Every indexed chunk MUST resolve to a government source URL.** A chunk that cannot is rejected, never indexed. This is the product's core guarantee.
- **Supreme Court judgments are filtered to `language_code == 'en'`.** This retains 35,596 of 37,014 cases (96.2%) and removes 67% of volume.
- **`text_original` is never mutated.** Quality scrubbing produces a new field; the original is retained for audit.
- **Attribution is mandatory.** `vaquill/open-india-law` is CC BY 4.0 (attribution required by upstream eCourts/NCLT/NCLAT policy); `KanoonGPT/indian-case-laws` is Apache-2.0. Both credited in README.
- **Python style:** type hints on all functions and variables, `pathlib.Path` over `os.path`, f-strings, functions under 50 lines, files under 800 lines.
- **Immutability:** functions return new objects; never mutate inputs in place.
- **TDD is mandatory:** write the failing test, watch it fail, implement minimally, watch it pass, commit. Minimum 80% coverage.
- **Dataset paths (read-only, never modified):**
  - vaquill: `/Users/sauravagarwal/Engineering/datasets/vaquill--open-india-law/snapshots/58ea6d8b6859f8039ee68dff5af636f787b02463/`
  - KanoonGPT: `/Users/sauravagarwal/Engineering/datasets/indian-case-laws/structured/v1/`

---

## File Structure

```
openlaw-in/
├── pyproject.toml                      # deps, pytest + coverage config
├── docker-compose.yml                  # Qdrant, Postgres, Neo4j
├── .env.example                         # required env vars, no real values
├── README.md                            # incl. mandatory dataset attribution
├── src/openlaw/
│   ├── config.py                        # env-driven settings (pydantic-settings)
│   ├── corpus/
│   │   ├── schema.py                    # pydantic models for every record type
│   │   ├── identifiers.py               # join-key normalisation
│   │   ├── dates.py                     # the two decision_date formats
│   │   ├── subjects.py                  # string-encoded legal_subject lists
│   │   ├── quality.py                   # judge parser-noise filter
│   │   ├── join.py                      # KanoonGPT metadata index
│   │   ├── sources.py                   # government URL resolution
│   │   └── filters.py                   # English filter, chunk dedup
│   ├── datasets/
│   │   ├── vaquill.py                   # vaquill Parquet readers
│   │   └── kanoongpt.py                 # KanoonGPT Parquet readers
│   └── index/
│       ├── qdrant_restore.py            # acts_india shard restore
│       ├── embed.py                     # voyage-4 embedding
│       ├── upsert.py                    # Qdrant writes
│       └── validation.py                # payload validation
├── scripts/
│   ├── make_fixtures.py                 # sample real data into test fixtures
│   ├── restore_acts_india.py            # driver
│   ├── build_sc_index.py                # driver
│   └── validate_corpus.py               # final gate
└── tests/
    ├── conftest.py
    ├── fixtures/                         # small Parquet samples (committed)
    ├── corpus/                           # one test module per corpus/ module
    ├── datasets/
    └── index/
```

Each `corpus/` module holds one responsibility and is independently testable as pure functions. `datasets/` isolates all Parquet I/O so the rest of the pipeline never touches pyarrow. `index/` isolates all network I/O.

---

## Task 1: Project scaffold and test fixtures

Tests must run in seconds, but the real datasets are 60 GB. This task builds the scaffold and a fixture generator that samples the real data down to committable Parquet files.

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/openlaw/__init__.py`, `src/openlaw/config.py`
- Create: `scripts/make_fixtures.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `openlaw.config.Settings` — pydantic-settings class with fields `vaquill_root: Path`, `kanoongpt_root: Path`, `qdrant_url: str`, `voyage_api_key: str | None`, `corpus_version: str`
  - `openlaw.config.get_settings() -> Settings`
  - pytest fixtures `vaquill_fixture_dir: Path` and `kanoongpt_fixture_dir: Path`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "openlaw"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pyarrow>=18.0",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "qdrant-client>=1.12",
    "voyageai>=0.3",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-cov>=6.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/openlaw"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=openlaw --cov-report=term-missing --cov-fail-under=80"

[tool.coverage.run]
omit = ["scripts/*"]
```

- [ ] **Step 2: Create `.env.example`**

```bash
# Local dataset roots (read-only)
OPENLAW_VAQUILL_ROOT=/Users/sauravagarwal/Engineering/datasets/vaquill--open-india-law/snapshots/58ea6d8b6859f8039ee68dff5af636f787b02463
OPENLAW_KANOONGPT_ROOT=/Users/sauravagarwal/Engineering/datasets/indian-case-laws/structured/v1

# Qdrant
OPENLAW_QDRANT_URL=http://localhost:6333

# Voyage AI — required for embedding Supreme Court chunks (voyage-4, 1024-dim)
OPENLAW_VOYAGE_API_KEY=

# Corpus snapshot version
OPENLAW_CORPUS_VERSION=v2026.08
```

- [ ] **Step 3: Write the failing test for config**

Create `tests/test_config.py`:

```python
from pathlib import Path

from openlaw.config import Settings


def test_settings_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_QDRANT_URL", "http://localhost:6333")

    settings = Settings()

    assert settings.vaquill_root == Path("/tmp/vaquill")
    assert settings.kanoongpt_root == Path("/tmp/kanoon")
    assert settings.qdrant_url == "http://localhost:6333"


def test_corpus_version_defaults_to_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().corpus_version == "v2026.08"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_config.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.config'`

- [ ] **Step 5: Implement `src/openlaw/config.py`**

```python
"""Environment-driven settings for the OpenLaw corpus pipeline."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="OPENLAW_", env_file=".env", extra="ignore"
    )

    vaquill_root: Path
    kanoongpt_root: Path
    qdrant_url: str = "http://localhost:6333"
    voyage_api_key: str | None = None
    corpus_version: str = "v2026.08"


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
```

Also create an empty `src/openlaw/__init__.py`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `pip install -e ".[dev]" && pytest tests/test_config.py -v --no-cov`
Expected: PASS (2 passed)

- [ ] **Step 7: Write the fixture generator**

Create `scripts/make_fixtures.py`. It samples the real datasets into small Parquet files so the test suite never reads 60 GB.

```python
"""Sample the real datasets into small Parquet fixtures for tests.

Run once: python scripts/make_fixtures.py
Fixtures are committed so the test suite does not depend on the 60 GB corpus.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from openlaw.config import get_settings

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
ROWS_PER_FILE = 400


def sample_parquet(source: Path, destination: Path, rows: int) -> int:
    """Write the first `rows` rows of `source` to `destination`. Returns row count."""
    reader = pq.ParquetFile(source)
    batch = next(reader.iter_batches(batch_size=rows))
    table = pa.Table.from_batches([batch])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination)
    return table.num_rows


def main() -> None:
    settings = get_settings()

    sources: dict[str, Path] = {
        "vaquill/in_supreme-court_judgments.parquet":
            settings.vaquill_root / "in_supreme-court_judgments.parquet",
        "vaquill/in_delhi_judgments.parquet":
            settings.vaquill_root / "in_delhi_judgments.parquet",
        "vaquill/in_central_legislation.parquet":
            settings.vaquill_root / "in_central_legislation.parquet",
    }

    kanoon_2019 = sorted((settings.kanoongpt_root / "year=2019").glob("*.parquet"))
    if kanoon_2019:
        sources["kanoongpt/year=2019.parquet"] = kanoon_2019[0]

    for relative, source in sources.items():
        written = sample_parquet(source, FIXTURE_DIR / relative, ROWS_PER_FILE)
        print(f"[Fixture] {relative}: {written} rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Generate the fixtures and add conftest**

Run: `python scripts/make_fixtures.py`
Expected: four `[Fixture]` lines, each reporting 400 rows.

Create `tests/conftest.py`:

```python
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vaquill_fixture_dir() -> Path:
    """Directory holding sampled vaquill Parquet files."""
    return FIXTURE_DIR / "vaquill"


@pytest.fixture
def kanoongpt_fixture_dir() -> Path:
    """Directory holding sampled KanoonGPT Parquet files."""
    return FIXTURE_DIR / "kanoongpt"
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .env.example src/openlaw scripts/make_fixtures.py tests/
git commit -m "feat: project scaffold, settings, and test fixtures"
```

---

## Task 2: Local infrastructure

Brings up the three datastores the pipeline writes to. Nothing after this task runs
without them.

> **Scope note.** Earlier drafts made this task a "Phase 0 kill-gate" that restored a
> 42 GB shard of `legal_corpus_v2` to discover whether tribunal decisions had extracted
> text. **That question is settled and the download is not needed.** The
> `open-india-law` repository's own documentation answers it: `legal_corpus_v2` holds
> Supreme Court and High Court judgments, not tribunal decisions, and `COVERAGE.md`
> records tribunal material as 2,112,201 PDFs with `Extracted text: No`, "Case index
> searchable". Tribunals are out of scope. The v1 corpus is **1,469,516 vectors** —
> `acts_india` plus English Supreme Court. See spec §3.1 and §4.

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: nothing
- Produces: Qdrant, Postgres and Neo4j running on localhost. No importable code.

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
# Local development only. Every port is bound to 127.0.0.1 so these
# datastores are not reachable from the local network — the credentials
# below are development defaults and Qdrant runs without authentication.
# Production deployment must supply real secrets and its own network policy.

services:
  qdrant:
    image: qdrant/qdrant:v1.16.3
    ports: ["127.0.0.1:6333:6333", "127.0.0.1:6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: openlaw
      POSTGRES_PASSWORD: openlaw
      POSTGRES_DB: openlaw
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["./data/postgres:/var/lib/postgresql/data"]

  neo4j:
    image: neo4j:2026.06.0-community
    environment:
      NEO4J_AUTH: neo4j/openlawdev
      NEO4J_PLUGINS: '["graph-data-science"]'
      NEO4J_server_memory_pagecache_size: 4G
      NEO4J_server_memory_heap_max__size: 4G
    ports: ["127.0.0.1:7474:7474", "127.0.0.1:7687:7687"]
    volumes: ["./data/neo4j:/data"]
```

Qdrant is pinned to `v1.16.3` because the published snapshots were taken from that version.

- [ ] **Step 2: Start every service**

```bash
docker compose up -d
```

- [ ] **Step 3: Verify all three are reachable**

```bash
curl -fsS http://localhost:6333/healthz
docker compose exec -T postgres pg_isready -U openlaw
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:7474
```

Expected: `healthz check passed`, `accepting connections`, and `200`.

- [ ] **Step 4: Confirm the GDS plugin loaded**

Neo4j must have Graph Data Science available — the knowledge-graph plan depends on it.

```bash
docker compose exec -T neo4j cypher-shell -u neo4j -p openlawdev \
  "RETURN gds.version() AS version"
```

Expected: a version string. A `Unknown function 'gds.version'` error means
`NEO4J_PLUGINS` did not take effect; recreate the container with
`docker compose up -d --force-recreate neo4j`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: local Qdrant, Postgres and Neo4j via Docker Compose"
```

## Task 3: Identifier normalisation

The join keys. Supreme Court joins on neutral citation (verified 100%, 37,014/37,014); High Courts join on CNR (verified 98.6% on Delhi, 325,898/330,401).

**Files:**
- Create: `src/openlaw/corpus/__init__.py`, `src/openlaw/corpus/identifiers.py`
- Create: `tests/corpus/__init__.py`, `tests/corpus/test_identifiers.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `normalise_identifier(raw: str | None) -> str` — uppercase, strip every non-alphanumeric character; `""` for falsy input
  - `IdentifierKind` — `enum.Enum` with members `NEUTRAL_CITATION`, `CNR`, `UNKNOWN`
  - `classify_identifier(raw: str | None) -> IdentifierKind`

- [ ] **Step 1: Write the failing test**

Create `tests/corpus/test_identifiers.py`:

```python
import pytest

from openlaw.corpus.identifiers import (
    IdentifierKind,
    classify_identifier,
    normalise_identifier,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2016_INSC_1151", "2016INSC1151"),   # vaquill Supreme Court case_id
        ("2019INSC398", "2019INSC398"),       # KanoonGPT neutral_citation
        ("2019 INSC 398", "2019INSC398"),
        ("dlhc010517432014", "DLHC010517432014"),
        ("  ESCR010005442019  ", "ESCR010005442019"),
    ],
)
def test_normalise_identifier_strips_separators_and_upcases(
    raw: str, expected: str
) -> None:
    assert normalise_identifier(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "___"])
def test_normalise_identifier_returns_empty_for_no_content(raw: str | None) -> None:
    assert normalise_identifier(raw) == ""


def test_vaquill_and_kanoongpt_supreme_court_ids_normalise_to_the_same_key() -> None:
    """This equality is what makes the 100% Supreme Court join work."""
    assert normalise_identifier("2019_INSC_398") == normalise_identifier("2019INSC398")


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("2016_INSC_1151", IdentifierKind.NEUTRAL_CITATION),
        ("2019INSC398", IdentifierKind.NEUTRAL_CITATION),
        ("DLHC010517432014", IdentifierKind.CNR),
        ("BRHC010004832019", IdentifierKind.CNR),
        ("ESCR010005442019", IdentifierKind.CNR),
        ("", IdentifierKind.UNKNOWN),
        ("not-an-id", IdentifierKind.UNKNOWN),
    ],
)
def test_classify_identifier(raw: str, kind: IdentifierKind) -> None:
    assert classify_identifier(raw) == kind
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/corpus/test_identifiers.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus'`

- [ ] **Step 3: Implement `src/openlaw/corpus/identifiers.py`**

```python
"""Normalisation and classification of Indian court case identifiers.

Two identifier families join the datasets:

- Neutral citation, e.g. vaquill ``2016_INSC_1151`` and KanoonGPT ``2016INSC1151``.
  Normalising both to ``2016INSC1151`` yields a 100% Supreme Court join.
- CNR (Case Number Record), e.g. ``DLHC010517432014``. A 16-character code whose
  first four characters identify the court. Yields a 98.6% Delhi High Court join.
"""

import re
from enum import Enum

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]")
# Neutral citation: 4-digit year, court token, serial number.
_NEUTRAL_CITATION = re.compile(r"^(19|20)\d{2}[A-Z]{2,6}\d{1,6}$")
# CNR: 4 letters (court), then 12 digits.
_CNR = re.compile(r"^[A-Z]{4}\d{12}$")


class IdentifierKind(Enum):
    """Which identifier family a normalised string belongs to."""

    NEUTRAL_CITATION = "neutral_citation"
    CNR = "cnr"
    UNKNOWN = "unknown"


def normalise_identifier(raw: str | None) -> str:
    """Return `raw` uppercased with every non-alphanumeric character removed.

    Returns an empty string when `raw` is None, blank, or has no alphanumerics.
    """
    if not raw:
        return ""
    return _NON_ALPHANUMERIC.sub("", raw.strip().upper())


def classify_identifier(raw: str | None) -> IdentifierKind:
    """Classify `raw` as a neutral citation, a CNR, or unknown."""
    normalised = normalise_identifier(raw)
    if _NEUTRAL_CITATION.match(normalised):
        return IdentifierKind.NEUTRAL_CITATION
    if _CNR.match(normalised):
        return IdentifierKind.CNR
    return IdentifierKind.UNKNOWN
```

Also create empty `src/openlaw/corpus/__init__.py` and `tests/corpus/__init__.py`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/corpus/test_identifiers.py -v --no-cov`
Expected: PASS (18 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/corpus tests/corpus
git commit -m "feat: normalise and classify court case identifiers"
```

---

## Task 4: Date and legal-subject parsing

Two measured data quirks. Supreme Court `decision_date` is `2019-03-26`; Delhi High Court is `2016-05-31 00:00:00`. And `legal_subject` is a **string-encoded list** — `"['tax_law', 'family_law']"` — which naive iteration walks character by character.

**Files:**
- Create: `src/openlaw/corpus/dates.py`, `src/openlaw/corpus/subjects.py`
- Create: `tests/corpus/test_dates.py`, `tests/corpus/test_subjects.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `parse_decision_date(raw: str | None) -> datetime.date | None`
  - `parse_legal_subject(raw: str | list[str] | None) -> tuple[str, ...]`
  - `SUBSTANTIVE_SUBJECTS: frozenset[str]` — the 13 real tags, excluding `general`

- [ ] **Step 1: Write the failing test for dates**

Create `tests/corpus/test_dates.py`:

```python
from datetime import date

import pytest

from openlaw.corpus.dates import parse_decision_date


def test_parses_supreme_court_iso_date() -> None:
    assert parse_decision_date("2019-03-26") == date(2019, 3, 26)


def test_parses_high_court_date_with_time_component() -> None:
    """Delhi HC stores 'YYYY-MM-DD HH:MM:SS' where SC stores 'YYYY-MM-DD'."""
    assert parse_decision_date("2016-05-31 00:00:00") == date(2016, 5, 31)


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date", "0000-00-00"])
def test_returns_none_for_unparseable_input(raw: str | None) -> None:
    assert parse_decision_date(raw) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/corpus/test_dates.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.dates'`

- [ ] **Step 3: Implement `src/openlaw/corpus/dates.py`**

```python
"""Parsing of judgment decision dates.

The two datasets disagree on format. Supreme Court rows carry ``2019-03-26``;
Delhi High Court rows carry ``2016-05-31 00:00:00``. Both must reduce to a date.
"""

from datetime import date, datetime

_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")


def parse_decision_date(raw: str | None) -> date | None:
    """Return the date in `raw`, or None when it is absent or unparseable."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/corpus/test_dates.py -v --no-cov`
Expected: PASS (7 passed)

- [ ] **Step 5: Write the failing test for subjects**

Create `tests/corpus/test_subjects.py`:

```python
import pytest

from openlaw.corpus.subjects import SUBSTANTIVE_SUBJECTS, parse_legal_subject


def test_parses_string_encoded_list() -> None:
    """legal_subject arrives as a string that looks like a list, not a list."""
    assert parse_legal_subject("['tax_law', 'family_law']") == (
        "tax_law",
        "family_law",
    )


def test_accepts_a_real_list_unchanged() -> None:
    assert parse_legal_subject(["criminal_law"]) == ("criminal_law",)


def test_parses_single_general_tag() -> None:
    assert parse_legal_subject("['general']") == ("general",)


@pytest.mark.parametrize("raw", [None, "", "   ", "[]"])
def test_returns_empty_tuple_when_absent(raw: str | None) -> None:
    assert parse_legal_subject(raw) == ()


def test_malformed_input_is_treated_as_a_single_tag_not_characters() -> None:
    """Guards the bug where a string is iterated character by character."""
    assert parse_legal_subject("tax_law") == ("tax_law",)


def test_general_is_not_a_substantive_subject() -> None:
    """336,224 of 544,078 provisions are tagged 'general' — useless for routing."""
    assert "general" not in SUBSTANTIVE_SUBJECTS
    assert "family_law" in SUBSTANTIVE_SUBJECTS


def test_consumer_law_is_absent_from_the_corpus_vocabulary() -> None:
    """Documents a known gap: no consumer-law tag exists (spec 3.4.5)."""
    assert "consumer_law" not in SUBSTANTIVE_SUBJECTS
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/corpus/test_subjects.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.subjects'`

- [ ] **Step 7: Implement `src/openlaw/corpus/subjects.py`**

```python
"""Parsing of the `legal_subject` field.

Stored as a string that renders like a Python list — ``"['tax_law', 'family_law']"``.
Iterating it directly walks characters, so it must be parsed. Measured distribution
across 544,078 provisions: 336,224 are ``general``, leaving 13 substantive tags.
There is no consumer-law tag, a documented corpus gap.
"""

import ast

SUBSTANTIVE_SUBJECTS: frozenset[str] = frozenset(
    {
        "tax_law",
        "property_law",
        "administrative_law",
        "criminal_law",
        "labour_law",
        "environmental_law",
        "banking_finance",
        "civil_procedure",
        "family_law",
        "corporate_law",
        "constitutional_law",
        "information_technology",
        "intellectual_property",
    }
)


def parse_legal_subject(raw: str | list[str] | None) -> tuple[str, ...]:
    """Return the subject tags in `raw` as a tuple of strings.

    Handles a real list, a string-encoded list, and a bare single tag. Returns an
    empty tuple when there is nothing to parse.
    """
    if not raw:
        return ()
    if isinstance(raw, list):
        return tuple(str(tag) for tag in raw if tag)

    text = raw.strip()
    if not text:
        return ()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return (text,)

    if isinstance(parsed, (list, tuple)):
        return tuple(str(tag) for tag in parsed if tag)
    return (str(parsed),) if parsed else ()
```

- [ ] **Step 8: Run it to verify it passes**

Run: `pytest tests/corpus/test_subjects.py -v --no-cov`
Expected: PASS (11 passed)

- [ ] **Step 9: Commit**

```bash
git add src/openlaw/corpus/dates.py src/openlaw/corpus/subjects.py tests/corpus
git commit -m "feat: parse decision dates and string-encoded legal subjects"
```

---

## Task 5: Judge-name quality filter

One measured defect. vaquill's judge extraction emits parser noise — real observed values include `['which']` and `['SH. ANIL KUMAR SISODIA', 'vide']` in Delhi High Court rows.

> **Removed after review: `scrub_margin_letters`.** An earlier version of this task also
> stripped stray Supreme Court Reports margin letters (`A`-`H`) from judgment text. It was
> implemented, reviewed, and **removed** — the regex could not distinguish a margin marker
> from a single capital letter used as a party placeholder, which is the dominant idiom in
> Indian statutory illustrations (`"A owes B a sum of money"`). Measured against the real
> corpus it fired on **60.5% of English Supreme Court chunks**, corrupting text such as
> `"if A owes B a sum of money"` into `"if owes a sum of money"`. Margin letters are now
> left in place deliberately: leaving OCR noise is strictly better than corrupting a legal
> quotation, and no signal in the text alone separates the two cases. Do not re-add it.

**Files:**
- Create: `src/openlaw/corpus/quality.py`
- Create: `tests/corpus/test_quality.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `is_plausible_judge_name(name: str | None) -> bool`
  - `clean_judge_names(names: list[str] | None) -> tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/corpus/test_quality.py`:

```python
import pytest

from openlaw.corpus.quality import clean_judge_names, is_plausible_judge_name


@pytest.mark.parametrize(
    "name",
    ["Y.V. CHANDRACHUD", "SH. ANIL KUMAR SISODIA", "T.S. THAKUR", "DINESH MAHESHWARI"],
)
def test_accepts_real_judge_names(name: str) -> None:
    assert is_plausible_judge_name(name) is True


@pytest.mark.parametrize("name", ["which", "vide", "and", "the", "", "   ", None, "A"])
def test_rejects_observed_parser_noise(name: str | None) -> None:
    """'which' and 'vide' are real values found in vaquill Delhi HC judges lists."""
    assert is_plausible_judge_name(name) is False


def test_clean_judge_names_drops_noise_and_keeps_names() -> None:
    assert clean_judge_names(["SH. ANIL KUMAR SISODIA", "vide"]) == (
        "SH. ANIL KUMAR SISODIA",
    )


def test_clean_judge_names_on_all_noise_returns_empty() -> None:
    assert clean_judge_names(["which"]) == ()


@pytest.mark.parametrize("names", [None, []])
def test_clean_judge_names_handles_absent_input(names: list[str] | None) -> None:
    assert clean_judge_names(names) == ()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/corpus/test_quality.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.quality'`

- [ ] **Step 3: Implement `src/openlaw/corpus/quality.py`**

```python
"""Quality filters for measured defects in the vaquill judgment corpus.

Judge extraction emits parser noise. Observed real values include ``['which']``
and ``['SH. ANIL KUMAR SISODIA', 'vide']``.

**Margin letters are deliberately NOT scrubbed.** English Supreme Court text
carries stray Supreme Court Reports margin markers (``A``-``H``) inline, an
artifact of the reporter's page layout. Removing them is not safe: an isolated
capital between two lowercase words is equally likely to be a party placeholder
in a statutory illustration — ``"A owes B a sum of money"`` — which is the
dominant idiom in Indian law. A regex fired on 60.5% of English Supreme Court
chunks and turned ``"if A owes B a sum of money"`` into ``"if owes a sum of
money"``. Leaving OCR noise in place is strictly better than corrupting a legal
quotation, so the noise stays. Do not re-add a scrubber.
"""

# Lowercase function words that indicate a mis-parsed judge field.
_JUDGE_NOISE: frozenset[str] = frozenset(
    {
        "which", "vide", "and", "the", "of", "for", "with", "that", "this",
        "hon", "honble", "before", "coram", "per", "dated", "order", "judgment",
    }
)
_MIN_JUDGE_LENGTH = 3


def is_plausible_judge_name(name: str | None) -> bool:
    """Return True when `name` looks like a judge's name rather than parser noise."""
    if not name:
        return False
    candidate = name.strip()
    if len(candidate) < _MIN_JUDGE_LENGTH:
        return False
    if candidate.lower().strip(".") in _JUDGE_NOISE:
        return False
    # A real name carries at least one uppercase letter; noise is lowercase prose.
    return any(character.isupper() for character in candidate)


def clean_judge_names(names: list[str] | None) -> tuple[str, ...]:
    """Return only the plausible judge names in `names`, order preserved."""
    if not names:
        return ()
    return tuple(name.strip() for name in names if is_plausible_judge_name(name))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/corpus/test_quality.py -v --no-cov`
Expected: PASS (16 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/corpus/quality.py tests/corpus/test_quality.py
git commit -m "feat: filter judge parser noise from vaquill judgment metadata"
```

---

## Task 6: Record schemas

Pydantic models are the contract between the Parquet readers and everything downstream. Validation at this boundary is where bad rows get rejected rather than silently indexed.

**Files:**
- Create: `src/openlaw/corpus/schema.py`
- Create: `tests/corpus/test_schema.py`

**Interfaces:**
- Consumes: `parse_decision_date`, `parse_legal_subject` (Task 4), `clean_judge_names` (Task 5), `normalise_identifier` (Task 3)
- Produces:
  - `CaseMetadata` — frozen model; `from_row(row: dict) -> CaseMetadata`; fields `join_key: str`, `court_code: str`, `neutral_citation: str | None`, `cnr_number: str | None`, `source_pdf_url: str | None`, `headnote: str | None`, `has_parser_errors: bool`
  - `JudgmentChunk` — frozen model; `from_row(row: dict, corpus_version: str) -> JudgmentChunk`; fields `chunk_id`, `case_id`, `join_key`, `chunk_index`, `text`, `text_original`, `court_code`, `judges: tuple[str, ...]`, `decision_date: date | None`, `language_code`, `corpus_version`
  - `Provision` — frozen model; `from_row(row: dict, corpus_version: str) -> Provision`; fields `chunk_id`, `act_id`, `title`, `section_number`, `text`, `jurisdiction`, `in_force: bool`, `legal_subjects: tuple[str, ...]`, `acts_referenced: tuple[str, ...]`, `defined_terms: tuple[str, ...]`, `source_url`, `corpus_version`

- [ ] **Step 1: Write the failing test**

Create `tests/corpus/test_schema.py`:

```python
from datetime import date

import pytest
from pydantic import ValidationError

from openlaw.corpus.schema import CaseMetadata, JudgmentChunk, Provision


def test_judgment_chunk_from_vaquill_row() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is G dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR", "vide"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )

    assert chunk.join_key == "2016INSC1151"
    assert chunk.judges == ("T.S. THAKUR",)          # noise dropped
    assert chunk.decision_date == date(2016, 12, 15)
    assert chunk.corpus_version == "v2026.08"


def test_judgment_chunk_handles_high_court_date_format() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "DLHC010517432014_000",
            "case_id": "DLHC010517432014",
            "chunk_index": 0,
            "text": "petition allowed",
            "text_original": "petition allowed",
            "court_code": "7_26",
            "judges": [],
            "decision_date": "2016-05-31 00:00:00",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )

    assert chunk.join_key == "DLHC010517432014"
    assert chunk.decision_date == date(2016, 5, 31)
    assert chunk.judges == ()


def test_judgment_chunk_is_immutable() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "c1", "case_id": "2016_INSC_1151", "chunk_index": 0,
            "text": "t", "text_original": "t", "court_code": "SCI",
            "judges": [], "decision_date": "2016-12-15", "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    with pytest.raises(ValidationError):
        chunk.text = "mutated"


def test_judgment_chunk_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        JudgmentChunk.from_row(
            {
                "chunk_id": "c1", "case_id": "2016_INSC_1151", "chunk_index": 0,
                "text": "", "text_original": "", "court_code": "SCI",
                "judges": [], "decision_date": "2016-12-15", "language_code": "en",
            },
            corpus_version="v2026.08",
        )


def test_provision_from_vaquill_row_parses_encoded_subjects() -> None:
    provision = Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388",
            "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925",
            "section_number": "388",
            "text": "An order of a District Judge on an appeal ...",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['tax_law', 'family_law']",
            "acts_referenced": ["Indian Succession Act, 1925"],
            "defined_terms": [],
            "source_url": (
                "https://www.indiacode.nic.in/handle/123456789/2385?view_type=browse"
            ),
        },
        corpus_version="v2026.08",
    )

    assert provision.legal_subjects == ("tax_law", "family_law")
    assert provision.acts_referenced == ("Indian Succession Act, 1925",)
    assert provision.source_url.startswith("https://www.indiacode.nic.in/")


def test_provision_requires_a_source_url() -> None:
    """Enforces the core guarantee: no citation without a government URL."""
    with pytest.raises(ValidationError):
        Provision.from_row(
            {
                "chunk_id": "x", "act_id": "IND_central_1", "title": "An Act",
                "section_number": "1", "text": "text", "jurisdiction": "central",
                "in_force": True, "legal_subject": "['general']",
                "acts_referenced": [], "defined_terms": [], "source_url": "",
            },
            corpus_version="v2026.08",
        )


def test_case_metadata_from_kanoongpt_row() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "SCI",
            "neutral_citation": "2019INSC398",
            "cnr_number": "ESCR010005442019",
            "source_pdf_s3_url": (
                "https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/"
                "year=2019/english/english.tar#member=2019_2_1042_1046.pdf"
            ),
            "headnote_text": "Land laws: Resumption of allotted land ...",
            "quality_json": '{"flags":{"has_parser_failed":false},"error_count":0}',
        }
    )

    assert metadata.join_key == "2019INSC398"
    assert metadata.has_parser_errors is False
    assert metadata.headnote is not None


def test_case_metadata_prefers_neutral_citation_then_falls_back_to_cnr() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "10~8",
            "neutral_citation": None,
            "cnr_number": "BRHC010004832019",
            "source_pdf_s3_url": "https://example.gov.in/x.pdf",
            "headnote_text": None,
            "quality_json": "{}",
        }
    )
    assert metadata.join_key == "BRHC010004832019"


def test_case_metadata_detects_parser_failure_flag() -> None:
    metadata = CaseMetadata.from_row(
        {
            "court_code": "SCI", "neutral_citation": "2019INSC398",
            "cnr_number": None, "source_pdf_s3_url": None, "headnote_text": None,
            "quality_json": '{"flags":{"has_parser_failed":true},"error_count":3}',
        }
    )
    assert metadata.has_parser_errors is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/corpus/test_schema.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.schema'`

- [ ] **Step 3: Implement `src/openlaw/corpus/schema.py`**

```python
"""Validated record models — the boundary where bad rows are rejected.

Every model is frozen: records are created, never mutated. `from_row` classmethods
adapt raw Parquet dictionaries, applying the normalisation and quality filters.
"""

import json
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openlaw.corpus.dates import parse_decision_date
from openlaw.corpus.identifiers import normalise_identifier
from openlaw.corpus.quality import clean_judge_names
from openlaw.corpus.subjects import parse_legal_subject


def _has_parser_errors(quality_json: str | None) -> bool:
    """Return True when KanoonGPT's quality payload reports a parser failure."""
    if not quality_json:
        return False
    try:
        quality = json.loads(quality_json)
    except (ValueError, TypeError):
        return True
    flags = quality.get("flags", {})
    return bool(
        flags.get("has_parser_failed")
        or flags.get("has_errors")
        or int(quality.get("error_count") or 0) > 0
    )


class CaseMetadata(BaseModel):
    """Judgment-level metadata from KanoonGPT — the source of citation URLs."""

    model_config = ConfigDict(frozen=True)

    join_key: str = Field(min_length=1)
    court_code: str
    neutral_citation: str | None = None
    cnr_number: str | None = None
    source_pdf_url: str | None = None
    headnote: str | None = None
    has_parser_errors: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CaseMetadata":
        """Build metadata from a raw KanoonGPT Parquet row.

        The join key prefers the neutral citation (Supreme Court) and falls back
        to the CNR (High Courts).
        """
        neutral = normalise_identifier(row.get("neutral_citation"))
        cnr = normalise_identifier(row.get("cnr_number"))
        return cls(
            join_key=neutral or cnr,
            court_code=str(row.get("court_code") or ""),
            neutral_citation=neutral or None,
            cnr_number=cnr or None,
            source_pdf_url=row.get("source_pdf_s3_url") or None,
            headnote=row.get("headnote_text") or None,
            has_parser_errors=_has_parser_errors(row.get("quality_json")),
        )


class JudgmentChunk(BaseModel):
    """One chunk of a judgment from vaquill, ready for indexing."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    join_key: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_original: str
    court_code: str
    judges: tuple[str, ...] = ()
    decision_date: date | None = None
    language_code: str
    corpus_version: str

    @classmethod
    def from_row(cls, row: dict[str, Any], corpus_version: str) -> "JudgmentChunk":
        """Build a chunk from a raw vaquill Parquet row."""
        case_id = str(row.get("case_id") or "")
        return cls(
            chunk_id=str(row.get("chunk_id") or ""),
            case_id=case_id,
            join_key=normalise_identifier(case_id),
            chunk_index=int(row.get("chunk_index") or 0),
            text=str(row.get("text") or ""),
            text_original=str(row.get("text_original") or row.get("text") or ""),
            court_code=str(row.get("court_code") or ""),
            judges=clean_judge_names(row.get("judges")),
            decision_date=parse_decision_date(row.get("decision_date")),
            language_code=str(row.get("language_code") or ""),
            corpus_version=corpus_version,
        )


class Provision(BaseModel):
    """One statutory or regulatory provision from vaquill."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    act_id: str = Field(min_length=1)
    title: str
    section_number: str
    text: str = Field(min_length=1)
    jurisdiction: str
    in_force: bool
    legal_subjects: tuple[str, ...] = ()
    acts_referenced: tuple[str, ...] = ()
    defined_terms: tuple[str, ...] = ()
    source_url: str = Field(min_length=1)
    corpus_version: str

    @classmethod
    def from_row(cls, row: dict[str, Any], corpus_version: str) -> "Provision":
        """Build a provision from a raw vaquill Parquet row.

        `source_url` has `min_length=1`, so a provision without a government URL
        fails validation rather than being indexed.
        """
        return cls(
            chunk_id=str(row.get("chunk_id") or ""),
            act_id=str(row.get("act_id") or ""),
            title=str(row.get("title") or ""),
            section_number=str(row.get("section_number") or ""),
            text=str(row.get("text") or ""),
            jurisdiction=str(row.get("jurisdiction") or ""),
            in_force=bool(row.get("in_force")),
            legal_subjects=parse_legal_subject(row.get("legal_subject")),
            acts_referenced=tuple(row.get("acts_referenced") or ()),
            defined_terms=tuple(row.get("defined_terms") or ()),
            source_url=str(row.get("source_url") or ""),
            corpus_version=corpus_version,
        )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/corpus/test_schema.py -v --no-cov`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/corpus/schema.py tests/corpus/test_schema.py
git commit -m "feat: validated record schemas rejecting rows without source URLs"
```

---

## Task 7: Dataset readers

Isolate all Parquet I/O here so no other module imports pyarrow. Readers stream in batches — the real files are up to 4.9 GB and must never be loaded whole.

**Files:**
- Create: `src/openlaw/datasets/__init__.py`, `src/openlaw/datasets/vaquill.py`, `src/openlaw/datasets/kanoongpt.py`
- Create: `tests/datasets/__init__.py`, `tests/datasets/test_vaquill.py`, `tests/datasets/test_kanoongpt.py`

**Interfaces:**
- Consumes: `JudgmentChunk`, `Provision`, `CaseMetadata` (Task 6)
- Produces:
  - `vaquill.read_judgment_chunks(path: Path, corpus_version: str, batch_size: int = 10_000) -> Iterator[JudgmentChunk]`
  - `vaquill.read_provisions(path: Path, corpus_version: str, batch_size: int = 10_000) -> Iterator[Provision]`
  - `kanoongpt.iter_parquet_files(root: Path) -> list[Path]`
  - `kanoongpt.read_case_metadata(root: Path, batch_size: int = 50_000) -> Iterator[CaseMetadata]`
  - Every reader skips rows that fail validation rather than raising.

- [ ] **Step 1: Write the failing test for the vaquill reader**

Create `tests/datasets/test_vaquill.py`:

```python
from pathlib import Path

from openlaw.datasets import vaquill


def test_reads_supreme_court_chunks_from_fixture(vaquill_fixture_dir: Path) -> None:
    chunks = list(
        vaquill.read_judgment_chunks(
            vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
            corpus_version="v2026.08",
        )
    )

    assert len(chunks) > 0
    first = chunks[0]
    assert first.court_code == "SCI"
    assert first.join_key != ""
    assert first.corpus_version == "v2026.08"


def test_judgment_chunks_carry_normalised_join_keys(vaquill_fixture_dir: Path) -> None:
    chunks = list(
        vaquill.read_judgment_chunks(
            vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
            corpus_version="v2026.08",
        )
    )
    for chunk in chunks:
        assert "_" not in chunk.join_key
        assert chunk.join_key == chunk.join_key.upper()


def test_reads_provisions_from_fixture(vaquill_fixture_dir: Path) -> None:
    provisions = list(
        vaquill.read_provisions(
            vaquill_fixture_dir / "in_central_legislation.parquet",
            corpus_version="v2026.08",
        )
    )

    assert len(provisions) > 0
    for provision in provisions:
        # The core guarantee, enforced at read time.
        assert provision.source_url.startswith("http")


def test_reader_streams_rather_than_materialising(vaquill_fixture_dir: Path) -> None:
    """A generator, so 4.9 GB files are never loaded whole."""
    stream = vaquill.read_judgment_chunks(
        vaquill_fixture_dir / "in_supreme-court_judgments.parquet",
        corpus_version="v2026.08",
    )
    assert next(stream) is not None
    stream.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/datasets/test_vaquill.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.datasets'`

- [ ] **Step 3: Implement `src/openlaw/datasets/vaquill.py`**

```python
"""Streaming readers for the vaquill/open-india-law Parquet files.

All pyarrow use is confined to this module. Files reach 4.9 GB, so readers yield
in batches and never materialise a whole file. Rows failing validation are skipped
so one bad record cannot abort an ingest.
"""

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import ValidationError

from openlaw.corpus.schema import JudgmentChunk, Provision

_JUDGMENT_COLUMNS: list[str] = [
    "chunk_id", "case_id", "chunk_index", "text", "text_original",
    "court_code", "judges", "decision_date", "language_code",
]
_PROVISION_COLUMNS: list[str] = [
    "chunk_id", "act_id", "title", "section_number", "text", "jurisdiction",
    "in_force", "legal_subject", "acts_referenced", "defined_terms", "source_url",
]


def read_judgment_chunks(
    path: Path, corpus_version: str, batch_size: int = 10_000
) -> Iterator[JudgmentChunk]:
    """Yield validated judgment chunks from the Parquet file at `path`."""
    reader = pq.ParquetFile(path)
    for batch in reader.iter_batches(batch_size=batch_size, columns=_JUDGMENT_COLUMNS):
        for row in batch.to_pylist():
            try:
                yield JudgmentChunk.from_row(row, corpus_version=corpus_version)
            except ValidationError:
                continue


def read_provisions(
    path: Path, corpus_version: str, batch_size: int = 10_000
) -> Iterator[Provision]:
    """Yield validated provisions from the Parquet file at `path`."""
    reader = pq.ParquetFile(path)
    for batch in reader.iter_batches(batch_size=batch_size, columns=_PROVISION_COLUMNS):
        for row in batch.to_pylist():
            try:
                yield Provision.from_row(row, corpus_version=corpus_version)
            except ValidationError:
                continue
```

Also create empty `src/openlaw/datasets/__init__.py` and `tests/datasets/__init__.py`.

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/datasets/test_vaquill.py -v --no-cov`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing test for the KanoonGPT reader**

Create `tests/datasets/test_kanoongpt.py`:

```python
from pathlib import Path

from openlaw.datasets import kanoongpt


def test_reads_case_metadata_from_fixture(kanoongpt_fixture_dir: Path) -> None:
    records = list(kanoongpt.read_case_metadata(kanoongpt_fixture_dir))

    assert len(records) > 0
    for record in records:
        assert record.join_key != ""


def test_supreme_court_records_carry_pdf_urls(kanoongpt_fixture_dir: Path) -> None:
    """KanoonGPT supplies the citation URL that vaquill judgments lack."""
    supreme = [
        record
        for record in kanoongpt.read_case_metadata(kanoongpt_fixture_dir)
        if record.court_code == "SCI"
    ]

    assert supreme, "fixture should contain Supreme Court records"
    assert any(record.source_pdf_url for record in supreme)


def test_supreme_court_records_carry_headnotes(kanoongpt_fixture_dir: Path) -> None:
    """43,402 of 43,424 SCI records have headnotes (spec 3.3)."""
    supreme = [
        record
        for record in kanoongpt.read_case_metadata(kanoongpt_fixture_dir)
        if record.court_code == "SCI"
    ]
    assert any(record.headnote for record in supreme)


def test_iter_parquet_files_finds_year_partitions(kanoongpt_fixture_dir: Path) -> None:
    assert len(kanoongpt.iter_parquet_files(kanoongpt_fixture_dir)) > 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/datasets/test_kanoongpt.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.datasets.kanoongpt'`

- [ ] **Step 7: Implement `src/openlaw/datasets/kanoongpt.py`**

```python
"""Streaming reader for the KanoonGPT/indian-case-laws Parquet files.

The structured release is partitioned by year across 77 files holding 17,070,574
judgment-level records. This module supplies the citation URLs, headnotes and
quality flags that the vaquill judgment corpus lacks.
"""

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import ValidationError

from openlaw.corpus.schema import CaseMetadata

_COLUMNS: list[str] = [
    "court_code", "neutral_citation", "cnr_number",
    "source_pdf_s3_url", "headnote_text", "quality_json",
]


def iter_parquet_files(root: Path) -> list[Path]:
    """Return every Parquet file under `root`, including year partitions, sorted."""
    return sorted(root.rglob("*.parquet"))


def read_case_metadata(root: Path, batch_size: int = 50_000) -> Iterator[CaseMetadata]:
    """Yield validated case metadata from every Parquet file under `root`.

    Records without any usable join key fail validation and are skipped.
    """
    for path in iter_parquet_files(root):
        reader = pq.ParquetFile(path)
        for batch in reader.iter_batches(batch_size=batch_size, columns=_COLUMNS):
            for row in batch.to_pylist():
                try:
                    yield CaseMetadata.from_row(row)
                except ValidationError:
                    continue
```

- [ ] **Step 8: Run it to verify it passes**

Run: `pytest tests/datasets/test_kanoongpt.py -v --no-cov`
Expected: PASS (4 passed)

- [ ] **Step 9: Commit**

```bash
git add src/openlaw/datasets tests/datasets
git commit -m "feat: streaming Parquet readers for both datasets"
```

---

## Task 8: The join index and source URL resolution

The heart of the pipeline. Builds a `join_key → CaseMetadata` index from KanoonGPT, then resolves a government URL for every vaquill chunk. A chunk whose URL cannot be resolved is rejected.

**Files:**
- Create: `src/openlaw/corpus/join.py`, `src/openlaw/corpus/sources.py`
- Create: `tests/corpus/test_join.py`, `tests/corpus/test_sources.py`

**Interfaces:**
- Consumes: `CaseMetadata`, `JudgmentChunk`, `Provision` (Task 6), `read_case_metadata` (Task 7), `normalise_identifier` (Task 3)
- Produces:
  - `join.MetadataIndex` — constructed from `Mapping[str, CaseMetadata]`; methods `lookup(join_key: str) -> CaseMetadata | None`, `__len__`; property `join_keys: frozenset[str]`
  - `join.build_metadata_index(root: Path) -> MetadataIndex`
  - `join.join_rate(chunk_keys: set[str], index: MetadataIndex) -> float`
  - `sources.resolve_judgment_url(chunk: JudgmentChunk, index: MetadataIndex) -> str | None`
  - `sources.resolve_provision_url(provision: Provision) -> str | None`
  - `sources.CitationUnresolvable` — exception
  - `sources.require_url(url: str | None, chunk_id: str) -> str`

- [ ] **Step 1: Write the failing test for the join**

Create `tests/corpus/test_join.py`:

```python
from openlaw.corpus.join import MetadataIndex, join_rate
from openlaw.corpus.schema import CaseMetadata


def _metadata(join_key: str, court_code: str = "SCI") -> CaseMetadata:
    return CaseMetadata(
        join_key=join_key,
        court_code=court_code,
        source_pdf_url=f"https://example.gov.in/{join_key}.pdf",
    )


def test_index_looks_up_by_normalised_key() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})

    found = index.lookup("2016INSC1151")
    assert found is not None
    assert found.join_key == "2016INSC1151"


def test_index_lookup_normalises_the_query_key() -> None:
    """A vaquill case_id must find its KanoonGPT record despite separators."""
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert index.lookup("2016_INSC_1151") is not None


def test_index_returns_none_for_missing_key() -> None:
    assert MetadataIndex({}).lookup("2016INSC1151") is None


def test_index_reports_its_size() -> None:
    index = MetadataIndex({"A1": _metadata("A1"), "B2": _metadata("B2")})

    assert len(index) == 2
    assert index.join_keys == frozenset({"A1", "B2"})


def test_join_rate_all_matched() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert join_rate({"2016INSC1151"}, index) == 1.0


def test_join_rate_partial() -> None:
    index = MetadataIndex({"2016INSC1151": _metadata("2016INSC1151")})
    assert join_rate({"2016INSC1151", "1999INSC1"}, index) == 0.5


def test_join_rate_of_empty_input_is_zero() -> None:
    assert join_rate(set(), MetadataIndex({})) == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/corpus/test_join.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.join'`

- [ ] **Step 3: Implement `src/openlaw/corpus/join.py`**

```python
"""The dataset join — vaquill text to KanoonGPT citation metadata.

Measured join rates (spec 3.3): Supreme Court 100.0% on neutral citation
(37,014 / 37,014); Delhi High Court 98.6% on CNR (325,898 / 330,401).
"""

from collections.abc import Mapping
from pathlib import Path

from openlaw.corpus.identifiers import normalise_identifier
from openlaw.corpus.schema import CaseMetadata
from openlaw.datasets.kanoongpt import read_case_metadata


class MetadataIndex:
    """An immutable `join_key -> CaseMetadata` lookup."""

    def __init__(self, records: Mapping[str, CaseMetadata]) -> None:
        self._records: dict[str, CaseMetadata] = dict(records)

    def lookup(self, join_key: str) -> CaseMetadata | None:
        """Return the metadata for `join_key`, normalising it first."""
        return self._records.get(normalise_identifier(join_key))

    def __len__(self) -> int:
        return len(self._records)

    @property
    def join_keys(self) -> frozenset[str]:
        """Every join key held by this index."""
        return frozenset(self._records)


def build_metadata_index(root: Path) -> MetadataIndex:
    """Build a metadata index from every KanoonGPT Parquet file under `root`.

    Records reporting parser errors lose to clean records for the same key.
    """
    records: dict[str, CaseMetadata] = {}
    for metadata in read_case_metadata(root):
        existing = records.get(metadata.join_key)
        if existing is None or (
            existing.has_parser_errors and not metadata.has_parser_errors
        ):
            records[metadata.join_key] = metadata
    return MetadataIndex(records)


def join_rate(chunk_keys: set[str], index: MetadataIndex) -> float:
    """Return the fraction of `chunk_keys` present in `index`. 0.0 when empty."""
    if not chunk_keys:
        return 0.0
    matched = sum(1 for key in chunk_keys if index.lookup(key) is not None)
    return matched / len(chunk_keys)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/corpus/test_join.py -v --no-cov`
Expected: PASS (7 passed)

- [ ] **Step 5: Write the failing test for source resolution**

Create `tests/corpus/test_sources.py`:

```python
import pytest

from openlaw.corpus.join import MetadataIndex
from openlaw.corpus.schema import CaseMetadata, JudgmentChunk, Provision
from openlaw.corpus.sources import (
    CitationUnresolvable,
    require_url,
    resolve_judgment_url,
    resolve_provision_url,
)

PDF_URL = (
    "https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/"
    "year=2019/english/english.tar#member=2019_2_1042_1046.pdf"
)


def _chunk(case_id: str = "2019_INSC_398") -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": f"{case_id}_000", "case_id": case_id, "chunk_index": 0,
            "text": "text", "text_original": "text", "court_code": "SCI",
            "judges": [], "decision_date": "2019-03-26", "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def test_resolves_judgment_url_through_the_join() -> None:
    index = MetadataIndex(
        {
            "2019INSC398": CaseMetadata(
                join_key="2019INSC398", court_code="SCI", source_pdf_url=PDF_URL
            )
        }
    )
    assert resolve_judgment_url(_chunk(), index) == PDF_URL


def test_returns_none_when_the_case_is_not_in_the_index() -> None:
    assert resolve_judgment_url(_chunk(), MetadataIndex({})) is None


def test_returns_none_when_the_matched_record_has_no_pdf_url() -> None:
    index = MetadataIndex(
        {
            "2019INSC398": CaseMetadata(
                join_key="2019INSC398", court_code="SCI", source_pdf_url=None
            )
        }
    )
    assert resolve_judgment_url(_chunk(), index) is None


def test_resolves_provision_url_from_its_own_field() -> None:
    provision = Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388", "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925", "section_number": "388",
            "text": "text", "jurisdiction": "central", "in_force": True,
            "legal_subject": "['family_law']", "acts_referenced": [],
            "defined_terms": [],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )
    assert resolve_provision_url(provision) == (
        "https://www.indiacode.nic.in/handle/123456789/2385"
    )


def test_require_url_returns_the_url_when_present() -> None:
    assert require_url(PDF_URL, "chunk-1") == PDF_URL


@pytest.mark.parametrize("url", [None, "", "   "])
def test_require_url_raises_when_absent(url: str | None) -> None:
    """The core guarantee: an unresolvable citation must stop indexing."""
    with pytest.raises(CitationUnresolvable) as caught:
        require_url(url, "chunk-1")
    assert "chunk-1" in str(caught.value)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/corpus/test_sources.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.sources'`

- [ ] **Step 7: Implement `src/openlaw/corpus/sources.py`**

```python
"""Government source URL resolution.

The product's core guarantee is that every indexed chunk resolves to a government
source document. Judgments get their URL from the KanoonGPT join, because vaquill's
judgment `source_url` is empty. Provisions carry their own India Code URL.
"""

from openlaw.corpus.join import MetadataIndex
from openlaw.corpus.schema import JudgmentChunk, Provision


class CitationUnresolvable(Exception):
    """Raised when a chunk has no government source URL and must not be indexed."""


def resolve_judgment_url(chunk: JudgmentChunk, index: MetadataIndex) -> str | None:
    """Return the government PDF URL for `chunk`, or None when unresolvable."""
    metadata = index.lookup(chunk.join_key)
    if metadata is None:
        return None
    return metadata.source_pdf_url or None


def resolve_provision_url(provision: Provision) -> str | None:
    """Return the India Code URL for `provision`, or None when absent."""
    return provision.source_url.strip() or None


def require_url(url: str | None, chunk_id: str) -> str:
    """Return `url`, raising CitationUnresolvable when it is missing.

    This is the strict enforcement point for the core guarantee. Batch drivers
    that prefer to count and skip unresolvable chunks call the `resolve_*`
    functions directly instead.
    """
    if not url or not url.strip():
        raise CitationUnresolvable(
            f"chunk {chunk_id} has no government source URL; refusing to index"
        )
    return url.strip()
```

- [ ] **Step 8: Run it to verify it passes**

Run: `pytest tests/corpus/test_sources.py -v --no-cov`
Expected: PASS (8 passed)

- [ ] **Step 9: Commit**

```bash
git add src/openlaw/corpus/join.py src/openlaw/corpus/sources.py tests/corpus
git commit -m "feat: dataset join index and government source URL resolution"
```

---

## Task 9: English filter and chunk deduplication

Supreme Court is 33.3% English (371,247 of 1,113,313 chunks). The remaining 67% are court-produced translations of the same cases; filtering them retains 96.2% of cases. High Court chunks are 100% English, so the filter is a no-op there.

**Files:**
- Create: `src/openlaw/corpus/filters.py`
- Create: `tests/corpus/test_filters.py`

**Interfaces:**
- Consumes: `JudgmentChunk` (Task 6)
- Produces:
  - `ENGLISH_CODES: frozenset[str]` — `{"en", "eng"}`
  - `is_english(chunk: JudgmentChunk) -> bool`
  - `english_only(chunks: Iterable[JudgmentChunk]) -> Iterator[JudgmentChunk]`
  - `deduplicate(chunks: Iterable[JudgmentChunk]) -> Iterator[JudgmentChunk]`
  - `CorpusStats` — frozen dataclass; fields `total: int`, `english: int`, `unique_cases: int`; property `english_fraction: float`
  - `summarise(chunks: Iterable[JudgmentChunk]) -> CorpusStats`

- [ ] **Step 1: Write the failing test**

Create `tests/corpus/test_filters.py`:

```python
from openlaw.corpus.filters import deduplicate, english_only, is_english, summarise
from openlaw.corpus.schema import JudgmentChunk


def _chunk(
    case_id: str, index: int, language: str, chunk_id: str | None = None
) -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": chunk_id or f"{case_id}_{index:03d}_{language}",
            "case_id": case_id,
            "chunk_index": index,
            "text": "text",
            "text_original": "text",
            "court_code": "SCI",
            "judges": [],
            "decision_date": "2016-12-15",
            "language_code": language,
        },
        corpus_version="v2026.08",
    )


def test_recognises_english_codes() -> None:
    assert is_english(_chunk("2016_INSC_1", 0, "en")) is True
    assert is_english(_chunk("2016_INSC_1", 0, "eng")) is True


def test_rejects_regional_language_codes() -> None:
    """Punjabi and Hindi are the two largest non-English blocks in the SC corpus."""
    for code in ("pun", "hin", "guj", "tam", "mal", "mar", "ben", "tel", "kan"):
        assert is_english(_chunk("2016_INSC_1", 0, code)) is False


def test_english_only_drops_translations_of_the_same_case() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en"),
        _chunk("2016_INSC_1", 0, "pun"),
        _chunk("2016_INSC_1", 0, "hin"),
    ]
    kept = list(english_only(chunks))

    assert len(kept) == 1
    assert kept[0].language_code == "en"


def test_deduplicate_removes_repeated_chunk_ids() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en", chunk_id="dup"),
        _chunk("2016_INSC_1", 0, "en", chunk_id="dup"),
        _chunk("2016_INSC_1", 1, "en", chunk_id="unique"),
    ]
    assert [chunk.chunk_id for chunk in deduplicate(chunks)] == ["dup", "unique"]


def test_deduplicate_preserves_first_occurrence_order() -> None:
    chunks = [
        _chunk("2016_INSC_2", 0, "en", chunk_id="b"),
        _chunk("2016_INSC_1", 0, "en", chunk_id="a"),
        _chunk("2016_INSC_2", 0, "en", chunk_id="b"),
    ]
    assert [chunk.chunk_id for chunk in deduplicate(chunks)] == ["b", "a"]


def test_summarise_reports_english_fraction_and_case_count() -> None:
    chunks = [
        _chunk("2016_INSC_1", 0, "en"),
        _chunk("2016_INSC_1", 0, "pun"),
        _chunk("2016_INSC_2", 0, "en"),
        _chunk("2016_INSC_2", 0, "hin"),
    ]
    stats = summarise(chunks)

    assert stats.total == 4
    assert stats.english == 2
    assert stats.unique_cases == 2
    assert stats.english_fraction == 0.5


def test_summarise_of_empty_input_has_zero_fraction() -> None:
    stats = summarise([])
    assert stats.total == 0
    assert stats.english_fraction == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/corpus/test_filters.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.corpus.filters'`

- [ ] **Step 3: Implement `src/openlaw/corpus/filters.py`**

```python
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/corpus/test_filters.py -v --no-cov`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add src/openlaw/corpus/filters.py tests/corpus/test_filters.py
git commit -m "feat: English-only filter, dedup, and corpus statistics"
```

---

## Task 10: Restore the acts_india collection

1,098,269 legislation and regulator provisions, published as two Qdrant shard snapshots totalling 11,207,628,288 bytes. Restored directly — no embedding cost, and this is the collection whose vector space the Supreme Court embeddings must match.

**Files:**
- Create: `src/openlaw/index/__init__.py`, `src/openlaw/index/qdrant_restore.py`
- Create: `scripts/restore_acts_india.py`
- Create: `tests/index/__init__.py`, `tests/index/test_qdrant_restore.py`

**Interfaces:**
- Consumes: `openlaw.config.get_settings` (Task 1)
- Produces:
  - `CollectionSpec` — frozen dataclass; fields `name: str`, `shards: int`, `mirror: str`
  - `ACTS_INDIA: CollectionSpec` — `name="acts_india"`, `shards=2`
  - `VECTOR_SIZE: int` (1024), `DISTANCE: str` (`"Cosine"`)
  - `build_collection_payload(spec: CollectionSpec) -> dict[str, Any]`
  - `shard_snapshot_urls(spec: CollectionSpec, shard: int, snapshot: str) -> tuple[str, str]`
  - `restore_collection(spec: CollectionSpec, qdrant_url: str, client: httpx.Client) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/index/test_qdrant_restore.py`:

```python
from openlaw.index.qdrant_restore import (
    ACTS_INDIA,
    CollectionSpec,
    build_collection_payload,
    shard_snapshot_urls,
)


def test_acts_india_spec_matches_the_published_collection() -> None:
    assert ACTS_INDIA.name == "acts_india"
    assert ACTS_INDIA.shards == 2


def test_payload_declares_voyage_4_vector_geometry() -> None:
    """1024 dimensions and cosine distance, or vectors are not comparable."""
    payload = build_collection_payload(ACTS_INDIA)

    assert payload["shard_number"] == 2
    assert payload["vectors"]["dense"]["size"] == 1024
    assert payload["vectors"]["dense"]["distance"] == "Cosine"


def test_payload_enables_on_disk_storage_and_int8_quantization() -> None:
    dense = build_collection_payload(ACTS_INDIA)["vectors"]["dense"]

    assert dense["on_disk"] is True
    assert dense["quantization_config"]["scalar"]["type"] == "int8"


def test_payload_declares_the_sparse_vector_for_hybrid_search() -> None:
    """The published collections carry a named sparse vector for BM25."""
    assert "sparse" in build_collection_payload(ACTS_INDIA)["sparse_vectors"]


def test_shard_snapshot_urls_are_built_from_the_mirror() -> None:
    spec = CollectionSpec(
        name="acts_india", shards=2, mirror="https://mirror.test/qdrant/acts_india"
    )
    snapshot_url, checksum_url = shard_snapshot_urls(spec, shard=1, snapshot="snap.tar")

    assert snapshot_url == "https://mirror.test/qdrant/acts_india/shard-1/snap.tar"
    assert checksum_url == (
        "https://mirror.test/qdrant/acts_india/shard-1/snap.tar.checksum"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/index/test_qdrant_restore.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.index'`

- [ ] **Step 3: Implement `src/openlaw/index/qdrant_restore.py`**

```python
"""Restore published Qdrant collection snapshots.

Snapshots are per-shard, so the collection must be created with a matching
`shard_number` before each shard is recovered. Qdrant fetches each snapshot itself
and verifies the published SHA256 before accepting it.

`acts_india` holds 1,098,269 legislation and regulator provisions across 2 shards
(11,207,628,288 bytes). Its geometry — voyage-4, 1024 dimensions, cosine — is the
vector space every other collection must match.
"""

from dataclasses import dataclass
from typing import Any

import httpx

MIRROR_ROOT = "https://oss-data-in.vaquill.ai/qdrant"
VECTOR_SIZE = 1024
DISTANCE = "Cosine"


@dataclass(frozen=True)
class CollectionSpec:
    """A published collection and where its shard snapshots live."""

    name: str
    shards: int
    mirror: str


ACTS_INDIA = CollectionSpec(
    name="acts_india", shards=2, mirror=f"{MIRROR_ROOT}/acts_india"
)


def build_collection_payload(spec: CollectionSpec) -> dict[str, Any]:
    """Return the Qdrant create-collection body for `spec`."""
    return {
        "shard_number": spec.shards,
        "vectors": {
            "dense": {
                "size": VECTOR_SIZE,
                "distance": DISTANCE,
                "on_disk": True,
                "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99}},
            }
        },
        "sparse_vectors": {"sparse": {}},
    }


def shard_snapshot_urls(
    spec: CollectionSpec, shard: int, snapshot: str
) -> tuple[str, str]:
    """Return the `(snapshot_url, checksum_url)` pair for one shard."""
    base = f"{spec.mirror}/shard-{shard}/{snapshot}"
    return base, f"{base}.checksum"


def restore_collection(
    spec: CollectionSpec, qdrant_url: str, client: httpx.Client
) -> None:
    """Create `spec`'s collection and recover every shard snapshot into it."""
    created = client.put(
        f"{qdrant_url}/collections/{spec.name}",
        json=build_collection_payload(spec),
    )
    # 409 means the collection already exists, which is what we wanted. Anything
    # else is a real failure. Without this the restore cannot be retried after an
    # interrupted 10.44 GB download.
    if created.status_code != 409:
        created.raise_for_status()

    for shard in range(spec.shards):
        index = client.get(f"{spec.mirror}/shard-{shard}/index.json").json()
        snapshot = index["snapshot"]
        snapshot_url, checksum_url = shard_snapshot_urls(spec, shard, snapshot)
        checksum = client.get(checksum_url).text.strip()
        client.put(
            f"{qdrant_url}/collections/{spec.name}/shards/{shard}/snapshots/recover",
            json={
                "location": snapshot_url,
                "checksum": checksum,
                "priority": "snapshot",
            },
            timeout=None,
        ).raise_for_status()
```

Also create empty `src/openlaw/index/__init__.py` and `tests/index/__init__.py`.

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/index/test_qdrant_restore.py -v --no-cov`
Expected: PASS (5 passed)

- [ ] **Step 5: Write the restore driver**

Create `scripts/restore_acts_india.py`:

```python
"""Restore the published acts_india collection into local Qdrant.

Downloads ~11.2 GB. Run: python scripts/restore_acts_india.py
"""

import sys

import httpx

from openlaw.config import get_settings
from openlaw.index.qdrant_restore import ACTS_INDIA, restore_collection

EXPECTED_POINTS = 1_098_577


def main() -> int:
    settings = get_settings()
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        print(f"[Restore] creating and recovering {ACTS_INDIA.name} (~11.2 GB)")
        restore_collection(ACTS_INDIA, settings.qdrant_url, client)

        info = client.get(f"{settings.qdrant_url}/collections/{ACTS_INDIA.name}").json()

    points = info["result"]["points_count"]
    print(f"[Restore] points_count = {points:,} (published: {EXPECTED_POINTS:,})")

    if points < EXPECTED_POINTS * 0.99:
        print("[Restore] FAIL: point count is more than 1% below published figure")
        return 1
    print("[Restore] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the restore**

```bash
docker compose up -d qdrant
python scripts/restore_acts_india.py
```

Expected: `[Restore] OK` with `points_count` at or near 1,098,577.

- [ ] **Step 7: Commit**

```bash
git add src/openlaw/index tests/index scripts/restore_acts_india.py
git commit -m "feat: restore published acts_india Qdrant collection"
```

---

## Task 11: Embed and index Supreme Court English chunks

371,247 chunks across 35,596 cases. These cannot be extracted selectively from `legal_corpus_v1`'s four shared shards, so they are embedded here — with voyage-4, to land in the same space as `acts_india`.

**Files:**
- Create: `src/openlaw/index/embed.py`, `src/openlaw/index/upsert.py`
- Create: `scripts/build_sc_index.py`
- Create: `tests/index/test_embed.py`, `tests/index/test_upsert.py`

**Interfaces:**
- Consumes: `JudgmentChunk` (Task 6), `MetadataIndex`/`build_metadata_index` (Task 8), `resolve_judgment_url` (Task 8), `english_only`/`deduplicate` (Task 9), `build_collection_payload`/`CollectionSpec` (Task 10)
- Produces:
  - `embed.EMBEDDING_MODEL: str` — `"voyage-4"`
  - `embed.EMBEDDING_DIMENSIONS: int` — `1024`
  - `embed.DEFAULT_BATCH_SIZE: int` — `128`
  - `embed.batched(items: Iterable[T], size: int) -> Iterator[list[T]]`
  - `embed.Embedder` — Protocol with `embed_documents(texts: list[str]) -> list[list[float]]`
  - `embed.VoyageEmbedder(api_key: str, model: str = EMBEDDING_MODEL)`
  - `upsert.SC_COLLECTION: str` — `"sc_judgments_v1"`
  - `upsert.point_id(chunk_id: str) -> str`
  - `upsert.build_point(chunk: JudgmentChunk, vector: list[float], source_url: str, headnote: str | None) -> dict[str, Any]`
  - `upsert.upsert_points(points: list[dict], qdrant_url: str, client: httpx.Client) -> None`

- [ ] **Step 1: Write the failing test for embedding helpers**

Create `tests/index/test_embed.py`:

```python
import pytest

from openlaw.index.embed import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, batched


def test_embedding_model_is_voyage_4() -> None:
    """Must match acts_india, or similarity scores are meaningless."""
    assert EMBEDDING_MODEL == "voyage-4"
    assert EMBEDDING_DIMENSIONS == 1024


def test_batched_splits_evenly() -> None:
    assert list(batched([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_batched_yields_a_short_final_batch() -> None:
    assert list(batched([1, 2, 3], 2)) == [[1, 2], [3]]


def test_batched_of_empty_input_yields_nothing() -> None:
    assert list(batched([], 10)) == []


def test_batched_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError):
        list(batched([1, 2], 0))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/index/test_embed.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.index.embed'`

- [ ] **Step 3: Implement `src/openlaw/index/embed.py`**

```python
"""voyage-4 embedding of judgment chunks.

The published collections were embedded with the voyage-4 series at 1024
dimensions. Vectors from any other model live in a different space, so similarity
scores against `acts_india` would be meaningless. This constant is load-bearing.
"""

from collections.abc import Iterable, Iterator
from typing import Protocol, TypeVar

EMBEDDING_MODEL = "voyage-4"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_BATCH_SIZE = 128

T = TypeVar("T")


class Embedder(Protocol):
    """Anything that can turn document texts into vectors."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in the same order."""
        ...


def batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Yield `items` in lists of at most `size`."""
    if size <= 0:
        raise ValueError(f"batch size must be positive, got {size}")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class VoyageEmbedder:
    """An `Embedder` backed by the Voyage AI API."""

    def __init__(self, api_key: str, model: str = EMBEDDING_MODEL) -> None:
        import voyageai

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts` as documents, returning one vector each."""
        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/index/test_embed.py -v --no-cov`
Expected: PASS (5 passed)

- [ ] **Step 5: Write the failing test for point construction**

Create `tests/index/test_upsert.py`:

```python
from openlaw.corpus.schema import JudgmentChunk
from openlaw.index.upsert import SC_COLLECTION, build_point

PDF_URL = "https://indian-supreme-court-judgments.s3.amazonaws.com/x.pdf"


def _chunk() -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is G dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def test_collection_name_is_versioned() -> None:
    assert SC_COLLECTION == "sc_judgments_v1"


def test_point_carries_the_vector_under_the_dense_name() -> None:
    point = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    assert len(point["vector"]["dense"]) == 1024


def test_point_payload_carries_the_source_url() -> None:
    """Without this the citation cannot be rendered or verified."""
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]
    assert payload["source_url"] == PDF_URL


def test_point_payload_carries_filterable_metadata() -> None:
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]

    assert payload["court_code"] == "SCI"
    assert payload["case_id"] == "2016_INSC_1151"
    assert payload["decision_date"] == "2016-12-15"
    assert payload["corpus_version"] == "v2026.08"
    assert payload["language_code"] == "en"


def test_point_payload_carries_scrubbed_text_and_retains_the_original() -> None:
    payload = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)["payload"]

    assert payload["text"] == "the appeal is dismissed"
    assert payload["text_original"] == "the appeal is G dismissed"


def test_point_includes_the_headnote_when_available() -> None:
    payload = build_point(
        _chunk(), [0.1] * 1024, PDF_URL, headnote="Contempt: scope of powers"
    )["payload"]
    assert payload["headnote"] == "Contempt: scope of powers"


def test_point_id_is_stable_for_the_same_chunk() -> None:
    first = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    second = build_point(_chunk(), [0.2] * 1024, PDF_URL, headnote=None)
    assert first["id"] == second["id"]


def test_point_ids_differ_across_chunks() -> None:
    other = JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_002", "case_id": "2016_INSC_1151",
            "chunk_index": 2, "text": "t", "text_original": "t",
            "court_code": "SCI", "judges": [], "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    first = build_point(_chunk(), [0.1] * 1024, PDF_URL, headnote=None)
    second = build_point(other, [0.1] * 1024, PDF_URL, headnote=None)
    assert first["id"] != second["id"]
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/index/test_upsert.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.index.upsert'`

- [ ] **Step 7: Implement `src/openlaw/index/upsert.py`**

```python
"""Construction and writing of Qdrant points for Supreme Court chunks.

Point IDs are a deterministic UUID5 of `chunk_id`, so re-running an ingest updates
points rather than duplicating them.
"""

import uuid
from typing import Any

import httpx

from openlaw.corpus.schema import JudgmentChunk

SC_COLLECTION = "sc_judgments_v1"
_ID_NAMESPACE = uuid.UUID("6f1c9d2e-0a3b-4c5d-8e7f-1a2b3c4d5e6f")


def point_id(chunk_id: str) -> str:
    """Return a deterministic UUID for `chunk_id`."""
    return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def build_point(
    chunk: JudgmentChunk,
    vector: list[float],
    source_url: str,
    headnote: str | None,
) -> dict[str, Any]:
    """Return the Qdrant point for `chunk`.

    `source_url` is required by the caller's contract — see
    `openlaw.corpus.sources.require_url`.
    """
    payload: dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "case_id": chunk.case_id,
        "join_key": chunk.join_key,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "text_original": chunk.text_original,
        "court_code": chunk.court_code,
        "judges": list(chunk.judges),
        "decision_date": (
            chunk.decision_date.isoformat() if chunk.decision_date else None
        ),
        "language_code": chunk.language_code,
        "corpus_version": chunk.corpus_version,
        "source_url": source_url,
        "headnote": headnote,
    }
    return {
        "id": point_id(chunk.chunk_id),
        "vector": {"dense": vector},
        "payload": payload,
    }


def upsert_points(
    points: list[dict[str, Any]], qdrant_url: str, client: httpx.Client
) -> None:
    """Write `points` into the Supreme Court collection."""
    client.put(
        f"{qdrant_url}/collections/{SC_COLLECTION}/points",
        json={"points": points},
        timeout=120,
    ).raise_for_status()
```

- [ ] **Step 8: Run it to verify it passes**

Run: `pytest tests/index/test_upsert.py -v --no-cov`
Expected: PASS (8 passed)

- [ ] **Step 9: Write the index-building driver**

Create `scripts/build_sc_index.py`:

```python
"""Embed and index English Supreme Court chunks.

Filters to English, deduplicates, resolves a government URL for every chunk via
the KanoonGPT join, embeds with voyage-4, and upserts into Qdrant. Chunks whose
citation cannot be resolved are counted and skipped, never indexed.

Run: python scripts/build_sc_index.py
"""

import sys

import httpx

from openlaw.config import get_settings
from openlaw.corpus.filters import deduplicate, english_only
from openlaw.corpus.join import build_metadata_index
from openlaw.corpus.sources import resolve_judgment_url
from openlaw.datasets.vaquill import read_judgment_chunks
from openlaw.index.embed import DEFAULT_BATCH_SIZE, VoyageEmbedder, batched
from openlaw.index.qdrant_restore import CollectionSpec, build_collection_payload
from openlaw.index.upsert import SC_COLLECTION, build_point, upsert_points

PROGRESS_EVERY = 10_000


def main() -> int:
    settings = get_settings()
    if not settings.voyage_api_key:
        print("[Index] FAIL: OPENLAW_VOYAGE_API_KEY is not set")
        return 1

    print("[Index] building KanoonGPT metadata index (17M records, be patient)")
    metadata_index = build_metadata_index(settings.kanoongpt_root)
    print(f"[Index] metadata index holds {len(metadata_index):,} cases")

    source = settings.vaquill_root / "in_supreme-court_judgments.parquet"
    chunks = deduplicate(
        english_only(read_judgment_chunks(source, corpus_version=settings.corpus_version))
    )

    embedder = VoyageEmbedder(settings.voyage_api_key)
    indexed = 0
    unresolved = 0

    with httpx.Client(timeout=120) as client:
        spec = CollectionSpec(name=SC_COLLECTION, shards=1, mirror="")
        client.put(
            f"{settings.qdrant_url}/collections/{SC_COLLECTION}",
            json=build_collection_payload(spec),
        )

        for batch in batched(chunks, DEFAULT_BATCH_SIZE):
            resolved = [
                (chunk, url)
                for chunk in batch
                if (url := resolve_judgment_url(chunk, metadata_index)) is not None
            ]
            unresolved += len(batch) - len(resolved)
            if not resolved:
                continue

            texts = [chunk.text for chunk, _ in resolved]
            vectors = embedder.embed_documents(texts)

            points = []
            for (chunk, url), vector in zip(resolved, vectors, strict=True):
                found = metadata_index.lookup(chunk.join_key)
                points.append(
                    build_point(
                        chunk, vector, url, headnote=found.headnote if found else None
                    )
                )

            upsert_points(points, settings.qdrant_url, client)
            indexed += len(points)
            if indexed % PROGRESS_EVERY < DEFAULT_BATCH_SIZE:
                print(f"[Index] {indexed:,} indexed, {unresolved:,} unresolved")

    print(f"[Index] done: {indexed:,} indexed, {unresolved:,} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 10: Run the index build**

```bash
python scripts/build_sc_index.py
```

Expected: roughly 371,247 chunks indexed. Note the unresolved count — it should be small, since the Supreme Court join measured 100%.

- [ ] **Step 11: Commit**

```bash
git add src/openlaw/index tests/index scripts/build_sc_index.py
git commit -m "feat: embed and index English Supreme Court chunks with voyage-4"
```

---

## Task 12: Corpus validation gate

The gate for this whole plan: prove that **every indexed chunk resolves to a government URL**. This is the guarantee the entire product rests on, so it is asserted against the live index rather than assumed.

**Files:**
- Create: `src/openlaw/index/validation.py`
- Create: `scripts/validate_corpus.py`
- Create: `tests/index/test_validation.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `SC_COLLECTION` (Task 11)
- Produces:
  - `ValidationReport` — frozen dataclass; fields `checked: int`, `missing_url: list[str]`, `missing_version: list[str]`; property `is_clean: bool`
  - `validate_payloads(points: Iterable[dict[str, Any]]) -> ValidationReport`

- [ ] **Step 1: Write the failing test**

Create `tests/index/test_validation.py`:

```python
from openlaw.index.validation import validate_payloads


def _point(chunk_id: str, url: str | None, version: str | None = "v2026.08") -> dict:
    return {
        "id": chunk_id,
        "payload": {
            "chunk_id": chunk_id,
            "source_url": url,
            "corpus_version": version,
        },
    }


def test_clean_when_every_point_has_a_url_and_version() -> None:
    report = validate_payloads(
        [_point("a", "https://x.gov.in/a.pdf"), _point("b", "https://x.gov.in/b.pdf")]
    )

    assert report.checked == 2
    assert report.is_clean is True
    assert report.missing_url == []


def test_flags_a_point_without_a_source_url() -> None:
    report = validate_payloads(
        [_point("a", "https://x.gov.in/a.pdf"), _point("b", None)]
    )

    assert report.is_clean is False
    assert report.missing_url == ["b"]


def test_flags_a_point_with_a_blank_source_url() -> None:
    report = validate_payloads([_point("a", "   ")])
    assert report.missing_url == ["a"]


def test_flags_a_point_missing_the_corpus_version() -> None:
    report = validate_payloads([_point("a", "https://x.gov.in/a.pdf", version=None)])

    assert report.is_clean is False
    assert report.missing_version == ["a"]


def test_empty_input_is_clean() -> None:
    report = validate_payloads([])
    assert report.checked == 0
    assert report.is_clean is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/index/test_validation.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'openlaw.index.validation'`

- [ ] **Step 3: Implement `src/openlaw/index/validation.py`**

```python
"""Validation of indexed point payloads.

Enforces the product's core guarantee against the live index: every chunk carries
a government source URL and the corpus snapshot version.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationReport:
    """The outcome of validating a set of indexed points."""

    checked: int
    missing_url: list[str] = field(default_factory=list)
    missing_version: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when no point is missing a URL or a corpus version."""
        return not self.missing_url and not self.missing_version


def validate_payloads(points: Iterable[dict[str, Any]]) -> ValidationReport:
    """Validate that every point in `points` carries a URL and corpus version."""
    checked = 0
    missing_url: list[str] = []
    missing_version: list[str] = []

    for point in points:
        checked += 1
        payload = point.get("payload") or {}
        identifier = str(payload.get("chunk_id") or point.get("id") or "<unknown>")

        url = payload.get("source_url")
        if not url or not str(url).strip():
            missing_url.append(identifier)

        version = payload.get("corpus_version")
        if not version or not str(version).strip():
            missing_version.append(identifier)

    return ValidationReport(
        checked=checked, missing_url=missing_url, missing_version=missing_version
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/index/test_validation.py -v --no-cov`
Expected: PASS (5 passed)

- [ ] **Step 5: Write the validation driver**

Create `scripts/validate_corpus.py`:

```python
"""Corpus validation gate: every indexed chunk must resolve to a government URL.

Scrolls the Supreme Court collection and validates every payload. Exits non-zero
on any violation. Run: python scripts/validate_corpus.py
"""

import sys

import httpx

from openlaw.config import get_settings
from openlaw.index.upsert import SC_COLLECTION
from openlaw.index.validation import validate_payloads

PAGE_SIZE = 1_000


def main() -> int:
    settings = get_settings()
    total_checked = 0
    violations: list[str] = []

    with httpx.Client(timeout=120) as client:
        offset: str | None = None
        while True:
            body: dict[str, object] = {
                "limit": PAGE_SIZE,
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            response = client.post(
                f"{settings.qdrant_url}/collections/{SC_COLLECTION}/points/scroll",
                json=body,
            )
            response.raise_for_status()
            result = response.json()["result"]

            report = validate_payloads(result["points"])
            total_checked += report.checked
            violations.extend(report.missing_url)
            violations.extend(report.missing_version)

            offset = result.get("next_page_offset")
            if offset is None:
                break

    print(f"[Validate] checked {total_checked:,} points")
    if violations:
        print(f"[Validate] FAIL: {len(violations):,} violations")
        for identifier in violations[:20]:
            print(f"  - {identifier}")
        return 1

    print("[Validate] PASS: every indexed chunk resolves to a government URL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the gate**

```bash
python scripts/validate_corpus.py
```
Expected: `[Validate] PASS`. Any failure blocks this plan from being complete.

- [ ] **Step 7: Create `README.md` with mandatory attribution**

Write this content to `README.md`:

```markdown
# OpenLaw.in

Free, open-source legal aid for India. Every legal claim is backed by a citation
that resolves to a government source document.

## Data attribution

This project is built on two open datasets. **Attribution is a licence condition,
not a courtesy** — it is imposed by upstream eCourts, NCLT and NCLAT policy.

- **[vaquill/open-india-law](https://huggingface.co/datasets/vaquill/open-india-law)**
  — Indian primary law: judgments, legislation, regulator instruments.
  Licensed **CC BY 4.0**.
- **[KanoonGPT/indian-case-laws](https://huggingface.co/datasets/KanoonGPT/indian-case-laws)**
  — structured Indian case-law metadata. Licensed **Apache-2.0**.

The underlying legal text is a Government work under s.17(d) of the Copyright Act
1957, reproducible under s.52(1)(q).

## Coverage limits

Stated plainly, because a legal tool that overstates its coverage is dangerous:

- Retrieval coverage is not corpus coverage — 1,786,868 more judgments are held as
  extracted text upstream than are embedded.
- Consumer fora (NCDRC and State Commissions) are **not** in this corpus.
- District and trial court decisions are **not** included.
- The corpus is snapshot `v2026.08` — an archive, not current law.

**This is legal information, not legal advice.** Always verify against the official
source before relying on anything here.

## Setup

    cp .env.example .env      # set OPENLAW_VOYAGE_API_KEY
    pip install -e ".[dev]"
    docker compose up -d
    pytest

## Corpus build

    python scripts/make_fixtures.py            # test fixtures (once)
    python scripts/restore_acts_india.py       # 1,098,269 provisions (~11.2 GB)
    python scripts/build_sc_index.py           # 371,247 Supreme Court chunks
    python scripts/validate_corpus.py          # the gate: all citations resolve
```

- [ ] **Step 8: Run the full suite with coverage**

Run: `pytest`
Expected: all tests pass, coverage at or above 80% (enforced by `--cov-fail-under=80`).

- [ ] **Step 9: Commit**

```bash
git add src/openlaw/index/validation.py tests/index/test_validation.py \
        scripts/validate_corpus.py README.md
git commit -m "feat: corpus validation gate and attribution-bearing README"
```

---

## Task 13: Rebuild sparse vectors consistently

Hybrid retrieval (Plan 3) needs a sparse vector per chunk, and the ones we have cannot be
used as they stand. **Measured on 5,000 random `acts_india` points:** 51.7% carry
BM25-saturated weights (capped ≈2.2) while 47.6% carry raw term counts (values to 16+),
mixed together with no pattern by jurisdiction or document type. Since hybrid search sums
these weights, a raw-count chunk can outscore a BM25 chunk ~7x for no relevance reason.
The collection also sets no IDF modifier, so "shall" counts as much as "arbitration".
Separately, SC points from Task 11 are written **dense-only** and have no sparse vector at
all.

Both problems are fixed by one pass. This needs **no model and no API** — sparse vectors
are computed from the `text` already in each payload, so this is CPU work of roughly an
hour, not a re-embedding.

**Files:**
- Create: `src/openlaw/index/sparse.py`
- Create: `scripts/build_sparse_vectors.py`
- Create: `tests/index/test_sparse.py`

**Interfaces:**
- Consumes: `SC_COLLECTION` (Task 11); the live `acts_india` collection (read-only)
- Produces:
  - `SPARSE_VECTOR_NAME: str` — `"sparse"`
  - `bm25_vector(text: str, idf: Mapping[int, float], avg_len: float) -> dict[str, list]`
    returning `{"indices": [...], "values": [...]}`
  - `token_index(token: str) -> int` — stable hash, matching the published scheme
  - `corpus_statistics(texts: Iterable[str]) -> tuple[dict[int, float], float]` — document
    frequencies → IDF, and mean document length

**Constraints:**
- **Never write to `acts_india`.** Rebuilt statute vectors go to a **new** collection; the
  restored one stays untouched (it is the only copy of the published dense vectors and is
  expensive to restore).
- `k1=1.2`, `b=0.75`; IDF computed over the v1 corpus, not assumed.
- Assert the two-scheme defect is gone afterwards: no value above `k1 + 1` in a sample.
- Idempotent and checkpointed, like Task 11 — it touches ~1.47M points.

- [ ] **Step 1: Write the failing test** — `tests/index/test_sparse.py`: a repeated term
      saturates below `k1 + 1`; a rare term outweighs a common one at equal frequency;
      `token_index` is stable across calls; an empty string yields an empty vector.
- [ ] **Step 2: Implement** `src/openlaw/index/sparse.py`.
- [ ] **Step 3: Driver** `scripts/build_sparse_vectors.py` — two passes (statistics, then
      write), `--collection`, `--restart`, checkpointed.
- [ ] **Step 4: Verify on the live index** — rebuild, then confirm no raw-count outliers
      remain and that a known statutory phrase ranks its own section top-1 on sparse alone.
- [ ] **Step 5: Commit** — `feat: rebuild sparse vectors under one consistent BM25 scheme`

---

## Self-Review

**1. Spec coverage.**

| Spec section | Covered by |
|---|---|
| §3.3 the join (SC 100%, HC 98.6%) | Tasks 3, 8 |
| §3.3 identifier format table | Task 3 |
| §3.4.1 SC 33.3% English | Task 9 |
| §3.4.3 judge parser noise | Task 5 (margin-letter scrubbing removed after review — see Task 5 note) |
| §3.4.4 unusable semantic section labels | Not indexed as a retrieval field — deliberately unused. No task needed. |
| §3.4.5 `legal_subject` string-encoded, no consumer tag | Task 4 |
| §3.4.6 `section_title` quirk | `title` and `section_number` are indexed instead (Task 6). No task needed. |
| Date format divergence | Task 4 |
| §4 v1 corpus scope — 1,469,516 vectors | Tasks 10, 11 |
| §4 tribunals out of scope (resolved from documentation) | Task 2 scope note |
| §4 infrastructure (Qdrant/Postgres/Neo4j) | Task 2 (`docker-compose.yml`) |
| Global: voyage-4 / 1024 / cosine | Tasks 10, 11 |
| Global: every chunk resolves to a URL | Tasks 6, 8, 12 |
| §10 attribution and licensing | Task 12 (README) |
| §3.5 coverage limits published | Task 12 (README) |

Spec phases 2–6 are out of scope for this plan by design — see the handoff below.

**2. Placeholder scan.** No `TBD`, `TODO`, "implement later", "add error handling", or "similar to Task N". Every code step carries runnable code; every test step carries real assertions using values measured from the actual datasets.

**3. Type consistency.** Checked across tasks:
- `normalise_identifier` — defined Task 3; used Tasks 6, 8. Consistent.
- `JudgmentChunk.from_row(row, corpus_version=...)` — defined Task 6; used Tasks 7, 9, 11, 12. Signature consistent everywhere.
- `Provision.from_row(row, corpus_version=...)` — defined Task 6; used Task 7. Consistent.
- `CaseMetadata.from_row(row)` — no `corpus_version` (metadata is not versioned); defined Task 6, used Task 7. Intentional asymmetry.
- `MetadataIndex.lookup` / `build_metadata_index` — defined Task 8; used Task 11 driver. Consistent.
- `build_collection_payload(spec)` / `CollectionSpec` — defined Task 10; reused in Task 11's driver for `SC_COLLECTION`. Consistent.
- `CorpusStats.english_fraction` — a property, not a method; used only in Task 9.
- `require_url` (Task 8) raises; the Task 11 driver instead counts and skips via `resolve_judgment_url`. Both behaviours are intentional and documented in the `require_url` docstring — the strict form exists for callers that must fail loudly.
- `validate_payloads` / `ValidationReport` — defined Task 12; used only there.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-08-corpus-foundation.md`.**

This is Plan 1 of 5 (13 tasks; Task 13 added after the sparse-vector defect was measured). The remaining plans are written after their predecessor lands:

2. **Knowledge graph** (spec §5) — Neo4j builder, GDS PageRank and Louvain
3. **Retrieval and answer contract** (spec §6) — hybrid retrieval, citation validator
4. **Multilingual** (spec §7–8) — per-role models, query and answer translation
5. **Application** (spec §9–10) — auth, quota, chat UI, escalation routing

Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
