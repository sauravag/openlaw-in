"""Edge payload builders — pure functions from corpus records to graph edges.

Edges come from two sources: columns that already exist (`acts_referenced`,
`defined_terms`, `judges`, `legal_subject`) and regexes over judgment text
(`extract_case_citations`, `extract_section_references`). No LLM extraction.

Two edge kinds intentionally target a value that is not itself a node's key
property in `openlaw.graph.nodes`:

- `CITES` targets the normalised citation string produced by
  `extract_case_citations` (e.g. `1979SCR453`), not a `case_id` such as
  `2016_INSC_1151`. Resolving a citation to the `Judgment` it names requires a
  citation-to-`case_id` lookup that does not exist in this corpus; until that
  join is built, Task 5 will `MERGE` a placeholder `Judgment` node keyed on the
  citation string for any citation that is not itself a `case_id`.
- `INTERPRETS` targets a bare section number (e.g. `302`), not a `Section`'s
  `chunk_id` (e.g. `IND_central_45_302`), because judgment text names a
  section without saying which Act's `chunk_id` it belongs to. Task 5 will
  `MERGE` a placeholder `Section`-like node keyed on the bare number.

Both are accepted trade-offs of deterministic, LLM-free extraction: the plan
counts ~200,000 deduped case citations and ~120,000 judgment-to-section links
on this basis. A future task may resolve these placeholders against real
`case_id`/`chunk_id` keys once a citation index exists.
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
    """Return every edge derivable from `chunk`, deduplicated and ordered.

    `IN_COURT` and `DECIDED_BY` are chunk-level facts about the judgment as a
    whole, so calling this once per chunk of the same `case_id` yields the
    same court/judge edges every time; dedup happens once all chunks' edges
    are combined (e.g. in `group_by_kind` or the Task 5 loader), not here.
    `CITES` and `INTERPRETS` are derived from this chunk's own text only, so
    they naturally vary chunk to chunk within one judgment.
    """
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
