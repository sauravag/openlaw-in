import neo4j
import pytest

from openlaw.graph.algorithms import (
    CITATION_PROJECTION,
    LOUVAIN_PROPERTY,
    PAGERANK_PROPERTY,
    authority_label,
    drop_projection,
    louvain_cypher,
    most_authoritative,
    most_authoritative_cypher,
    pagerank_cypher,
    project_citation_graph,
    projection_cypher,
    run_louvain,
    run_pagerank,
)


def test_projection_names_the_citation_relationship() -> None:
    statement = projection_cypher(CITATION_PROJECTION)
    assert "CITES" in statement
    assert "Judgment" in statement
    assert CITATION_PROJECTION in statement


def test_pagerank_writes_to_the_indexed_property() -> None:
    """schema.INDEXES declares judgment_authority on Judgment.pagerank."""
    assert PAGERANK_PROPERTY == "pagerank"
    assert PAGERANK_PROPERTY in pagerank_cypher(CITATION_PROJECTION)


def test_pagerank_uses_write_mode_not_stream() -> None:
    """Authority must persist on the node for request-path ordering."""
    assert "gds.pageRank.write" in pagerank_cypher(CITATION_PROJECTION)


def test_louvain_writes_a_doctrine_cluster_property() -> None:
    assert LOUVAIN_PROPERTY == "doctrine_cluster"
    assert "gds.louvain.write" in louvain_cypher(CITATION_PROJECTION)
    assert LOUVAIN_PROPERTY in louvain_cypher(CITATION_PROJECTION)


def test_both_algorithms_run_over_the_same_projection() -> None:
    assert CITATION_PROJECTION in pagerank_cypher(CITATION_PROJECTION)
    assert CITATION_PROJECTION in louvain_cypher(CITATION_PROJECTION)


def _cleanup(driver: neo4j.Driver, case_ids: list[str]) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (j:Judgment) WHERE j.case_id IN $ids DETACH DELETE j",
            ids=case_ids,
        )


def _fetch_properties(driver: neo4j.Driver, case_ids: list[str]) -> list[dict]:
    with driver.session() as session:
        return session.run(
            "MATCH (j:Judgment) WHERE j.case_id IN $ids "
            "RETURN j.case_id AS case_id, j.pagerank AS pagerank, "
            "j.doctrine_cluster AS cluster",
            ids=case_ids,
        ).data()


@pytest.mark.integration
def test_gds_pipeline_ranks_the_most_cited_judgment_highest(
    live_driver: neo4j.Driver,
) -> None:
    """End-to-end: project a toy citation graph, run both algorithms, and
    confirm the judgment with the most inbound citations outranks one with
    none, and that Louvain assigns every node a cluster."""
    case_ids = ["TASK6_TEST_A", "TASK6_TEST_B", "TASK6_TEST_C", "TASK6_TEST_D"]
    with live_driver.session() as session:
        session.run(
            "UNWIND $ids AS cid MERGE (:Judgment {case_id: cid})", ids=case_ids
        )
        session.run(
            "MATCH (a:Judgment {case_id: 'TASK6_TEST_A'}), "
            "(b:Judgment {case_id: 'TASK6_TEST_B'}), "
            "(c:Judgment {case_id: 'TASK6_TEST_C'}), "
            "(d:Judgment {case_id: 'TASK6_TEST_D'}) "
            "MERGE (b)-[:CITES]->(a) "
            "MERGE (c)-[:CITES]->(a) "
            "MERGE (d)-[:CITES]->(a) "
            "MERGE (c)-[:CITES]->(b)"
        )

    try:
        drop_projection(live_driver, CITATION_PROJECTION)
        node_count = project_citation_graph(live_driver)
        assert node_count >= len(case_ids)

        properties_written = run_pagerank(live_driver)
        assert properties_written >= len(case_ids)

        clusters_written = run_louvain(live_driver)
        assert clusters_written >= len(case_ids)

        rows = _fetch_properties(live_driver, case_ids)
        properties = {row["case_id"]: row for row in rows}
        assert all(row["cluster"] is not None for row in properties.values())

        # Compare scores directly rather than via most_authoritative's ranked
        # position: the live graph may hold far more than these 4 judgments
        # (e.g. a corpus slice loaded by scripts/build_graph.py), so A and D
        # are not guaranteed to fall within any fixed `limit`.
        score_a = properties["TASK6_TEST_A"]["pagerank"]
        score_d = properties["TASK6_TEST_D"]["pagerank"]
        assert score_a > score_d

        # most_authoritative itself still needs a smoke test: non-empty, capped
        # at the limit, and sorted by descending pagerank. It must not assert
        # exactly `limit` rows — that depends on how much unrelated data
        # happens to be loaded, which is ambient state, not behaviour.
        ranked = most_authoritative(live_driver, limit=10)
        assert 0 < len(ranked) <= 10
        assert ranked == sorted(ranked, key=lambda row: row["pagerank"], reverse=True)
        assert all(authority_label(row) for row in ranked)
    finally:
        drop_projection(live_driver, CITATION_PROJECTION)
        _cleanup(live_driver, case_ids)


@pytest.mark.integration
def test_drop_projection_is_safe_when_nothing_is_projected(
    live_driver: neo4j.Driver,
) -> None:
    """Called unconditionally before every run, so a missing projection (the
    normal case) must not raise."""
    drop_projection(live_driver, "a_projection_that_was_never_created")


@pytest.mark.integration
def test_full_cycle_is_safe_to_run_twice_back_to_back(
    live_driver: neo4j.Driver,
) -> None:
    """The graph is derived data, "dropped and rebuilt per corpus release" —
    so project -> PageRank -> Louvain -> drop must be safe to run twice in a
    row. A stale projection left over from a previous run must not make the
    second pass fail, or silently double-write its properties."""
    case_ids = ["TASK6_RERUN_A", "TASK6_RERUN_B", "TASK6_RERUN_C"]
    with live_driver.session() as session:
        session.run(
            "UNWIND $ids AS cid MERGE (:Judgment {case_id: cid})", ids=case_ids
        )
        session.run(
            "MATCH (a:Judgment {case_id: 'TASK6_RERUN_A'}), "
            "(b:Judgment {case_id: 'TASK6_RERUN_B'}), "
            "(c:Judgment {case_id: 'TASK6_RERUN_C'}) "
            "MERGE (b)-[:CITES]->(a) "
            "MERGE (c)-[:CITES]->(a)"
        )

    def run_full_cycle() -> tuple[int, int, int]:
        drop_projection(live_driver, CITATION_PROJECTION)
        node_count = project_citation_graph(live_driver)
        properties_written = run_pagerank(live_driver)
        clusters_written = run_louvain(live_driver)
        drop_projection(live_driver, CITATION_PROJECTION)
        return node_count, properties_written, clusters_written

    try:
        first_pass = run_full_cycle()
        second_pass = run_full_cycle()

        assert first_pass[0] >= len(case_ids)
        assert second_pass == first_pass
    finally:
        drop_projection(live_driver, CITATION_PROJECTION)
        _cleanup(live_driver, case_ids)


# --- Task 2 whole-plan review: stub rows must be legible, not a crash ---------


def test_most_authoritative_cypher_reports_whether_a_row_is_a_stub() -> None:
    """`most_authoritative` deliberately returns stubs — they are the most
    cited nodes — and the release-verification script formatted `case_id` with
    `:<24`, which raises TypeError on the None every stub carries. The rows
    must say what they are so a caller can render them."""
    statement = most_authoritative_cypher()
    assert "AS is_stub" in statement
    assert "join_key" in statement


def test_most_authoritative_cypher_orders_by_authority_and_is_bounded() -> None:
    statement = most_authoritative_cypher()
    assert "ORDER BY j.pagerank DESC" in statement
    assert "LIMIT $limit" in statement


def test_authority_label_names_a_real_judgment_by_its_case_id() -> None:
    assert authority_label({"case_id": "2016_INSC_1151", "join_key": "X",
                            "is_stub": False}) == "2016_INSC_1151"


def test_authority_label_marks_a_stub_row_as_a_stub() -> None:
    """The formatting crash, fixed at the source: a stub has no case_id."""
    assert authority_label(
        {"case_id": None, "join_key": "1979SCR453", "is_stub": True}
    ) == "1979SCR453 (stub)"


def test_authority_label_survives_a_stub_with_no_join_key_either() -> None:
    assert authority_label({"case_id": None, "join_key": None, "is_stub": True}) == (
        "unidentified (stub)"
    )
