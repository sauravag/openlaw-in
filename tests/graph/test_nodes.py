from openlaw.corpus.schema import JudgmentChunk, Provision
from openlaw.graph.nodes import (
    act_node,
    court_node,
    judge_nodes,
    judgment_node,
    section_node,
    subject_nodes,
    term_nodes,
)


def _chunk() -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR", "vide"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )


def _provision() -> Provision:
    return Provision.from_row(
        {
            "chunk_id": "IND_central_2385_388",
            "act_id": "IND_central_2385",
            "title": "The Indian Succession Act, 1925",
            "section_number": "388",
            "text": "An order of a District Judge ...",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['tax_law', 'family_law']",
            "acts_referenced": ["Indian Succession Act, 1925"],
            "defined_terms": ["Executor", "administrator"],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )


def test_judgment_node_keys_on_case_id_and_carries_the_join_key() -> None:
    node = judgment_node(_chunk())
    assert node["case_id"] == "2016_INSC_1151"
    assert node["join_key"] == "2016INSC1151"


def test_judgment_node_carries_a_decision_year_for_index_filtering() -> None:
    assert judgment_node(_chunk())["decision_year"] == 2016


def test_judgment_node_year_is_none_when_the_date_is_absent() -> None:
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "c", "case_id": "2016_INSC_1", "chunk_index": 0,
            "text": "t", "text_original": "t", "court_code": "SCI",
            "judges": [], "decision_date": "", "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    node = judgment_node(chunk)
    assert node["decision_year"] is None
    assert node["decision_date"] is None


def test_judge_nodes_exclude_parser_noise() -> None:
    """'vide' is real observed noise in the vaquill judges field."""
    assert {node["name"] for node in judge_nodes(_chunk())} == {"T.S. THAKUR"}


def test_section_node_keys_on_chunk_id_and_links_its_act() -> None:
    node = section_node(_provision())
    assert node["chunk_id"] == "IND_central_2385_388"
    assert node["act_id"] == "IND_central_2385"
    assert node["section_number"] == "388"


def test_section_node_carries_the_source_url() -> None:
    assert section_node(_provision())["source_url"].startswith("https://")


def test_act_node_keys_on_act_id() -> None:
    node = act_node(_provision())
    assert node["act_id"] == "IND_central_2385"
    assert node["title"] == "The Indian Succession Act, 1925"
    assert node["in_force"] is True


def test_court_node_keys_on_code() -> None:
    assert court_node("SCI")["code"] == "SCI"


def test_term_nodes_are_lowercased_for_matching() -> None:
    """'Executor' and 'executor' must collapse to one node."""
    assert {node["term"] for node in term_nodes(_provision())} == {
        "executor",
        "administrator",
    }


def test_subject_nodes_come_from_the_parsed_encoded_list() -> None:
    assert {node["name"] for node in subject_nodes(_provision())} == {
        "tax_law",
        "family_law",
    }


def test_builders_do_not_mutate_their_input() -> None:
    provision = _provision()
    section_node(provision)
    term_nodes(provision)
    assert provision.defined_terms == ("Executor", "administrator")
