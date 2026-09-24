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


def test_expand_chunks_excludes_every_stub_by_label() -> None:
    """The not-null key checks below are per-label and easy to get wrong as
    labels grow. `NOT related:Stub` is the one check that holds for all of
    them, because a placeholder is never anything else."""
    assert "NOT related:Stub" in expand_chunks_cypher()


def test_expand_chunks_cannot_traverse_the_legal_subject_supernode() -> None:
    """`legal_subject` is 62% 'general' — 336,224 of 544,078 provisions — so
    at full scale `LegalSubject {name: 'general'}` is a ~336k-degree node. An
    untyped undirected `*1..2` walk goes Section -> that node -> 336k
    Sections, which no 100ms request-path budget survives. HAS_SUBJECT and
    DEFINES are therefore excluded from the traversal: both lead only to
    nodes this query filters out anyway, so nothing retrievable is lost."""
    statement = expand_chunks_cypher()
    assert ":PART_OF|REFERENCES|INTERPRETS*1..2" in statement
    assert "HAS_SUBJECT" not in statement
    assert "DEFINES" not in statement


def test_judgments_interpreting_can_be_narrowed_to_one_act() -> None:
    """'Section 302' means nothing on its own — every act has sections with
    low numbers. A caller that knows the act must be able to say so."""
    assert "$act_id" in judgments_interpreting_cypher()


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
    """Asserting only `label in {Section, Act, Judgment}` was vacuous — every
    stub carries one of those labels too. What actually distinguishes a stub
    is that it lacks its label's uniqueness-constrained key, and that it is
    labelled `:Stub`. Both are asserted per returned node, against the real
    node, not against the projected row."""
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
        assert row["id"] is not None, "a null id could only come from a stub"
        assert row["label"] in {"Section", "Act", "Judgment"}

    with live_driver.session() as session:
        stubs = session.run(
            "MATCH (n:Stub) WHERE n.chunk_id IN $ids OR n.act_id IN $ids "
            "OR n.case_id IN $ids RETURN count(n) AS n",
            ids=[row["id"] for row in rows],
        ).single()["n"]
    assert stubs == 0


@pytest.mark.integration
def test_a_judgment_is_reachable_from_a_real_section(
    live_driver: neo4j.Driver,
) -> None:
    """The product feature the whole graph exists for: given a statutory
    provision a citizen's question matched, surface the case law interpreting
    it. No test asserted this before, which is how a defect that made it
    either wrong or impossible survived seven scoped reviews.

    Wrong: INTERPRETS keyed on the non-unique `section_number` fanned every
    judgment onto every same-numbered section of every act. Impossible: on a
    clean build the judgments phase ran first, so INTERPRETS attached only to
    placeholders, real Sections received none, and `expand_chunks` — which
    filters placeholders — could never return a Judgment at all."""
    with live_driver.session() as session:
        record = session.run(
            "MATCH (j:Judgment)-[:INTERPRETS]->(s:Section) "
            "WHERE j.case_id IS NOT NULL AND s.chunk_id IS NOT NULL "
            "AND NOT s:Stub RETURN s.chunk_id AS chunk_id LIMIT 1"
        ).single()
    if record is None:
        pytest.skip("no INTERPRETS edge onto a real Section is loaded")

    rows = expand_chunks(live_driver, [record["chunk_id"]], limit=40)

    judgments = [row for row in rows if row["label"] == "Judgment"]
    assert judgments, "the statute-to-case-law bridge returned nothing"
    assert all(row["id"] is not None for row in judgments)


@pytest.mark.integration
def test_interprets_edges_reach_real_sections_not_only_placeholders(
    live_driver: neo4j.Driver,
) -> None:
    """A whole-graph invariant, independent of which chunk is sampled: if any
    INTERPRETS edge exists at all, some of them must land on real Sections.
    An all-placeholder INTERPRETS population is the silent-empty failure."""
    with live_driver.session() as session:
        counts = session.run(
            "MATCH (:Judgment)-[:INTERPRETS]->(s:Section) "
            "RETURN sum(CASE WHEN s:Stub THEN 1 ELSE 0 END) AS stubs, "
            "sum(CASE WHEN s:Stub THEN 0 ELSE 1 END) AS real"
        ).single()
    if (counts["stubs"] or 0) + (counts["real"] or 0) == 0:
        pytest.skip("no INTERPRETS edges are loaded")

    assert counts["real"] > 0


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


def test_judgments_interpreting_excludes_placeholder_sections() -> None:
    """With no act_id, matching on the bare number would otherwise count the
    placeholder created when a judgment named a section without its statute —
    which is precisely the case where the act is unknown."""
    assert "NOT s:Stub" in judgments_interpreting_cypher()
