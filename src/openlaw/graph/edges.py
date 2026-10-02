"""Edge payload builders — pure functions from corpus records to graph edges.

Edges come from two sources: columns that already exist (`acts_referenced`,
`defined_terms`, `judges`, `legal_subject`) and regexes over judgment text
(`extract_case_citations`, `extract_section_references`). No LLM extraction.

Three edge kinds name a target that is not itself a node key, because the
source text does not say which node it means:

- `CITES` names a normalised citation string (e.g. `1979SCR453`), not a
  `case_id` such as `2016_INSC_1151`.
- `INTERPRETS` names a bare section number (e.g. `302`) plus, when the text
  gives one, an act hint (e.g. `"Negotiable Instruments Act"`) carried on
  `Edge.hint`.
- `REFERENCES` names an act's free-text title (e.g. `"Companies Act, 1956"`),
  not an `act_id`.

Resolving those names to real nodes needs the graph, so it happens in
`openlaw.graph.loader` (with `openlaw.graph.resolve.ActIndex` for act titles),
not here — these builders stay pure and database-free. What matters here is
that `Edge.hint` is *carried* rather than discarded: it is the only
deterministic signal distinguishing "section 302 of the Indian Penal Code"
from "section 302 of a state municipal act", and dropping it (as this module
previously did) left every judgment-to-section link pointing at a bare number.

`Edge.hint` participates in equality, so "Section 138" and "Section 138 of the
Negotiable Instruments Act" in one chunk stay two distinct edges: only the
second can resolve to a real Section, and collapsing them would silently throw
the resolvable one away.
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
    """A directed relationship from a source node key to a named target.

    `target` is a node key for edge kinds whose endpoints are both known
    (`PART_OF`, `IN_COURT`, ...), and a free-text name the loader must resolve
    for `CITES`, `INTERPRETS` and `REFERENCES` — see the module docstring.

    `hint` is the optional disambiguating context the source text supplied
    alongside `target`: for `INTERPRETS`, the act a section reference named.
    It defaults to None so every existing two-endpoint call site is unchanged.
    """

    source: str
    target: str
    kind: EdgeKind
    hint: str | None = None


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
        Edge(
            chunk.case_id,
            reference.number,
            EdgeKind.INTERPRETS,
            hint=reference.act_hint,
        )
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
