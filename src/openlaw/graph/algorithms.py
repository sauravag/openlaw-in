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
weight for PageRank. But it means `most_authoritative` can, and does, surface
high-PageRank stub nodes that carry no judgment text. Callers must not assume
every row it returns is a real, retrievable judgment.
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
    """Drop the named GDS projection if it exists.

    `false` as the second argument means "do not fail if missing" — this is
    called both before a run (clearing any leftover projection from a prior
    crash) and after (returning the catalog to a clean state), and both calls
    must succeed whether or not the projection currently exists.
    """
    with driver.session() as session:
        session.run(f"CALL gds.graph.drop('{name}', false)")


def most_authoritative(driver: neo4j.Driver, limit: int = 10) -> list[dict]:
    """Return the highest-PageRank judgments — a sanity check on the precompute.

    Note: rows may be stub `Judgment` nodes with no judgment text (see the
    module docstring) — this helper does not filter them out.
    """
    with driver.session() as session:
        result = session.run(
            "MATCH (j:Judgment) WHERE j.pagerank IS NOT NULL "
            "RETURN j.case_id AS case_id, j.pagerank AS pagerank "
            "ORDER BY j.pagerank DESC LIMIT $limit",
            limit=limit,
        )
        return [dict(record) for record in result]
