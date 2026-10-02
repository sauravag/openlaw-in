"""Graph Data Science precomputes: citation authority and doctrinal clusters.

Both run offline, once per corpus release, over the projection defined here.
GDS Community Edition caps concurrency at 4 cores, which is irrelevant for a
batch pass over ~1.9M edges. The projection is dropped and recreated on every
run rather than patched, matching the rest of the graph pipeline: derived
data, rebuilt from scratch per corpus release.

PageRank over CITES gives authority — a judgment cited by many judgments that
are themselves heavily cited ranks highly, which is what "landmark" means in
practice.

Louvain over the same projection groups judgments that cite each other
densely, recovering doctrinal areas. This matters because `legal_subject` is
62% `general` (336,224 of 544,078 provisions) and carries no consumer-law tag
at all — Louvain is the only signal available for doctrinal clustering.

Caveat carried from Task 5 (citation-key collisions): CITES edges resolve
their target against `Judgment.citation_key`, and the corpus contains genuine
collisions — two distinct 2007 judgments, for example, share one citation
string. `openlaw.graph.loader`'s CITES merge resolves such a collision
deterministically (`ORDER BY candidate.case_id LIMIT 1`), which means the
"loser" judgment in a collision never receives the inbound citation edges
attributed to its colliding twin. PageRank here is computed over exactly that
citation graph, so a small number of judgments may be under-ranked purely
because their inbound citations were attributed to a colliding case. This is
accepted source-data ambiguity, not a defect this module corrects.

Caveat on stub nodes: a large share of CITES targets are placeholder
`Judgment` nodes for cases outside this corpus (on a 3,000-chunk sample: 237
citations resolved to a real judgment, 1,154 created a stub — see
`openlaw.graph.loader`). Stubs are intentional: they still carry citation
weight for PageRank, and the most-cited nodes in the graph are routinely
stubs, because the most-cited judgments in Indian law predate this corpus.

So `most_authoritative` returns stub rows by design, and every row it returns
says so: `is_stub` is set, and `join_key` carries the citation string that is
all a stub is identified by. `authority_label` renders either kind for
display. This is not decoration — the release-verification script formatted
`case_id` with `:<24`, which raises TypeError on the None a stub carries, so
the script died on its final line on entirely expected data.
"""

import neo4j

CITATION_PROJECTION = "citation_graph"
PAGERANK_PROPERTY = "pagerank"
LOUVAIN_PROPERTY = "doctrine_cluster"


def projection_cypher(name: str) -> str:
    """Return the Cypher projecting the citation graph under `name`.

    A native projection over every `Judgment` node and `CITES` edge — the
    only relationship type PageRank and Louvain run over here.
    """
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


def project_citation_graph(driver: neo4j.Driver, name: str = CITATION_PROJECTION) -> int:
    """Project the citation graph into the GDS catalog under `name`.

    Returns node count. `name` defaults to the production projection name;
    tests pass a unique name so concurrent runs against a shared Neo4j
    instance cannot collide on the same GDS catalog entry.
    """
    with driver.session() as session:
        record = session.run(projection_cypher(name)).single()
    return int(record["nodeCount"]) if record else 0


def run_pagerank(driver: neo4j.Driver, name: str = CITATION_PROJECTION) -> int:
    """Write PageRank authority scores over the `name` projection.

    Returns properties written. See `project_citation_graph` for why `name`
    is overridable.
    """
    with driver.session() as session:
        record = session.run(pagerank_cypher(name)).single()
    return int(record["nodePropertiesWritten"]) if record else 0


def run_louvain(driver: neo4j.Driver, name: str = CITATION_PROJECTION) -> int:
    """Write Louvain doctrinal cluster ids over the `name` projection.

    Returns properties written. See `project_citation_graph` for why `name`
    is overridable.
    """
    with driver.session() as session:
        record = session.run(louvain_cypher(name)).single()
    return int(record["nodePropertiesWritten"]) if record else 0


def drop_projection(driver: neo4j.Driver, name: str) -> None:
    """Drop the named GDS projection if it exists.

    `false` as the second argument means "do not fail if missing" — this is
    called both before a run (clearing any leftover projection from a prior
    crash) and after (returning the catalog to a clean state), and both calls
    must succeed whether or not the projection currently exists.
    """
    with driver.session() as session:
        session.run(f"CALL gds.graph.drop('{name}', false)")


def most_authoritative_cypher() -> str:
    """Return the Cypher listing the highest-PageRank judgments.

    Stub rows are included deliberately (see the module docstring), so every
    row carries `is_stub` and the `join_key` that identifies a stub, not just
    a `case_id` that is null for half of them.
    """
    return (
        "MATCH (j:Judgment) WHERE j.pagerank IS NOT NULL "
        "RETURN j.case_id AS case_id, j.join_key AS join_key, "
        "j:Stub AS is_stub, j.pagerank AS pagerank "
        "ORDER BY j.pagerank DESC LIMIT $limit"
    )


def most_authoritative(driver: neo4j.Driver, limit: int = 10) -> list[dict]:
    """Return the highest-PageRank judgments — a sanity check on the precompute.

    Rows may be stub `Judgment` nodes with no judgment text (see the module
    docstring); they are flagged, not filtered. Use `authority_label` to
    render a row.
    """
    with driver.session() as session:
        result = session.run(most_authoritative_cypher(), limit=limit)
        return [dict(record) for record in result]


def authority_label(row: dict) -> str:
    """Return a display label for a `most_authoritative` row.

    A real judgment shows its `case_id`. A stub has none, so it shows the
    citation string it was created from, marked as a stub — the caller is
    looking at the citation network, and "this uncited-by-us case is the most
    cited thing in the graph" is the finding, not an error.
    """
    if row.get("case_id"):
        return str(row["case_id"])
    return f"{row.get('join_key') or 'unidentified'} (stub)"
