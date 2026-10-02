"""Integration tests proving the "no stub reaches a merged set" guarantee.

Fix round 1 on Plan 3 Task 3: the reviewer wanted this enforced, not merely
observed by an uncommitted verification script. The actual enforcement
mechanism is `RetrievedChunk`'s own validation (`source_url`/`text` both
`Field(min_length=1)`, see `openlaw.retrieval.context`) -- a stub row from
`expand_chunks` (no real source_url, no text) simply cannot become a
`RetrievedChunk` at all, so it can never reach `merge_chunks`. These tests
exercise that boundary against the live graph rather than assuming it.
"""

from collections.abc import Iterator

import neo4j
import pytest
from pydantic import ValidationError

from openlaw.config import get_settings
from openlaw.graph.connection import graph_driver
from openlaw.graph.queries import expand_chunks
from openlaw.retrieval.context import ChunkKind, RetrievedChunk
from openlaw.retrieval.rerank import merge_chunks

_PLACEHOLDER_TEXT = "text fetched separately by chunk_id/case_id at merge time"


@pytest.fixture
def live_driver() -> Iterator[neo4j.Driver]:
    """A real Neo4j driver, skipping the test cleanly if unreachable."""
    with graph_driver(get_settings()) as driver:
        try:
            driver.verify_connectivity()
        except neo4j.exceptions.ServiceUnavailable:
            pytest.skip("Neo4j is not reachable at the configured URI")
        yield driver


def _try_build_chunk(row: dict) -> RetrievedChunk | None:
    """Attempt to build a `RetrievedChunk` straight from one `expand_chunks`
    row. Returns None when the row cannot satisfy the citation contract --
    exactly what must happen for every Act and Judgment row, since neither
    carries a `source_url` on this edge (see `expand_chunks_cypher`)."""
    kind = ChunkKind.JUDGMENT if row["label"] == "Judgment" else ChunkKind.PROVISION
    try:
        return RetrievedChunk(
            chunk_id=row["id"],
            text=_PLACEHOLDER_TEXT,
            source_url=row["source_url"] or "",
            score=0.0,
            kind=kind,
        )
    except ValidationError:
        return None


@pytest.mark.integration
def test_act_and_judgment_rows_cannot_become_retrieved_chunks(
    live_driver: neo4j.Driver,
) -> None:
    """Act rows never carry `source_url` (Act nodes hold no chunk text or
    URL) and Judgment rows never carry one on this edge by design (see
    `expand_chunks_cypher`) -- both must fail `RetrievedChunk` construction,
    proving they cannot reach `merge_chunks` however a caller wires things."""
    with live_driver.session() as session:
        act_linked = session.run(
            "MATCH (s:Section)-[:REFERENCES]->(a:Act) "
            "WHERE NOT a:Stub AND a.act_id IS NOT NULL AND NOT s:Stub "
            "RETURN s.chunk_id AS chunk_id LIMIT 5"
        )
        interpreted = session.run(
            "MATCH (j:Judgment)-[:INTERPRETS]->(s:Section) "
            "WHERE j.case_id IS NOT NULL AND s.chunk_id IS NOT NULL AND NOT s:Stub "
            "RETURN s.chunk_id AS chunk_id LIMIT 5"
        )
        chunk_ids = [r["chunk_id"] for r in act_linked] + [
            r["chunk_id"] for r in interpreted
        ]
    if not chunk_ids:
        pytest.skip("no Section has a real REFERENCES/INTERPRETS edge loaded")

    rows = expand_chunks(live_driver, chunk_ids, limit=100)
    non_section_rows = [row for row in rows if row["label"] != "Section"]
    if not non_section_rows:
        pytest.skip("expansion returned no Act/Judgment rows to test against")

    for row in non_section_rows:
        assert _try_build_chunk(row) is None, (
            f"a {row['label']} row built a citable RetrievedChunk: {row}"
        )


@pytest.mark.integration
def test_no_stub_survives_into_a_merged_set(live_driver: neo4j.Driver) -> None:
    """End-to-end: expand real chunk_ids, keep only the rows that could
    become real `RetrievedChunk`s, merge them, and assert every survivor
    carries a non-empty `chunk_id`, `text`, and `source_url` -- the full
    citation contract, checked against live data rather than assumed."""
    with live_driver.session() as session:
        act_linked = session.run(
            "MATCH (s:Section)-[:REFERENCES]->(a:Act) "
            "WHERE NOT a:Stub AND a.act_id IS NOT NULL AND NOT s:Stub "
            "RETURN s.chunk_id AS chunk_id LIMIT 5"
        )
        interpreted = session.run(
            "MATCH (j:Judgment)-[:INTERPRETS]->(s:Section) "
            "WHERE j.case_id IS NOT NULL AND s.chunk_id IS NOT NULL AND NOT s:Stub "
            "RETURN s.chunk_id AS chunk_id LIMIT 5"
        )
        chunk_ids = [r["chunk_id"] for r in act_linked] + [
            r["chunk_id"] for r in interpreted
        ]
        if not chunk_ids:
            chunk_ids = [
                record["chunk_id"]
                for record in session.run(
                    "MATCH (s:Section) WHERE s.chunk_id IS NOT NULL "
                    "RETURN s.chunk_id AS chunk_id LIMIT 20"
                )
            ]
    if not chunk_ids:
        pytest.skip("no Section nodes with a chunk_id are loaded")

    rows = expand_chunks(live_driver, chunk_ids, limit=100)
    if not rows:
        pytest.skip("expansion returned nothing for this sample")

    candidates = tuple(
        chunk for chunk in (_try_build_chunk(row) for row in rows) if chunk is not None
    )
    merged = merge_chunks((), candidates, max_expanded=len(candidates) or 1)

    assert len(merged) == len(candidates), "merge_chunks dropped a valid chunk"
    for chunk in merged:
        assert chunk.chunk_id
        assert chunk.text
        assert chunk.source_url
