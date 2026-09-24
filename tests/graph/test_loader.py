import os

import neo4j
import pytest

from openlaw.graph.edges import Edge, EdgeKind
from openlaw.graph.loader import (
    NODE_MERGE_CYPHER,
    drop_graph,
    edge_merge_cypher,
    edge_rows,
    load_edges,
    load_nodes,
)
from openlaw.graph.resolve import ActIndex


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


def test_cites_edges_fall_back_to_a_namespaced_stub_carrying_the_join_key() -> None:
    """Citation targets are normalised keys like '1979SCR453'. A citation that
    resolves to no judgment in the corpus still gets a stub so PageRank sees
    its citation weight — but the stub is keyed on `stub_key`, a property no
    real Judgment carries, and NOT on `join_key`, which real judgments do."""
    statement = edge_merge_cypher(EdgeKind.CITES)
    assert "MERGE (stub:Judgment:Stub {stub_key: row.stub_key})" in statement
    assert "stub.join_key = row.target" in statement
    assert "MERGE (stub:Judgment {join_key: row.target})" not in statement


def test_cites_edges_resolve_against_citation_key_first() -> None:
    """The required fix (Task 5 brief addendum): 0.2% of real citations are
    INSC-form join keys, but the corpus carries each judgment's own reporter
    citation, so CITES must match citation_key before falling back."""
    statement = edge_merge_cypher(EdgeKind.CITES)
    assert "citation_key: row.target" in statement


def test_edge_merge_rejects_a_non_edgekind() -> None:
    with pytest.raises(TypeError):
        edge_merge_cypher("CITES")


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


# --- Task 2 whole-plan review: stub keys must be namespaced -------------------

# Whole property maps, not substrings: `{act_id: ..., section_number: ...}` is
# a safe composite lookup, while `{section_number: ...}` alone is the defect.
_NON_UNIQUE_TARGET_KEYS = ("{section_number: row.target}", "{title: row.target}")


def test_no_edge_merge_keys_a_target_on_a_non_unique_property() -> None:
    """The CRITICAL defect. `Section.section_number` and `Act.title` carry no
    uniqueness constraint, so `MERGE (b:Section {section_number: row.target})`
    does not create one placeholder — it matches and links EVERY Section in
    the corpus numbered 302. Measured on the live graph before this fix:
    9,738,800 INTERPRETS edges against a plan budget of ~120,000."""
    for kind in EdgeKind:
        statement = edge_merge_cypher(kind)
        for forbidden in _NON_UNIQUE_TARGET_KEYS:
            assert forbidden not in statement, f"{kind.value} fans out on {forbidden}"


def test_every_stub_merge_uses_the_namespaced_key_and_the_stub_label() -> None:
    """A stub key must live in a namespace real nodes can never occupy:
    `stub_key` is a property no real node carries, and `:Stub` marks it so
    request-path queries can exclude it by label."""
    for kind in (EdgeKind.CITES, EdgeKind.INTERPRETS, EdgeKind.REFERENCES):
        statement = edge_merge_cypher(kind)
        assert ":Stub {stub_key: row.stub_key}" in statement


def test_interprets_resolves_a_section_through_the_act_the_text_named() -> None:
    """The section number alone is meaningless — every act has a section 2.
    The lookup must be keyed on (act_id, section_number) together."""
    assert (
        "Section {act_id: row.act_id, section_number: row.target}"
        in edge_merge_cypher(EdgeKind.INTERPRETS)
    )


def test_interprets_picks_one_section_deterministically() -> None:
    """A section can be split across chunks (369 chunks share section '2' of
    the Customs Tariff Act), so the lookup must be bounded and stable."""
    statement = edge_merge_cypher(EdgeKind.INTERPRETS)
    assert "ORDER BY s.chunk_id LIMIT 1" in statement


def test_references_resolves_against_the_unique_act_id() -> None:
    statement = edge_merge_cypher(EdgeKind.REFERENCES)
    assert "act_id: row.act_id" in statement


def test_cites_never_creates_a_self_loop() -> None:
    """A chunk quoting its own reporter citation must not cite itself —
    a self-loop is pure noise in the PageRank projection."""
    assert "<> row.source" in edge_merge_cypher(EdgeKind.CITES)


# --- edge_rows: the pure resolution step --------------------------------------

_ACT_INDEX = ActIndex.from_records(
    [
        ("IND_central_11", "The Negotiable Instruments Act, 1881", "central"),
        ("IND_central_12", "The Companies Act, 1956", "central"),
    ]
)


def test_rows_for_a_simple_kind_carry_only_source_and_target() -> None:
    rows = edge_rows(EdgeKind.PART_OF, [Edge("chunk", "act", EdgeKind.PART_OF)])
    assert rows == [{"source": "chunk", "target": "act"}]


def test_interprets_row_resolves_a_hint_to_an_act_id() -> None:
    edge = Edge("case", "138", EdgeKind.INTERPRETS, hint="Negotiable Instruments Act")
    assert edge_rows(EdgeKind.INTERPRETS, [edge], _ACT_INDEX) == [
        {
            "source": "case",
            "target": "138",
            "act_id": "IND_central_11",
            "stub_key": "section:138",
        }
    ]


def test_interprets_row_without_a_hint_carries_a_null_act_id() -> None:
    edge = Edge("case", "302", EdgeKind.INTERPRETS)
    assert edge_rows(EdgeKind.INTERPRETS, [edge], _ACT_INDEX)[0]["act_id"] is None


def test_interprets_row_with_an_unresolvable_hint_carries_a_null_act_id() -> None:
    edge = Edge("case", "302", EdgeKind.INTERPRETS, hint="said Act")
    assert edge_rows(EdgeKind.INTERPRETS, [edge], _ACT_INDEX)[0]["act_id"] is None


def test_references_row_resolves_the_act_title_to_an_act_id() -> None:
    edge = Edge("chunk", "Companies Act, 1956", EdgeKind.REFERENCES)
    row = edge_rows(EdgeKind.REFERENCES, [edge], _ACT_INDEX)[0]
    assert row["act_id"] == "IND_central_12"
    assert row["stub_key"] == "act:companies act 1956"


def test_references_row_for_an_unknown_act_still_gets_a_normalised_stub_key() -> None:
    """Two spellings of one unknown act must share a stub, not split into two."""
    first = edge_rows(
        EdgeKind.REFERENCES, [Edge("c", "The Unknown Act, 1999", EdgeKind.REFERENCES)]
    )[0]
    second = edge_rows(
        EdgeKind.REFERENCES, [Edge("c", "Unknown Act,1999", EdgeKind.REFERENCES)]
    )[0]
    assert first["act_id"] is None
    assert first["stub_key"] == second["stub_key"] == "act:unknown act 1999"


def test_references_row_keeps_an_unnormalisable_title_distinguishable() -> None:
    """A title that normalises to nothing must not collapse onto a shared
    'act:' key with every other such title."""
    rows = edge_rows(
        EdgeKind.REFERENCES,
        [Edge("c", "???", EdgeKind.REFERENCES), Edge("c", "!!!", EdgeKind.REFERENCES)],
    )
    assert rows[0]["stub_key"] != rows[1]["stub_key"]


def test_cites_row_carries_a_namespaced_judgment_stub_key() -> None:
    rows = edge_rows(EdgeKind.CITES, [Edge("case", "1979SCR453", EdgeKind.CITES)])
    assert rows[0]["stub_key"] == "judgment:1979SCR453"


def test_rows_are_deduplicated_before_being_sent() -> None:
    edge = Edge("case", "302", EdgeKind.INTERPRETS)
    assert len(edge_rows(EdgeKind.INTERPRETS, [edge, edge])) == 1


def test_rows_of_an_empty_edge_list_are_empty() -> None:
    assert edge_rows(EdgeKind.CITES, []) == []


# --- live-Neo4j proof that the fan-out is actually gone -----------------------

_ACTS = [
    {"act_id": "TEST_ACT_IPC", "title": "The Test Penal Code Act, 1860",
     "jurisdiction": "central", "in_force": True, "corpus_version": "test"},
    {"act_id": "TEST_ACT_MUNI", "title": "The Test Municipal Act, 1999",
     "jurisdiction": "goa", "in_force": True, "corpus_version": "test"},
]
_SECTIONS = [
    {"chunk_id": "TEST_ACT_IPC_302", "act_id": "TEST_ACT_IPC",
     "section_number": "TESTSEC302", "in_force": True,
     "source_url": "https://example.gov.in/ipc", "corpus_version": "test"},
    {"chunk_id": "TEST_ACT_MUNI_302", "act_id": "TEST_ACT_MUNI",
     "section_number": "TESTSEC302", "in_force": True,
     "source_url": "https://example.gov.in/muni", "corpus_version": "test"},
]
_TEST_INDEX = ActIndex.from_records(
    [(a["act_id"], a["title"], a["jurisdiction"]) for a in _ACTS]
)


def _seed_acts_and_sections(driver: neo4j.Driver) -> None:
    load_nodes(driver, "Act", list(_ACTS))
    load_nodes(driver, "Section", list(_SECTIONS))


def _teardown_acts_and_sections(driver: neo4j.Driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (n) WHERE n.act_id STARTS WITH 'TEST_ACT_' "
            "OR n.chunk_id STARTS WITH 'TEST_ACT_' "
            "OR n.stub_key IN ['section:TESTSEC302', 'act:test unknown act 1999'] "
            "DETACH DELETE n"
        )


def _interprets_targets(driver: neo4j.Driver, case_id: str) -> list[dict]:
    with driver.session() as session:
        return session.run(
            "MATCH (:Judgment {case_id: $case_id})-[:INTERPRETS]->(s) "
            "RETURN s.chunk_id AS chunk_id, s.stub_key AS stub_key, "
            "'Stub' IN labels(s) AS is_stub ORDER BY chunk_id, stub_key",
            case_id=case_id,
        ).data()


@pytest.mark.integration
def test_interprets_attaches_to_exactly_the_act_the_judgment_named(
    live_driver: neo4j.Driver,
) -> None:
    """The regression that motivated the whole fix: a judgment about section
    302 of the penal code must NOT become graph-adjacent to section 302 of an
    unrelated municipal act."""
    case_id = "TEST_INTERPRETS_CASE"
    try:
        _seed_acts_and_sections(live_driver)
        load_nodes(live_driver, "Judgment", [{
            "case_id": case_id, "join_key": "TESTINTERPRETSCASE",
            "citation_key": None, "court_code": "SCI", "decision_date": None,
            "decision_year": None, "corpus_version": "test"}])
        load_edges(
            live_driver,
            EdgeKind.INTERPRETS,
            [Edge(case_id, "TESTSEC302", EdgeKind.INTERPRETS, hint="Test Penal Code Act")],
            act_index=_TEST_INDEX,
        )

        targets = _interprets_targets(live_driver, case_id)

        assert targets == [
            {"chunk_id": "TEST_ACT_IPC_302", "stub_key": None, "is_stub": False}
        ]
    finally:
        _cleanup(live_driver, [case_id])
        _teardown_acts_and_sections(live_driver)


@pytest.mark.integration
def test_interprets_without_a_usable_hint_makes_one_namespaced_stub(
    live_driver: neo4j.Driver,
) -> None:
    """No hint means the text did not say which act. One stub, in its own
    namespace, and no edge onto any real Section."""
    case_id = "TEST_INTERPRETS_CASE_2"
    try:
        _seed_acts_and_sections(live_driver)
        load_nodes(live_driver, "Judgment", [{
            "case_id": case_id, "join_key": "TESTINTERPRETSCASE2",
            "citation_key": None, "court_code": "SCI", "decision_date": None,
            "decision_year": None, "corpus_version": "test"}])
        load_edges(
            live_driver,
            EdgeKind.INTERPRETS,
            [Edge(case_id, "TESTSEC302", EdgeKind.INTERPRETS)],
            act_index=_TEST_INDEX,
        )

        targets = _interprets_targets(live_driver, case_id)

        assert targets == [
            {"chunk_id": None, "stub_key": "section:TESTSEC302", "is_stub": True}
        ]
    finally:
        _cleanup(live_driver, [case_id])
        _teardown_acts_and_sections(live_driver)


@pytest.mark.integration
def test_references_resolves_a_known_act_and_stubs_an_unknown_one(
    live_driver: neo4j.Driver,
) -> None:
    try:
        _seed_acts_and_sections(live_driver)
        load_edges(
            live_driver,
            EdgeKind.REFERENCES,
            [
                Edge("TEST_ACT_IPC_302", "Test Municipal Act, 1999", EdgeKind.REFERENCES),
                Edge("TEST_ACT_IPC_302", "The Unknown Act, 1999", EdgeKind.REFERENCES),
            ],
            act_index=_TEST_INDEX,
        )

        with live_driver.session() as session:
            rows = session.run(
                "MATCH (:Section {chunk_id: 'TEST_ACT_IPC_302'})-[:REFERENCES]->(a:Act) "
                "RETURN a.act_id AS act_id, a.stub_key AS stub_key "
                "ORDER BY coalesce(a.act_id, a.stub_key)"
            ).data()

        assert rows == [
            {"act_id": "TEST_ACT_MUNI", "stub_key": None},
            {"act_id": None, "stub_key": "act:unknown act 1999"},
        ]
    finally:
        _teardown_acts_and_sections(live_driver)


_DESTRUCTIVE_ENV = "OPENLAW_ALLOW_DESTRUCTIVE_TESTS"

destructive = pytest.mark.skipif(
    os.environ.get(_DESTRUCTIVE_ENV) != "1",
    reason=(
        f"empties the whole graph; set {_DESTRUCTIVE_ENV}=1 to run it. Left off "
        "by default because every other integration test reads the loaded "
        "corpus slice this would delete."
    ),
)


@pytest.mark.integration
@destructive
def test_drop_graph_removes_every_node_and_relationship(
    live_driver: neo4j.Driver,
) -> None:
    """The plan's global constraint: the graph is derived data, dropped and
    rebuilt per corpus release. Repealed sections surviving a rebuild is a
    correctness problem for a legal product."""
    with live_driver.session() as session:
        session.run(
            "MERGE (a:Judgment {case_id: 'TEST_DROP_A'}) "
            "MERGE (b:Judgment {case_id: 'TEST_DROP_B'}) MERGE (a)-[:CITES]->(b)"
        )

    dropped = drop_graph(live_driver, batch_size=1)

    assert dropped >= 2
    with live_driver.session() as session:
        assert session.run("MATCH (n) RETURN count(n) AS n").single()["n"] == 0
        assert session.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"] == 0


@pytest.mark.integration
@destructive
def test_drop_graph_is_a_no_op_on_an_already_empty_graph(
    live_driver: neo4j.Driver,
) -> None:
    drop_graph(live_driver)
    assert drop_graph(live_driver) == 0


def test_drop_graph_rejects_a_batch_size_that_is_not_a_positive_int() -> None:
    """`batch_size` is interpolated straight into `IN TRANSACTIONS OF n ROWS`,
    so it must be validated here rather than failing as Cypher syntax."""
    for bad in (0, -1, 2.5, True, "1000"):
        with pytest.raises(ValueError):
            drop_graph(object(), batch_size=bad)  # type: ignore[arg-type]
