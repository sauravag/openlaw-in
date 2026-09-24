import neo4j
import pytest

from openlaw.graph.edges import Edge, EdgeKind
from openlaw.graph.loader import (
    NODE_MERGE_CYPHER,
    citation_key_for,
    edge_merge_cypher,
    load_edges,
    load_nodes,
)


def test_every_node_label_has_a_merge_statement() -> None:
    for label in (
        "Act", "Section", "Judgment", "Court", "Judge", "DefinedTerm", "LegalSubject"
    ):
        assert label in NODE_MERGE_CYPHER


def test_node_merges_use_unwind_for_batching() -> None:
    for statement in NODE_MERGE_CYPHER.values():
        assert "UNWIND $rows" in statement


def test_node_merges_use_merge_not_create() -> None:
    """Rebuilds must be idempotent — CREATE would duplicate on rerun."""
    for statement in NODE_MERGE_CYPHER.values():
        assert "MERGE" in statement
        assert "CREATE (" not in statement


def test_judgment_merge_sets_citation_key() -> None:
    """CITES edges resolve against `citation_key` first (see edge_merge_cypher
    tests below) — the Judgment node must carry it."""
    assert "citation_key" in NODE_MERGE_CYPHER["Judgment"]


def test_edge_merge_names_the_relationship_type() -> None:
    statement = edge_merge_cypher(EdgeKind.CITES)
    assert ":CITES" in statement
    assert "UNWIND $rows" in statement


def test_edge_merge_covers_every_kind() -> None:
    for kind in EdgeKind:
        assert f":{kind.value}" in edge_merge_cypher(kind)


def test_cites_edges_fall_back_to_the_judgment_join_key() -> None:
    """Citation targets are normalised keys like '1979SCR453'. A judgment
    whose own citation_key is unknown or does not match still gets a stub
    Judgment keyed on join_key, so PageRank sees citation weight for cases
    outside the indexed corpus."""
    assert "join_key: row.target" in edge_merge_cypher(EdgeKind.CITES)


def test_cites_edges_resolve_against_citation_key_first() -> None:
    """The required fix (Task 5 brief addendum): 0.2% of real citations are
    INSC-form join keys, but the corpus carries each judgment's own reporter
    citation, so CITES must match citation_key before falling back."""
    statement = edge_merge_cypher(EdgeKind.CITES)
    assert "citation_key: row.target" in statement


def test_edge_merge_rejects_a_non_edgekind() -> None:
    with pytest.raises(TypeError):
        edge_merge_cypher("CITES")


def test_citation_key_for_extracts_the_normalised_reporter_citation() -> None:
    """Case 1979_INSC_59's own citation '[1979] 3 S.C.R. 453' must normalise
    to '1979SCR453' — the same form CITES edges from other judgments target."""
    assert citation_key_for("[1979] 3 S.C.R. 453") == "1979SCR453"


def test_citation_key_for_none_when_citation_is_absent() -> None:
    assert citation_key_for(None) is None


def test_citation_key_for_none_when_citation_does_not_match_a_known_reporter() -> None:
    assert citation_key_for("an unparseable citation string") is None


@pytest.fixture
def live_driver() -> neo4j.Driver:
    """A real Neo4j driver, skipping the test cleanly if unreachable."""
    from openlaw.config import get_settings
    from openlaw.graph.connection import graph_driver

    with graph_driver(get_settings()) as driver:
        try:
            driver.verify_connectivity()
        except neo4j.exceptions.ServiceUnavailable:
            pytest.skip("Neo4j is not reachable at the configured URI")
        yield driver


def _cleanup(driver: neo4j.Driver, case_ids: list[str]) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (j:Judgment) WHERE j.case_id IN $ids OR j.join_key IN $ids "
            "OR j.citation_key IN $ids DETACH DELETE j",
            ids=case_ids,
        )


@pytest.mark.integration
def test_load_nodes_is_idempotent_against_live_neo4j(live_driver: neo4j.Driver) -> None:
    rows = [{"code": "TEST_COURT_LOADER"}]
    try:
        first = load_nodes(live_driver, "Court", rows)
        second = load_nodes(live_driver, "Court", rows)
        assert first == 1
        assert second == 1
        with live_driver.session() as session:
            count = session.run(
                "MATCH (c:Court {code: 'TEST_COURT_LOADER'}) RETURN count(c) AS n"
            ).single()["n"]
        assert count == 1
    finally:
        with live_driver.session() as session:
            session.run("MATCH (c:Court {code: 'TEST_COURT_LOADER'}) DETACH DELETE c")


@pytest.mark.integration
def test_load_nodes_deduplicates_rows_within_a_batch(live_driver: neo4j.Driver) -> None:
    """Task 3 review note: node builders don't dedupe, so the loader must,
    keeping UNWIND batches small at corpus scale."""
    rows = [{"name": "TEST_JUDGE_LOADER"}] * 5
    try:
        written = load_nodes(live_driver, "Judge", rows)
        assert written == 1
    finally:
        with live_driver.session() as session:
            session.run(
                "MATCH (g:Judge {name: 'TEST_JUDGE_LOADER'}) DETACH DELETE g"
            )


@pytest.mark.integration
def test_cites_edge_resolves_against_citation_key_over_join_key(
    live_driver: neo4j.Driver,
) -> None:
    """A judgment whose citation_key matches an incoming CITES target must
    link to that real Judgment, not spawn a stub."""
    case_ids = ["TEST_CITED_CASE", "TEST_CITING_CASE"]
    try:
        load_nodes(
            live_driver,
            "Judgment",
            [
                {
                    "case_id": "TEST_CITED_CASE",
                    "join_key": "TESTCITEDCASE",
                    "citation_key": "1979SCR999",
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                },
                {
                    "case_id": "TEST_CITING_CASE",
                    "join_key": "TESTCITINGCASE",
                    "citation_key": None,
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                },
            ],
        )
        load_edges(
            live_driver,
            EdgeKind.CITES,
            [Edge("TEST_CITING_CASE", "1979SCR999", EdgeKind.CITES)],
        )

        with live_driver.session() as session:
            result = session.run(
                "MATCH (:Judgment {case_id: 'TEST_CITING_CASE'})"
                "-[:CITES]->(target:Judgment) RETURN target.case_id AS case_id, "
                "target.join_key AS join_key"
            ).single()

        assert result["case_id"] == "TEST_CITED_CASE"
        # No stub was created keyed on the citation string itself.
        with live_driver.session() as session:
            stub_count = session.run(
                "MATCH (j:Judgment {join_key: '1979SCR999'}) RETURN count(j) AS n"
            ).single()["n"]
        assert stub_count == 0
    finally:
        _cleanup(live_driver, case_ids + ["1979SCR999"])


@pytest.mark.integration
def test_cites_edge_does_not_fan_out_when_citation_key_collides(
    live_driver: neo4j.Driver,
) -> None:
    """`citation_key` is indexed but not uniqueness-constrained. Real corpus
    data confirmed a collision (two 2007 Supreme Court judgments both keyed
    '2007SCR997'); a naive OPTIONAL MATCH creates one CITES edge per matching
    node instead of one, wrongly linking unrelated judgments. Exactly one
    edge must be created, deterministically, per citing row — and since the
    graph is dropped and rebuilt from scratch on every corpus release, a
    second load of the same rows must pick the identical target, not just
    "some" single target each time."""
    case_ids = ["TEST_CITING_CASE_3", "TEST_DUP_A", "TEST_DUP_B"]
    dup_key = "1950SCR1"
    try:
        load_nodes(
            live_driver,
            "Judgment",
            [
                {
                    "case_id": "TEST_CITING_CASE_3",
                    "join_key": "TESTCITINGCASE3",
                    "citation_key": None,
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                },
                {
                    "case_id": "TEST_DUP_A",
                    "join_key": "TESTDUPA",
                    "citation_key": dup_key,
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                },
                {
                    "case_id": "TEST_DUP_B",
                    "join_key": "TESTDUPB",
                    "citation_key": dup_key,
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                },
            ],
        )
        edges = [Edge("TEST_CITING_CASE_3", dup_key, EdgeKind.CITES)]
        query = (
            "MATCH (:Judgment {case_id: 'TEST_CITING_CASE_3'})-[:CITES]->(t) "
            "RETURN count(*) AS n, collect(DISTINCT t.case_id) AS targets"
        )

        load_edges(live_driver, EdgeKind.CITES, edges)
        with live_driver.session() as session:
            first = session.run(query).single()
        assert first["n"] == 1
        assert first["targets"] == ["TEST_DUP_A"]  # lower case_id wins, deterministically

        # Rebuilding from scratch (the real usage pattern) must not fan out or
        # drift to a different target the second time.
        load_edges(live_driver, EdgeKind.CITES, edges)
        with live_driver.session() as session:
            second = session.run(query).single()
        assert second["n"] == 1
        assert second["targets"] == first["targets"]
    finally:
        _cleanup(live_driver, case_ids)


@pytest.mark.integration
def test_cites_edge_creates_a_stub_when_no_citation_key_matches(
    live_driver: neo4j.Driver,
) -> None:
    """A citation outside the indexed corpus still gets a stub Judgment keyed
    on join_key, so it carries citation weight for PageRank."""
    case_ids = ["TEST_CITING_CASE_2"]
    stub_key = "1900SCR000"
    try:
        load_nodes(
            live_driver,
            "Judgment",
            [
                {
                    "case_id": "TEST_CITING_CASE_2",
                    "join_key": "TESTCITINGCASE2",
                    "citation_key": None,
                    "court_code": "SCI",
                    "decision_date": None,
                    "decision_year": None,
                    "corpus_version": "test",
                }
            ],
        )
        load_edges(
            live_driver,
            EdgeKind.CITES,
            [Edge("TEST_CITING_CASE_2", stub_key, EdgeKind.CITES)],
        )

        with live_driver.session() as session:
            result = session.run(
                "MATCH (:Judgment {case_id: 'TEST_CITING_CASE_2'})"
                "-[:CITES]->(target:Judgment) RETURN target.join_key AS join_key, "
                "target.case_id AS case_id"
            ).single()

        assert result["join_key"] == stub_key
        assert result["case_id"] is None
    finally:
        _cleanup(live_driver, case_ids + [stub_key])
