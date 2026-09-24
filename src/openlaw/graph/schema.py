"""Neo4j constraints and indexes for the legal knowledge graph.

All DDL is idempotent because the graph is rebuilt from scratch on every corpus
release (`openlaw.graph.loader.drop_graph`). Uniqueness constraints prevent
duplicate nodes across reruns; indexes back the filters used by request-path
traversal and by edge resolution at load time.

The `stub_key` constraint is load-bearing, not hygiene. Placeholder nodes for
references that resolve to nothing live in their own namespace — the `:Stub`
label plus a `stub_key` property no real node carries — precisely so a MERGE
for one placeholder cannot match a real node. The uniqueness constraint is what
makes that guarantee structural: see `openlaw.graph.loader` for the fan-out it
prevents.
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
    "CREATE CONSTRAINT stub_key IF NOT EXISTS "
    "FOR (n:Stub) REQUIRE n.stub_key IS UNIQUE",
)

INDEXES: tuple[str, ...] = (
    "CREATE INDEX judgment_court IF NOT EXISTS FOR (j:Judgment) ON (j.court_code)",
    "CREATE INDEX judgment_year IF NOT EXISTS FOR (j:Judgment) ON (j.decision_year)",
    "CREATE INDEX judgment_authority IF NOT EXISTS FOR (j:Judgment) ON (j.pagerank)",
    "CREATE INDEX judgment_join_key IF NOT EXISTS FOR (j:Judgment) ON (j.join_key)",
    "CREATE INDEX judgment_citation_key IF NOT EXISTS "
    "FOR (j:Judgment) ON (j.citation_key)",
    "CREATE INDEX section_act IF NOT EXISTS FOR (s:Section) ON (s.act_id)",
    "CREATE INDEX section_number IF NOT EXISTS FOR (s:Section) ON (s.section_number)",
    "CREATE INDEX section_act_number IF NOT EXISTS "
    "FOR (s:Section) ON (s.act_id, s.section_number)",
)


def apply_schema(driver: neo4j.Driver) -> int:
    """Apply every constraint and index. Returns the number of statements run."""
    statements = CONSTRAINTS + INDEXES
    with driver.session() as session:
        for statement in statements:
            session.run(statement)
    return len(statements)
