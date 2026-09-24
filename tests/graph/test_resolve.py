"""Unit tests for deterministic reference resolution and stub namespacing.

The defect these guard against: a stub node keyed on a property real nodes
also carry (`Section.section_number`, `Act.title`) makes a Cypher MERGE fan
out onto every real node sharing that value. Every stub key produced here
must therefore live in a namespace no real node can occupy.
"""

import neo4j
import pytest

from openlaw.graph.resolve import (
    ACT_INDEX_CYPHER,
    ACT_STUB,
    EMPTY_ACT_INDEX,
    JUDGMENT_STUB,
    SECTION_STUB,
    ActIndex,
    act_title_stem,
    load_act_index,
    normalise_act_title,
    stub_key,
)

_CENTRAL = "central"


def _index() -> ActIndex:
    return ActIndex.from_records(
        [
            ("IND_central_1", "The Companies Act, 1956", _CENTRAL),
            ("IND_central_2", "The Companies Act, 2013", _CENTRAL),
            ("IND_central_3", "The Indian Evidence Act, 1872", _CENTRAL),
            ("IND_delhi_4", "The Indian Evidence Act, 1872", "delhi"),
            ("IND_bihar_5", "The Bihar Land Reforms Act, 1950", "bihar"),
            ("IND_goa_6", "The Shops Act, 1973", "goa"),
            ("IND_kerala_7", "The Shops Act, 1973", "kerala"),
        ]
    )


# --- stub key namespacing -----------------------------------------------------


def test_stub_key_is_namespaced_by_kind() -> None:
    assert stub_key(SECTION_STUB, "302") == "section:302"


def test_stub_keys_of_different_kinds_never_collide() -> None:
    """A bare '302' as a section and as anything else must stay distinct."""
    assert stub_key(SECTION_STUB, "302") != stub_key(ACT_STUB, "302")
    assert stub_key(JUDGMENT_STUB, "302") != stub_key(SECTION_STUB, "302")


def test_stub_key_rejects_an_empty_value() -> None:
    """An empty value would produce the single key 'section:', which every
    unparseable reference would then share."""
    with pytest.raises(ValueError):
        stub_key(SECTION_STUB, "  ")


def test_stub_key_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        stub_key("chapter", "1")


# --- act title normalisation --------------------------------------------------


def test_normalise_drops_the_leading_article_so_a_reference_matches_a_title() -> None:
    """Act.title is 'The Companies Act, 1956'; acts_referenced says
    'Companies Act, 1956'. They are the same act."""
    assert normalise_act_title("The Companies Act, 1956") == normalise_act_title(
        "Companies Act, 1956"
    )


def test_normalise_collapses_punctuation_and_case() -> None:
    """Real corpus title: 'The Provisional collection of Taxes Act,2023' —
    note the missing space after the comma."""
    assert (
        normalise_act_title("The Provisional collection of Taxes Act,2023")
        == "provisional collection of taxes act 2023"
    )


def test_normalise_of_none_is_empty() -> None:
    assert normalise_act_title(None) == ""


def test_stem_drops_a_trailing_year() -> None:
    """Judgment text says 'the Negotiable Instruments Act'; the corpus title
    carries a year."""
    assert act_title_stem("negotiable instruments act 1881") == (
        "negotiable instruments act"
    )


def test_stem_leaves_a_title_without_a_year_untouched() -> None:
    assert act_title_stem("negotiable instruments act") == "negotiable instruments act"


def test_stem_does_not_strip_a_year_that_is_the_whole_title() -> None:
    assert act_title_stem("1881") == "1881"


# --- ActIndex resolution ------------------------------------------------------


def test_index_resolves_an_exact_title_to_its_act_id() -> None:
    assert _index().act_id_for("Companies Act, 1956") == "IND_central_1"


def test_index_resolves_a_year_less_reference_when_only_one_act_matches() -> None:
    assert _index().act_id_for("Bihar Land Reforms Act") == "IND_bihar_5"


def test_index_refuses_an_ambiguous_year_less_reference() -> None:
    """'Companies Act' matches both the 1956 and the 2013 Act. Guessing would
    attach a judgment to the wrong statute, so it must not resolve."""
    assert _index().act_id_for("Companies Act") is None


def test_index_prefers_the_single_central_act_when_states_share_a_title() -> None:
    """'Indian Evidence Act, 1872' exists centrally and as a state reprint.
    The central one is the statute a Supreme Court judgment means."""
    assert _index().act_id_for("Indian Evidence Act, 1872") == "IND_central_3"


def test_index_refuses_when_several_states_share_a_title_and_none_is_central() -> None:
    assert _index().act_id_for("Shops Act, 1973") is None


def test_index_returns_none_for_an_unknown_title() -> None:
    assert _index().act_id_for("The Entirely Invented Act, 1999") is None


def test_index_returns_none_for_none() -> None:
    assert _index().act_id_for(None) is None


def test_index_returns_none_for_blank_text() -> None:
    assert _index().act_id_for("   ") is None


def test_empty_index_resolves_nothing() -> None:
    assert EMPTY_ACT_INDEX.act_id_for("Companies Act, 1956") is None


def test_index_is_immutable() -> None:
    index = _index()
    with pytest.raises(TypeError):
        index.by_key["companies act 1956"] = "SPOOFED"  # type: ignore[index]


def test_index_ignores_records_without_an_act_id_or_title() -> None:
    index = ActIndex.from_records([("", "The Ghost Act, 1900", _CENTRAL), ("X", "", _CENTRAL)])
    assert index.act_id_for("Ghost Act, 1900") is None


def test_index_is_deterministic_regardless_of_record_order() -> None:
    records = [
        ("IND_central_3", "The Indian Evidence Act, 1872", _CENTRAL),
        ("IND_delhi_4", "The Indian Evidence Act, 1872", "delhi"),
    ]
    forward = ActIndex.from_records(records)
    backward = ActIndex.from_records(list(reversed(records)))
    assert forward.act_id_for("Indian Evidence Act, 1872") == backward.act_id_for(
        "Indian Evidence Act, 1872"
    )


# --- building the index from the graph ----------------------------------------


def test_act_index_cypher_reads_only_real_acts() -> None:
    """A stub Act has no act_id and must never enter the index — resolving a
    reference onto a placeholder is exactly the bug being fixed."""
    assert "a.act_id IS NOT NULL" in ACT_INDEX_CYPHER


@pytest.mark.integration
def test_load_act_index_resolves_an_act_that_is_actually_in_the_graph(
    live_driver: neo4j.Driver,
) -> None:
    from openlaw.graph.loader import load_nodes

    try:
        load_nodes(live_driver, "Act", [{
            "act_id": "TEST_RESOLVE_ACT", "title": "The Test Resolve Act, 1911",
            "jurisdiction": "central", "in_force": True, "corpus_version": "test"}])

        index = load_act_index(live_driver)

        assert index.act_id_for("Test Resolve Act, 1911") == "TEST_RESOLVE_ACT"
        assert index.act_id_for("Test Resolve Act") == "TEST_RESOLVE_ACT"
    finally:
        with live_driver.session() as session:
            session.run(
                "MATCH (a:Act {act_id: 'TEST_RESOLVE_ACT'}) DETACH DELETE a"
            )
