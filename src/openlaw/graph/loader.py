"""Batched writes into Neo4j using UNWIND, and the full drop that precedes them.

Every statement uses MERGE rather than CREATE so a rebuild is idempotent.

## Why edge targets need resolving

Three edge kinds name their target in free text rather than by node key, because
the source material does not say which node it means (see `openlaw.graph.edges`):
a citation string for CITES, a bare section number for INTERPRETS, an act title
for REFERENCES. Each is resolved here, deterministically, against the graph and
against `openlaw.graph.resolve.ActIndex`; whatever does not resolve becomes a
**namespaced stub**.

## The fan-out defect this module was rewritten to fix

INTERPRETS targets were previously merged as `(:Section {section_number: ...})`
and REFERENCES targets as `(:Act {title: ...})`. Neither property carries a
uniqueness constraint, so those MERGEs did not create one placeholder — they
matched and linked *every* node sharing the value. Measured on the live graph:
9,738,800 INTERPRETS edges, 9,531,762 of them onto real Sections, against the
plan's ~120,000 and its ~1.9M total-edge budget. Concretely, a judgment about
penal-code section 302 became graph-adjacent to section 302 of an unrelated
state municipal act, and `expand_chunks` returned it as "related".

The mirror failure was as bad: on a clean build the judgments phase ran before
provisions, so INTERPRETS attached only to bare-number placeholders, real
Sections received none, and since `expand_chunks` filters placeholders it could
never return a Judgment at all.

The rule that fixes both: **a stub key lives in a namespace a real node can
never occupy.** Stubs are keyed on `stub_key` — a property no real node carries,
holding a kind-prefixed value such as `"section:302"` — and additionally carry
the `:Stub` label so request-path queries can exclude them by label. The
`stub_key` uniqueness constraint (see `openlaw.graph.schema`) makes fan-out
structurally impossible rather than merely unlikely.

## Resolution, per kind

- **INTERPRETS**: `Edge.hint` carries the act a section reference named
  ("Section 138 of the Negotiable Instruments Act"). `ActIndex` turns that into
  an `act_id`, and the merge looks the Section up on `(act_id, section_number)`
  together. A section can be split across chunks — 369 chunks share section "2"
  of the Customs Tariff Act — so the lookup takes the lowest `chunk_id`, which
  is the base chunk and is stable across rebuilds. No hint, an ambiguous hint,
  or no such section in that act all fall back to a `section:` stub.
- **REFERENCES**: the act title resolves through the same `ActIndex` to a
  unique `act_id`, else an `act:` stub keyed on the normalised title, so two
  spellings of one unknown act share one stub.
- **CITES**: `extract_case_citations` normalises citations to a reporter form
  like `1979SCR453`, but a random sample of 400 real Supreme Court chunks
  showed 629 extracted citations were 406 SCR, 166 SCC, 56 AIR and only 1 INSC
  — the neutral form `Judgment.join_key` uses. So the merge matches the target
  against `citation_key` (each judgment's own reporter citation, derived in
  `openlaw.graph.nodes`) first, then `join_key`, and only then creates a
  `judgment:` stub. A stub still carries citation weight for PageRank, which is
  the honest record of "this judgment cites something outside our corpus".

Neither `citation_key` nor `join_key` is uniqueness-constrained (the corpus
contains genuine collisions — two 2007 judgments share one citation string), so
each lookup is capped at one deterministic match with `ORDER BY ... LIMIT 1`.
Without the cap a single ambiguous key produces one CITES edge per matching
judgment, wrongly linking judgments that merely share a citation string.
"""

import neo4j

from openlaw.graph.edges import Edge, EdgeKind
from openlaw.graph.resolve import (
    ACT_STUB,
    EMPTY_ACT_INDEX,
    JUDGMENT_STUB,
    SECTION_STUB,
    ActIndex,
    normalise_act_title,
    stub_key,
)

DROP_BATCH_SIZE = 10_000

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

# (source_label, source_key, target_label, target_key) for the kinds whose
# target is already a uniqueness-constrained node key, so a plain MERGE on it
# can match at most one node. The three resolving kinds are below.
_ENDPOINTS: dict[EdgeKind, tuple[str, str, str, str]] = {
    EdgeKind.DEFINES: ("Section", "chunk_id", "DefinedTerm", "term"),
    EdgeKind.HAS_SUBJECT: ("Section", "chunk_id", "LegalSubject", "name"),
    EdgeKind.DECIDED_BY: ("Judgment", "case_id", "Judge", "name"),
    EdgeKind.IN_COURT: ("Judgment", "case_id", "Court", "code"),
    EdgeKind.PART_OF: ("Section", "chunk_id", "Act", "act_id"),
}

_CITES_MERGE_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (a:Judgment {case_id: row.source}) "
    "WITH a, row "
    "CALL (row) { "
    "  OPTIONAL MATCH (c:Judgment {citation_key: row.target}) "
    "  WHERE c.case_id IS NOT NULL AND c.case_id <> row.source "
    "  RETURN c AS reported ORDER BY c.case_id LIMIT 1 "
    "} "
    "CALL (row) { "
    "  OPTIONAL MATCH (n:Judgment {join_key: row.target}) "
    "  WHERE n.case_id IS NOT NULL AND n.case_id <> row.source "
    "  RETURN n AS neutral ORDER BY n.case_id LIMIT 1 "
    "} "
    "WITH a, row, coalesce(reported, neutral) AS cited "
    "FOREACH (_ IN CASE WHEN cited IS NULL THEN [1] ELSE [] END | "
    "  MERGE (stub:Judgment:Stub {stub_key: row.stub_key}) "
    "  SET stub.join_key = row.target "
    "  MERGE (a)-[:CITES]->(stub) "
    ") "
    "FOREACH (_ IN CASE WHEN cited IS NOT NULL THEN [1] ELSE [] END | "
    "  MERGE (a)-[:CITES]->(cited) "
    ")"
)

_INTERPRETS_MERGE_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (j:Judgment {case_id: row.source}) "
    "WITH j, row "
    "CALL (row) { "
    "  OPTIONAL MATCH "
    "    (s:Section {act_id: row.act_id, section_number: row.target}) "
    "  RETURN s AS section ORDER BY s.chunk_id LIMIT 1 "
    "} "
    "FOREACH (_ IN CASE WHEN section IS NULL THEN [1] ELSE [] END | "
    "  MERGE (stub:Section:Stub {stub_key: row.stub_key}) "
    "  SET stub.section_number = row.target "
    "  MERGE (j)-[:INTERPRETS]->(stub) "
    ") "
    "FOREACH (_ IN CASE WHEN section IS NOT NULL THEN [1] ELSE [] END | "
    "  MERGE (j)-[:INTERPRETS]->(section) "
    ")"
)

_REFERENCES_MERGE_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (s:Section {chunk_id: row.source}) "
    "WITH s, row "
    "OPTIONAL MATCH (a:Act {act_id: row.act_id}) "
    "FOREACH (_ IN CASE WHEN a IS NULL THEN [1] ELSE [] END | "
    "  MERGE (stub:Act:Stub {stub_key: row.stub_key}) "
    "  SET stub.title = row.target "
    "  MERGE (s)-[:REFERENCES]->(stub) "
    ") "
    "FOREACH (_ IN CASE WHEN a IS NOT NULL THEN [1] ELSE [] END | "
    "  MERGE (s)-[:REFERENCES]->(a) "
    ")"
)

_RESOLVING_MERGE_CYPHER: dict[EdgeKind, str] = {
    EdgeKind.CITES: _CITES_MERGE_CYPHER,
    EdgeKind.INTERPRETS: _INTERPRETS_MERGE_CYPHER,
    EdgeKind.REFERENCES: _REFERENCES_MERGE_CYPHER,
}


def edge_merge_cypher(kind: EdgeKind) -> str:
    """Return the parameterised MERGE statement for edges of `kind`."""
    if not isinstance(kind, EdgeKind):
        raise TypeError(f"expected EdgeKind, got {type(kind).__name__}")

    resolving = _RESOLVING_MERGE_CYPHER.get(kind)
    if resolving is not None:
        return resolving

    source_label, source_key, target_label, target_key = _ENDPOINTS[kind]
    return (
        "UNWIND $rows AS row "
        f"MERGE (a:{source_label} {{{source_key}: row.source}}) "
        f"MERGE (b:{target_label} {{{target_key}: row.target}}) "
        f"MERGE (a)-[:{kind.value}]->(b)"
    )


def _interprets_row(edge: Edge, act_index: ActIndex) -> dict:
    """Return the INTERPRETS row for `edge`, resolving its act hint."""
    return {
        "source": edge.source,
        "target": edge.target,
        "act_id": act_index.act_id_for(edge.hint),
        "stub_key": stub_key(SECTION_STUB, edge.target),
    }


def _references_row(edge: Edge, act_index: ActIndex) -> dict:
    """Return the REFERENCES row for `edge`, resolving its act title.

    The stub key uses the *normalised* title so two spellings of one unknown
    act share a stub. A title that normalises to nothing (punctuation only)
    keeps its raw text, so such titles stay distinguishable instead of
    collapsing onto one shared node.
    """
    normalised = normalise_act_title(edge.target)
    return {
        "source": edge.source,
        "target": edge.target,
        "act_id": act_index.act_id_for(edge.target),
        "stub_key": stub_key(ACT_STUB, normalised or edge.target),
    }


def _cites_row(edge: Edge, act_index: ActIndex) -> dict:
    """Return the CITES row for `edge`. The graph, not the index, resolves it."""
    return {
        "source": edge.source,
        "target": edge.target,
        "stub_key": stub_key(JUDGMENT_STUB, edge.target),
    }


def _plain_row(edge: Edge, act_index: ActIndex) -> dict:
    """Return the row for an edge whose target is already a node key."""
    return {"source": edge.source, "target": edge.target}


_ROW_BUILDERS = {
    EdgeKind.CITES: _cites_row,
    EdgeKind.INTERPRETS: _interprets_row,
    EdgeKind.REFERENCES: _references_row,
}


def edge_rows(
    kind: EdgeKind, edges: list[Edge], act_index: ActIndex = EMPTY_ACT_INDEX
) -> list[dict]:
    """Return the deduplicated UNWIND rows for `edges` of `kind`.

    Pure: the only graph knowledge it uses is `act_index`, which is built once
    from the corpus act catalogue. Everything else resolves server-side.
    """
    build = _ROW_BUILDERS.get(kind, _plain_row)
    return [build(edge, act_index) for edge in dict.fromkeys(edges)]


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


def _run_batches(
    driver: neo4j.Driver, statement: str, rows: list[dict], batch_size: int
) -> int:
    """Run `statement` over `rows` in batches. Returns the number of rows sent."""
    written = 0
    with driver.session() as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            session.run(statement, rows=batch)
            written += len(batch)
    return written


def load_nodes(
    driver: neo4j.Driver, label: str, rows: list[dict], batch_size: int = 10_000
) -> int:
    """Merge `rows` as `label` nodes. Returns the number of distinct rows written."""
    return _run_batches(
        driver, NODE_MERGE_CYPHER[label], _deduplicate_rows(rows), batch_size
    )


def load_edges(
    driver: neo4j.Driver,
    kind: EdgeKind,
    edges: list[Edge],
    batch_size: int = 10_000,
    act_index: ActIndex = EMPTY_ACT_INDEX,
) -> int:
    """Merge `edges` of `kind`. Returns the number of distinct edges written.

    `act_index` resolves the act a free-text reference names; the default
    empty index resolves nothing, so every INTERPRETS and REFERENCES target
    becomes a namespaced stub. That is safe, never wrong, and exactly what a
    caller without the corpus act catalogue should get.
    """
    return _run_batches(
        driver, edge_merge_cypher(kind), edge_rows(kind, edges, act_index), batch_size
    )


def drop_graph(driver: neo4j.Driver, batch_size: int = DROP_BATCH_SIZE) -> int:
    """Delete every node and relationship. Returns the node count before the drop.

    The plan's global constraint is that the graph is derived data, dropped and
    rebuilt from scratch per corpus release, never migrated — nothing in this
    codebase honoured that before, so a repealed section loaded by one release
    survived into the next. Deleting a ~1.3M-node graph in one transaction
    exhausts the heap, so the delete runs in server-side sub-transactions of
    `batch_size` rows.

    `batch_size` is interpolated into the Cypher rather than parameterised
    (`IN TRANSACTIONS OF $n ROWS` is not accepted), so it is validated here:
    a non-int would otherwise fail as an opaque syntax error, and `bool` is
    rejected explicitly because `isinstance(True, int)` is True in Python.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")

    with driver.session() as session:
        before = session.run("MATCH (n) RETURN count(n) AS total").single()["total"]
        session.run(
            "MATCH (n) CALL (n) { DETACH DELETE n } "
            f"IN TRANSACTIONS OF {batch_size} ROWS"
        ).consume()
    return int(before)
