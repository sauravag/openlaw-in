import neo4j
import pytest

from openlaw.graph.schema import CONSTRAINTS, INDEXES, apply_schema


def test_every_node_label_has_a_uniqueness_constraint() -> None:
    """Rebuilds must not create duplicate nodes for the same identifier."""
    joined = " ".join(CONSTRAINTS)
    for label in (
        "Act", "Section", "Judgment", "Court", "Judge", "DefinedTerm", "LegalSubject"
    ):
        assert f":{label}" in joined, f"{label} has no uniqueness constraint"


def test_constraints_are_idempotent() -> None:
    """The builder reruns per corpus release, so DDL must be safe to repeat."""
    for statement in CONSTRAINTS:
        assert "IF NOT EXISTS" in statement


def test_indexes_are_idempotent() -> None:
    for statement in INDEXES:
        assert "IF NOT EXISTS" in statement


def test_judgment_is_indexed_on_the_fields_retrieval_filters_on() -> None:
    joined = " ".join(INDEXES)
    assert "court_code" in joined
    assert "decision_year" in joined


def test_judgment_is_indexed_on_citation_key_for_cites_resolution() -> None:
    """The CITES loader (Task 5) resolves targets against citation_key first —
    see openlaw.graph.loader — so it must be indexed at corpus scale."""
    assert "citation_key" in " ".join(INDEXES)


def test_section_is_indexed_for_act_and_number_lookup() -> None:
    joined = " ".join(INDEXES)
    assert "act_id" in joined
    assert "section_number" in joined


def test_pagerank_is_indexed_for_authority_ordering() -> None:
    assert "pagerank" in " ".join(INDEXES)


@pytest.mark.integration
def test_apply_schema_is_safe_to_rerun_against_live_neo4j(
    live_driver: neo4j.Driver,
) -> None:
    """Applying the schema twice must succeed and run the same statement count."""
    first_run = apply_schema(live_driver)
    second_run = apply_schema(live_driver)

    assert first_run == len(CONSTRAINTS) + len(INDEXES)
    assert second_run == first_run


# --- Task 2 whole-plan review: the stub namespace needs its own constraint ----


def test_stub_nodes_have_a_uniqueness_constraint_on_their_namespaced_key() -> None:
    """Fan-out must be structurally impossible, not merely unlikely: the
    constraint is what guarantees one stub node per namespaced key."""
    joined = " ".join(CONSTRAINTS)
    assert ":Stub" in joined
    assert "stub_key IS UNIQUE" in joined


def test_sections_are_indexed_on_act_and_number_together() -> None:
    """INTERPRETS resolves a section by (act_id, section_number) as a pair —
    one composite index seek per edge row, at ~120,000 rows."""
    assert any(
        "s.act_id, s.section_number" in statement for statement in INDEXES
    )
