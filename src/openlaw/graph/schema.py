"""Neo4j constraints and indexes for the legal knowledge graph.

All DDL is idempotent because the graph is rebuilt from scratch on every corpus
release. Uniqueness constraints prevent duplicate nodes across reruns; indexes
back the filters used by request-path traversal.
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
)

INDEXES: tuple[str, ...] = (
    "CREATE INDEX judgment_court IF NOT EXISTS FOR (j:Judgment) ON (j.court_code)",
    "CREATE INDEX judgment_year IF NOT EXISTS FOR (j:Judgment) ON (j.decision_year)",
    "CREATE INDEX judgment_authority IF NOT EXISTS FOR (j:Judgment) ON (j.pagerank)",
    "CREATE INDEX judgment_join_key IF NOT EXISTS FOR (j:Judgment) ON (j.join_key)",
    "CREATE INDEX section_act IF NOT EXISTS FOR (s:Section) ON (s.act_id)",
    "CREATE INDEX section_number IF NOT EXISTS FOR (s:Section) ON (s.section_number)",
)


def apply_schema(driver: neo4j.Driver) -> int:
    """Apply every constraint and index. Returns the number of statements run."""
    statements = CONSTRAINTS + INDEXES
    with driver.session() as session:
        for statement in statements:
            session.run(statement)
    return len(statements)
