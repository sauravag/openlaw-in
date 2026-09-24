import neo4j
import pytest

from openlaw.graph.queries import (
    MAX_DEPTH,
    acts_referenced_by,
    acts_referenced_by_cypher,
    expand_chunks,
    expand_chunks_cypher,
    judgments_interpreting,
    judgments_interpreting_cypher,
    precedent_lineage,
    precedent_lineage_cypher,
    sections_for_act,
    sections_for_act_cypher,
)


class _UnusedDriver:
    """A driver stand-in that fails the test if the database is ever touched.

    Used to prove a fast path (an empty input, a rejected depth) returns
    before opening a session — important on the request path, where a wasted
    round trip is exactly the kind of thing the 100ms budget cannot absorb.
    """

    def session(self) -> neo4j.Session:
        raise AssertionError("driver.session() should not have been called")


# --- Cypher builders: structure, not live data -----------------------------


def test_judgments_interpreting_orders_by_authority() -> None:
    """PageRank ordering is what makes landmark precedent surface first."""
    statement = judgments_interpreting_cypher()
    assert "INTERPRETS" in statement
    assert "ORDER BY j.pagerank DESC" in statement


def test_judgments_interpreting_is_bounded() -> None:
    assert "LIMIT $limit" in judgments_interpreting_cypher()


def test_judgments_interpreting_excludes_stub_judgments() -> None:
    """A stub Judgment (join_key only, no case_id) must never be returned."""
    assert "j.case_id IS NOT NULL" in judgments_interpreting_cypher()


def test_sections_for_act_is_bounded() -> None:
    assert "LIMIT $limit" in sections_for_act_cypher()


def test_sections_for_act_orders_by_section_number() -> None:
    assert "ORDER BY s.section_number" in sections_for_act_cypher()


def test_acts_referenced_by_is_bounded() -> None:
    assert "LIMIT $limit" in acts_referenced_by_cypher()


def test_acts_referenced_by_excludes_stub_acts() -> None:
    """A title-only stub Act (no act_id) must never be returned."""
    assert "a.act_id IS NOT NULL" in acts_referenced_by_cypher()


def test_expand_chunks_stays_within_two_hops() -> None:
    """Request-path expansion must not walk the graph deeply."""
    statement = expand_chunks_cypher()
    assert "*1..2" in statement
    assert "LIMIT $limit" in statement


def test_expand_chunks_excludes_stub_sections() -> None:
    """A bare-number placeholder Section (no chunk_id) must never be returned."""
    assert "related.chunk_id IS NOT NULL" in expand_chunks_cypher()


def test_expand_chunks_excludes_stub_acts() -> None:
    """A title-only placeholder Act (no act_id) must never be returned."""
    assert "related.act_id IS NOT NULL" in expand_chunks_cypher()


def test_expand_chunks_excludes_stub_judgments() -> None:
    """A join_key-only placeholder Judgment (no case_id) must never be returned."""
    assert "related.case_id IS NOT NULL" in expand_chunks_cypher()


def test_expand_chunks_is_anchored_on_the_indexed_chunk_id() -> None:
    """The anchor lookup must use Section.chunk_id, which carries a constraint
    (and therefore an index) — this is what keeps the query index-backed."""
    assert "s.chunk_id IN $chunk_ids" in expand_chunks_cypher()


def test_precedent_lineage_accepts_a_bounded_depth() -> None:
    assert "CITES*1..3" in precedent_lineage_cypher(depth=3)


def test_precedent_lineage_accepts_the_max_depth() -> None:
    assert f"CITES*1..{MAX_DEPTH}" in precedent_lineage_cypher(depth=MAX_DEPTH)


def test_precedent_lineage_rejects_depth_above_the_cap() -> None:
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=MAX_DEPTH + 1)


def test_precedent_lineage_rejects_non_positive_depth() -> None:
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=0)


def test_precedent_lineage_rejects_negative_depth() -> None:
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=-1)


def test_precedent_lineage_rejects_a_float_depth() -> None:
    """A float would f-string-interpolate into a malformed `CITES*1..2.5`,
    deferring failure to an opaque Neo4j syntax error — must be caught here."""
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=2.5)  # type: ignore[arg-type]


def test_precedent_lineage_rejects_a_bool_depth() -> None:
    """bool is a subclass of int in Python, so `isinstance(True, int)` is
    True — without an explicit bool check, depth=True would silently become
    CITES*1..1 instead of raising."""
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth=True)  # type: ignore[arg-type]


def test_precedent_lineage_rejects_a_string_depth_with_value_error() -> None:
    """Must raise the documented ValueError, not an incidental TypeError from
    the `depth < 1` comparison."""
    with pytest.raises(ValueError):
        precedent_lineage_cypher(depth="3")  # type: ignore[arg-type]


def test_precedent_lineage_flags_stub_rows_rather_than_hiding_them() -> None:
    """The citation network is the point of this query, so a citation to a
    case outside the corpus is left in — but tagged, and identified by its
    join_key, so a caller after retrievable content only can filter it out."""
    statement = precedent_lineage_cypher(depth=2)
    assert "cited.case_id IS NULL AS is_stub" in statement
    assert "cited.join_key AS join_key" in statement


def test_max_depth_is_capped_at_six() -> None:
    assert MAX_DEPTH == 6


# --- Wrapper functions: fast paths that must not touch the database --------


def test_expand_chunks_returns_empty_list_without_a_query() -> None:
    assert expand_chunks(_UnusedDriver(), []) == []  # type: ignore[arg-type]


def test_precedent_lineage_rejects_invalid_depth_before_opening_a_session() -> None:
    with pytest.raises(ValueError):
        precedent_lineage(_UnusedDriver(), "any_case_id", depth=MAX_DEPTH + 1)  # type: ignore[arg-type]


# --- Wrapper functions against a live, partially-loaded slice --------------


@pytest.mark.integration
def test_expand_chunks_never_returns_a_stub(live_driver: neo4j.Driver) -> None:
    """Assert on behaviour that holds regardless of how much data is loaded:
    every row `expand_chunks` returns must carry a non-null id, because a
    null id there could only come from a stub node (see the module
    docstring)."""
    with live_driver.session() as session:
        chunk_ids = [
            record["chunk_id"]
            for record in session.run(
                "MATCH (s:Section) WHERE s.chunk_id IS NOT NULL "
                "RETURN s.chunk_id AS chunk_id LIMIT 5"
            )
        ]
    if not chunk_ids:
        pytest.skip("no Section nodes with a chunk_id are loaded")

    rows = expand_chunks(live_driver, chunk_ids, limit=40)

    assert len(rows) <= 40
    for row in rows:
        assert row["id"] is not None
        assert row["label"] in {"Section", "Act", "Judgment"}


@pytest.mark.integration
def test_expand_chunks_respects_the_limit(live_driver: neo4j.Driver) -> None:
    with live_driver.session() as session:
        chunk_ids = [
            record["chunk_id"]
            for record in session.run(
                "MATCH (s:Section) WHERE s.chunk_id IS NOT NULL "
                "RETURN s.chunk_id AS chunk_id LIMIT 20"
            )
        ]
    if not chunk_ids:
        pytest.skip("no Section nodes with a chunk_id are loaded")

    rows = expand_chunks(live_driver, chunk_ids, limit=3)

    assert len(rows) <= 3


@pytest.mark.integration
def test_judgments_interpreting_never_returns_a_stub(
    live_driver: neo4j.Driver,
) -> None:
    with live_driver.session() as session:
        record = session.run(
            "MATCH (:Judgment)-[:INTERPRETS]->(s:Section) "
            "RETURN s.section_number AS section_number LIMIT 1"
        ).single()
    if record is None:
        pytest.skip("no INTERPRETS edges are loaded")

    rows = judgments_interpreting(live_driver, record["section_number"])

    assert all(row["case_id"] is not None for row in rows)
    pageranks = [row["pagerank"] for row in rows if row["pagerank"] is not None]
    assert pageranks == sorted(pageranks, reverse=True)


@pytest.mark.integration
def test_sections_for_act_returns_real_sections_in_order(
    live_driver: neo4j.Driver,
) -> None:
    with live_driver.session() as session:
        record = session.run(
            "MATCH (:Section)-[:PART_OF]->(a:Act) "
            "RETURN a.act_id AS act_id LIMIT 1"
        ).single()
    if record is None:
        pytest.skip("no PART_OF edges are loaded")

    rows = sections_for_act(live_driver, record["act_id"])

    numbers = [row["section_number"] for row in rows]
    assert numbers == sorted(numbers)
    assert all(row["chunk_id"] is not None for row in rows)


@pytest.mark.integration
def test_acts_referenced_by_never_returns_a_stub(live_driver: neo4j.Driver) -> None:
    with live_driver.session() as session:
        record = session.run(
            "MATCH (s:Section)-[:REFERENCES]->(:Act) "
            "RETURN s.chunk_id AS chunk_id LIMIT 1"
        ).single()
    if record is None:
        pytest.skip("no REFERENCES edges are loaded")

    rows = acts_referenced_by(live_driver, record["chunk_id"])

    assert all(row["act_id"] is not None for row in rows)


@pytest.mark.integration
def test_precedent_lineage_hops_stay_within_the_requested_depth(
    live_driver: neo4j.Driver,
) -> None:
    with live_driver.session() as session:
        record = session.run(
            "MATCH (j:Judgment)-[:CITES]->() WHERE j.case_id IS NOT NULL "
            "RETURN j.case_id AS case_id LIMIT 1"
        ).single()
    if record is None:
        pytest.skip("no outgoing CITES edges from a real judgment are loaded")

    rows = precedent_lineage(live_driver, record["case_id"], depth=2)

    for row in rows:
        assert 1 <= row["hops"] <= 2
        assert row["is_stub"] == (row["case_id"] is None)
        if row["is_stub"]:
            assert row["join_key"] is not None
