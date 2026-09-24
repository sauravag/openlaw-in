"""Request-path graph traversal.

These run inside a user request and must complete in under 100ms, so every query
is bounded in both depth and result count. Deep lineage is available but capped
at MAX_DEPTH.

Stub nodes (carried from Task 6's placeholder-node trade-off): a node created
as the far endpoint of an edge whose real corpus record was never loaded is a
placeholder that lacks the label's own constrained key property:

- `Judgment` stubs come from `CITES` targets that did not resolve to a real
  judgment (`openlaw.graph.loader._CITES_MERGE_CYPHER`) — they carry
  `join_key` but no `case_id`, no text, no source URL. On the current slice,
  1,997 of 2,457 `Judgment` nodes are such stubs.
- `Section` stubs come from `INTERPRETS` targets, merged on the bare section
  number the judgment text names (`openlaw.graph.loader._ENDPOINTS`) — they
  carry `section_number` but no `chunk_id`, no `source_url`. 609 of 5,609
  `Section` nodes on the current slice are such stubs.
- `Act` stubs come from `REFERENCES` targets, merged on the referenced act's
  free-text title rather than a canonical `act_id` — they carry `title` but
  no `act_id`. 260 of 425 `Act` nodes on the current slice are such stubs.

A stub can never be shown to a user: the product's contract is that every
citation resolves to a real government source, and a stub has none. Each
query below either filters stubs out (retrieval-facing queries) or documents
why it intentionally leaves them in (citation-network queries, where a stub
still records a real fact: "this judgment cites something outside our
corpus").
"""

import neo4j

MAX_DEPTH = 6


def judgments_interpreting_cypher() -> str:
    """Return the Cypher finding judgments that interpret a section.

    `INTERPRETS` always originates from a real `Judgment` (its source key is
    `case_id`, populated only from an actual `JudgmentChunk` — see
    `openlaw.graph.loader._ENDPOINTS`), so the `j.case_id IS NOT NULL` guard
    below can never exclude a genuine row on today's data. It stays as an
    explicit, defensive contract: if a future change ever let `INTERPRETS`
    originate from an unresolved endpoint, this query must not start
    surfacing stub judgments silently. The target `Section` may itself be a
    bare-number placeholder (see module docstring), but that does not matter
    here since only `j`'s properties are returned.
    """
    return (
        "MATCH (j:Judgment)-[:INTERPRETS]->"
        "(s:Section {section_number: $section_number}) "
        "WHERE j.case_id IS NOT NULL "
        "RETURN j.case_id AS case_id, j.court_code AS court_code, "
        "j.decision_year AS decision_year, j.pagerank AS pagerank "
        "ORDER BY j.pagerank DESC LIMIT $limit"
    )


def sections_for_act_cypher() -> str:
    """Return the Cypher listing the sections of an Act.

    `PART_OF` only ever connects a real `Section` (source key `chunk_id`) to
    a real `Act` (target key `act_id`) — see `openlaw.graph.loader._ENDPOINTS`
    — so no stub can appear on either side of this edge. No extra filter is
    needed.
    """
    return (
        "MATCH (s:Section)-[:PART_OF]->(a:Act {act_id: $act_id}) "
        "RETURN s.chunk_id AS chunk_id, s.section_number AS section_number, "
        "s.source_url AS source_url, s.in_force AS in_force "
        "ORDER BY s.section_number LIMIT $limit"
    )


def acts_referenced_by_cypher() -> str:
    """Return the Cypher listing Acts referenced by a provision.

    `REFERENCES` is merged on the referenced Act's free-text `title`, not its
    `act_id` (see `openlaw.graph.loader._ENDPOINTS`), so a reference that does
    not textually match a canonical Act's title creates an `act_id`-less stub
    Act. On the current slice that is 260 of 425 Act nodes. Filtered out here
    because a caller cannot link to a government source for a title-only
    stub.
    """
    return (
        "MATCH (s:Section {chunk_id: $chunk_id})-[:REFERENCES]->(a:Act) "
        "WHERE a.act_id IS NOT NULL "
        "RETURN a.act_id AS act_id, a.title AS title LIMIT $limit"
    )


def expand_chunks_cypher() -> str:
    """Return the Cypher expanding retrieved chunks by one or two hops.

    The retrieval-time entry point: rows from this query are fed straight to
    the retrieval layer, so every row must resolve to a real government
    source. Depth is fixed at two, which is what keeps it inside the 100ms
    budget.

    A plain `related:Section OR related:Act OR related:Judgment` label check
    is not enough — each of those labels has a stub form on this graph (see
    module docstring): a bare-number `Section` from `INTERPRETS`, a
    title-only `Act` from `REFERENCES`, or a `join_key`-only `Judgment` from
    `CITES`. Each is excluded by requiring the property that is that label's
    own uniqueness-constrained key (`chunk_id`, `act_id`, `case_id`
    respectively) to be present — a stub, by construction, never has it. On
    the current slice this excludes 609 Section stubs, 260 Act stubs, and
    1,997 Judgment stubs from ever reaching a caller.

    Note `Judgment` nodes never carry `source_url` in this graph (only
    `Section` and `Act` do — see `openlaw.graph.nodes`); the retrieval layer
    resolves a real judgment's text and source by `case_id` separately. A
    `Judgment` row here is retrievable because `case_id IS NOT NULL`
    guarantees it names a real judgment, not because `source_url` is set on
    the graph node itself.
    """
    return (
        "MATCH (s:Section) WHERE s.chunk_id IN $chunk_ids "
        "MATCH (s)-[*1..2]-(related) "
        "WHERE (related:Section AND related.chunk_id IS NOT NULL) "
        "   OR (related:Act AND related.act_id IS NOT NULL) "
        "   OR (related:Judgment AND related.case_id IS NOT NULL) "
        "RETURN DISTINCT "
        "  coalesce(related.chunk_id, related.act_id, related.case_id) AS id, "
        "  labels(related)[0] AS label, "
        "  related.source_url AS source_url "
        "LIMIT $limit"
    )


def precedent_lineage_cypher(depth: int) -> str:
    """Return the Cypher tracing a citation chain to `depth` hops.

    Unlike `expand_chunks`, this query is explicitly about the citation
    network, not about retrievable content: "case X cites something outside
    our corpus" is itself a real, useful fact, and hiding it would make a
    heavily-cited-but-unindexed case look uncited. So stub judgments are left
    in deliberately, but `is_stub` is returned alongside every row so a
    caller that needs retrievable content (unlike this query, which does not)
    can filter them out itself instead of being misled by a bare `case_id`.
    A stub still carries `join_key`, which is returned so a stub row is not
    entirely unidentifiable.

    Raises ValueError when `depth` falls outside 1..MAX_DEPTH.
    """
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}, got {depth}")
    return (
        f"MATCH path = (j:Judgment {{case_id: $case_id}})"
        f"-[:CITES*1..{depth}]->(cited:Judgment) "
        "RETURN DISTINCT cited.case_id AS case_id, cited.join_key AS join_key, "
        "cited.pagerank AS pagerank, cited.case_id IS NULL AS is_stub, "
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
    """Expand `chunk_ids` by one or two hops. The retrieval-time entry point.

    Returns `[]` for an empty `chunk_ids` without touching the database —
    an unconditional `IN []` MATCH is a wasted round trip on the request
    path.
    """
    if not chunk_ids:
        return []
    return _run(driver, expand_chunks_cypher(), chunk_ids=chunk_ids, limit=limit)


def precedent_lineage(
    driver: neo4j.Driver, case_id: str, depth: int = 3, limit: int = 25
) -> list[dict]:
    """Trace the citation chain from `case_id` to `depth` hops.

    May include stub judgments (`row["is_stub"]` is `True`) — see
    `precedent_lineage_cypher`.
    """
    return _run(driver, precedent_lineage_cypher(depth), case_id=case_id, limit=limit)
