"""Request-path graph traversal.

These run inside a user request and must complete in under 100ms, so every
query is bounded in depth, in result count, and — since the whole-plan review
— in which relationship types it may walk. Deep lineage is available but capped
at MAX_DEPTH.

**Stub nodes.** A reference that resolved to no real node is still recorded, as
a placeholder in its own namespace: the `:Stub` label plus a `stub_key`
property no real node carries (see `openlaw.graph.loader`). A stub can never be
shown to a user — the product's contract is that every citation resolves to a
real government source, and a stub has none. Each query below either excludes
stubs (retrieval-facing queries) or documents why it intentionally leaves them
in (citation-network queries, where a stub still records a real fact: "this
judgment cites something outside our corpus").

Retrieval-facing queries exclude stubs two ways at once, and both are
deliberate. `NOT related:Stub` is the single check that holds for every label,
present and future. The per-label `chunk_id`/`act_id`/`case_id IS NOT NULL`
checks are the older, narrower guard, kept because they are what the projected
row actually returns as `id`: a row with a null id would be unusable to the
caller whatever its labels say.

**Supernodes.** `legal_subject` is 62% `general` (336,224 of 544,078
provisions), so at full corpus scale `LegalSubject {name: 'general'}` is a
~336,000-degree node, and popular `DefinedTerm`s are nearly as bad. An untyped
two-hop walk from a Section reaches every one of those siblings. `expand_chunks`
therefore names the relationship types it may traverse rather than walking
everything — see its docstring.
"""

import neo4j

MAX_DEPTH = 6


def judgments_interpreting_cypher() -> str:
    """Return the Cypher finding judgments that interpret a section.

    `INTERPRETS` always originates from a real `Judgment` (its source key is
    `case_id`, populated only from an actual `JudgmentChunk`), so the
    `j.case_id IS NOT NULL` guard below can never exclude a genuine row on
    today's data. It stays as an explicit, defensive contract: if a future
    change ever let `INTERPRETS` originate from an unresolved endpoint, this
    query must not start surfacing stub judgments silently.

    `$act_id` is what makes the answer trustworthy. A bare section number is
    not an identifier — every act has a section 2 — so matching on
    `section_number` alone returns judgments interpreting section 302 of *any*
    act, including the placeholder created when a judgment named a section
    without naming its statute. `NOT s:Stub` removes that placeholder case
    outright — a judgment reaching section 302 only through a placeholder has
    not been shown to interpret any particular act's section 302. Passing
    `act_id` narrows the match further, to the one statute the caller means;
    passing None still matches the number across every act, and such a caller
    must treat the result as a candidate list rather than an answer.
    """
    return (
        "MATCH (j:Judgment)-[:INTERPRETS]->"
        "(s:Section {section_number: $section_number}) "
        "WHERE j.case_id IS NOT NULL AND NOT s:Stub "
        "  AND ($act_id IS NULL OR s.act_id = $act_id) "
        "RETURN DISTINCT j.case_id AS case_id, j.court_code AS court_code, "
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

    A referenced act title that `openlaw.graph.resolve.ActIndex` cannot pin to
    exactly one `act_id` becomes a namespaced stub Act instead (measured on
    the full act catalogue, 38.1% of `acts_referenced` values resolve). Stubs
    are filtered out here because a caller cannot link to a government source
    for a title-only placeholder.
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
    source. Depth is fixed at two.

    **Which relationships it walks, and why not all of them.** The traversal
    names `PART_OF|REFERENCES|INTERPRETS` rather than walking every edge type.
    Two reasons, and both matter:

    - *Cost.* `HAS_SUBJECT` leads to `LegalSubject`, which at full corpus
      scale includes a ~336,000-degree `general` node (62% of provisions carry
      that tag); `DEFINES` leads to `DefinedTerm`s that are nearly as popular.
      An untyped `*1..2` walk from any Section goes through one of those and
      fans out over every sibling in the corpus. The earlier "median 1.3ms"
      measurement was taken at ~0.4% of corpus with 12 LegalSubject nodes, so
      it could not see this; a re-measurement on a partial build already
      showed a 51ms maximum against a 100ms budget.
    - *Value.* Nothing is lost. `LegalSubject` and `DefinedTerm` nodes are
      filtered out of the results anyway — they are not retrievable sources —
      so those hops could only ever contribute by reaching sibling Sections,
      and "shares the tag `general`" is not a relationship worth surfacing.

    What remains is exactly the retrievable neighbourhood: the Act a Section
    belongs to, the Acts it cites, that Act's other Sections, and the
    Judgments interpreting it — the statute-to-case-law bridge.

    **Stub exclusion.** `NOT related:Stub` excludes every placeholder by
    label. The per-label key checks that follow are redundant with it by
    construction and kept deliberately: they guarantee the `id` this query
    projects is never null, whatever labels a future change introduces.

    Note `Judgment` nodes never carry `source_url` in this graph (only
    `Section` and `Act` do — see `openlaw.graph.nodes`); the retrieval layer
    resolves a real judgment's text and source by `case_id` separately.
    """
    return (
        "MATCH (s:Section) WHERE s.chunk_id IN $chunk_ids "
        "MATCH (s)-[:PART_OF|REFERENCES|INTERPRETS*1..2]-(related) "
        "WHERE NOT related:Stub AND ( "
        "      (related:Section AND related.chunk_id IS NOT NULL) "
        "   OR (related:Act AND related.act_id IS NOT NULL) "
        "   OR (related:Judgment AND related.case_id IS NOT NULL)) "
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

    Raises ValueError when `depth` is not an `int` in 1..MAX_DEPTH. `depth` is
    f-string-interpolated straight into the Cypher `CITES*1..{depth}` range,
    so a non-integer must be rejected here rather than left for Neo4j to fail
    on with an opaque syntax error: `2.5` would silently produce the malformed
    `CITES*1..2.5`, and `bool` is rejected even though `isinstance(True, int)`
    is `True` in Python — `depth=True` must not silently become `CITES*1..1`.
    """
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise ValueError(f"depth must be an int, got {type(depth).__name__}")
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
    driver: neo4j.Driver,
    section_number: str,
    limit: int = 20,
    act_id: str | None = None,
) -> list[dict]:
    """Return judgments interpreting `section_number`, most authoritative first.

    Pass `act_id` whenever the caller knows which statute it means — without
    it the match is on the bare number across every act. See
    `judgments_interpreting_cypher`.
    """
    return _run(
        driver,
        judgments_interpreting_cypher(),
        section_number=section_number,
        limit=limit,
        act_id=act_id,
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
