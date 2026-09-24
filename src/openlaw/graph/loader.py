"""Batched writes into Neo4j using UNWIND.

Every statement uses MERGE rather than CREATE so a rebuild is idempotent. Edge
merges match endpoints on the key properties defined by the schema constraints;
a missing endpoint is created as a stub, which is correct for cited cases that
fall outside the indexed corpus.

CITES is a special case. `extract_case_citations` (see `openlaw.graph.extract`)
normalises citations to a reporter form like `1979SCR453`, but a random sample
of 400 real Supreme Court chunks showed 629 extracted citations were 406 SCR,
166 SCC, 56 AIR, and only 1 INSC — the neutral form that `Judgment.join_key`
uses. Resolving CITES targets against `join_key` alone would therefore link
barely 0.2% of citations. Instead, `citation_key_for` derives each judgment's
own `citation_key` from the corpus's `citation` column (its own reporter
citation, e.g. `"[1979] 3 S.C.R. 453"` -> `"1979SCR453"`), and the CITES merge
matches a target against `citation_key` first. Only when no judgment in the
graph carries that `citation_key` does it fall back to matching/creating a
stub `Judgment` keyed on `join_key` — correct for cases genuinely outside the
corpus, since the stub still carries citation weight for PageRank.
"""

import neo4j

from openlaw.graph.edges import Edge, EdgeKind
from openlaw.graph.extract import extract_case_citations

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
        "SET j.join_key = row.join_key, j.citation_key = row.citation_key, "
        "j.court_code = row.court_code, "
        "j.decision_date = row.decision_date, j.decision_year = row.decision_year, "
        "j.corpus_version = row.corpus_version"
    ),
    "Court": "UNWIND $rows AS row MERGE (c:Court {code: row.code})",
    "Judge": "UNWIND $rows AS row MERGE (g:Judge {name: row.name})",
    "DefinedTerm": "UNWIND $rows AS row MERGE (t:DefinedTerm {term: row.term})",
    "LegalSubject": "UNWIND $rows AS row MERGE (l:LegalSubject {name: row.name})",
}

# (source_label, source_key, target_label, target_key) per relationship type.
# CITES is handled separately by `_CITES_MERGE_CYPHER` below.
_ENDPOINTS: dict[EdgeKind, tuple[str, str, str, str]] = {
    EdgeKind.INTERPRETS: ("Judgment", "case_id", "Section", "section_number"),
    EdgeKind.REFERENCES: ("Section", "chunk_id", "Act", "title"),
    EdgeKind.DEFINES: ("Section", "chunk_id", "DefinedTerm", "term"),
    EdgeKind.HAS_SUBJECT: ("Section", "chunk_id", "LegalSubject", "name"),
    EdgeKind.DECIDED_BY: ("Judgment", "case_id", "Judge", "name"),
    EdgeKind.IN_COURT: ("Judgment", "case_id", "Court", "code"),
    EdgeKind.PART_OF: ("Section", "chunk_id", "Act", "act_id"),
}

# CITES resolves its target against `citation_key` first — the form judgment
# text actually cites — and only creates/matches a `join_key` stub when no
# judgment in the graph carries that citation_key. See the module docstring.
#
# `citation_key` is indexed but not uniqueness-constrained (two judgments can
# legitimately, or due to a corpus data quality issue, carry the same reporter
# citation), so a plain `OPTIONAL MATCH` can find more than one candidate. The
# `CALL (row) { ... LIMIT 1 }` subquery caps that fan-out at one match per row
# — without it, a single ambiguous citation_key produces one CITES edge per
# matching judgment instead of one, corrupting the graph with edges between
# judgments that merely share a citation string. `ORDER BY candidate.case_id`
# makes the choice deterministic across reruns rather than relying on
# whatever order the index happens to return.
_CITES_MERGE_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (a:Judgment {case_id: row.source}) "
    "WITH a, row "
    "CALL (row) { "
    "  OPTIONAL MATCH (candidate:Judgment {citation_key: row.target}) "
    "  RETURN candidate ORDER BY candidate.case_id LIMIT 1 "
    "} "
    "FOREACH (_ IN CASE WHEN candidate IS NULL THEN [1] ELSE [] END | "
    "  MERGE (stub:Judgment {join_key: row.target}) "
    "  MERGE (a)-[:CITES]->(stub) "
    ") "
    "FOREACH (_ IN CASE WHEN candidate IS NOT NULL THEN [1] ELSE [] END | "
    "  MERGE (a)-[:CITES]->(candidate) "
    ")"
)


def citation_key_for(citation: str | None) -> str | None:
    """Return the normalised `citation_key` for a judgment's own citation.

    Uses the same normalisation as CITES edge targets (`extract_case_citations`)
    so a judgment's own reporter citation lines up with how other judgments
    cite it. Returns None when `citation` is absent or matches no known
    reporter pattern — such judgments are only reachable via a `join_key` stub.
    """
    if not citation:
        return None
    keys = extract_case_citations(citation)
    return keys[0] if keys else None


def edge_merge_cypher(kind: EdgeKind) -> str:
    """Return the parameterised MERGE statement for edges of `kind`."""
    if not isinstance(kind, EdgeKind):
        raise TypeError(f"expected EdgeKind, got {type(kind).__name__}")

    if kind is EdgeKind.CITES:
        return _CITES_MERGE_CYPHER

    source_label, source_key, target_label, target_key = _ENDPOINTS[kind]
    return (
        "UNWIND $rows AS row "
        f"MERGE (a:{source_label} {{{source_key}: row.source}}) "
        f"MERGE (b:{target_label} {{{target_key}: row.target}}) "
        f"MERGE (a)-[:{kind.value}]->(b)"
    )


def _deduplicate_rows(rows: list[dict]) -> list[dict]:
    """Return `rows` with exact duplicates removed, first occurrence kept.

    Node builders in `openlaw.graph.nodes` do not dedupe — MERGE collapses
    duplicates correctly server-side, but at corpus scale (~1.3M nodes)
    deduping client-side first keeps UNWIND batches small.
    """
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def load_nodes(
    driver: neo4j.Driver, label: str, rows: list[dict], batch_size: int = 10_000
) -> int:
    """Merge `rows` as `label` nodes. Returns the number of distinct rows written."""
    statement = NODE_MERGE_CYPHER[label]
    deduped = _deduplicate_rows(rows)
    written = 0
    with driver.session() as session:
        for start in range(0, len(deduped), batch_size):
            batch = deduped[start : start + batch_size]
            session.run(statement, rows=batch)
            written += len(batch)
    return written


def load_edges(
    driver: neo4j.Driver, kind: EdgeKind, edges: list[Edge], batch_size: int = 10_000
) -> int:
    """Merge `edges` of `kind`. Returns the number of distinct edges written."""
    statement = edge_merge_cypher(kind)
    deduped = list(dict.fromkeys(edges))
    rows = [{"source": edge.source, "target": edge.target} for edge in deduped]
    written = 0
    with driver.session() as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            session.run(statement, rows=batch)
            written += len(batch)
    return written
