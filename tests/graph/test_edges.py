from openlaw.corpus.schema import JudgmentChunk, Provision
from openlaw.graph.edges import (
    Edge,
    EdgeKind,
    group_by_kind,
    judgment_edges,
    provision_edges,
)


def _chunk(text: str = "the appeal is dismissed") -> JudgmentChunk:
    return JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": text,
            "text_original": text,
            "court_code": "SCI",
            "judges": ["T.S. THAKUR"],
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
            "text": "text",
            "jurisdiction": "central",
            "in_force": True,
            "legal_subject": "['family_law']",
            "acts_referenced": ["Code of Civil Procedure, 1908"],
            "defined_terms": ["Executor"],
            "source_url": "https://www.indiacode.nic.in/handle/123456789/2385",
        },
        corpus_version="v2026.08",
    )


def test_judgment_links_to_its_court() -> None:
    assert Edge("2016_INSC_1151", "SCI", EdgeKind.IN_COURT) in judgment_edges(_chunk())


def test_judgment_links_to_its_judges() -> None:
    assert Edge(
        "2016_INSC_1151", "T.S. THAKUR", EdgeKind.DECIDED_BY
    ) in judgment_edges(_chunk())


def test_judgment_cites_extracted_citations() -> None:
    edges = judgment_edges(_chunk("following [1979] 3 S.C.R. 453 this Court holds"))
    assert Edge("2016_INSC_1151", "1979SCR453", EdgeKind.CITES) in edges


def test_judgment_interprets_referenced_sections() -> None:
    edges = judgment_edges(_chunk("an offence under Section 302 of the Code"))
    assert Edge("2016_INSC_1151", "302", EdgeKind.INTERPRETS) in edges


def test_judgment_does_not_cite_itself() -> None:
    """A chunk quoting its own citation must not create a self-loop."""
    edges = judgment_edges(_chunk("this case, 2016 INSC 1151, holds"))
    assert not any(
        edge.kind is EdgeKind.CITES and edge.target == "2016INSC1151" for edge in edges
    )


def test_section_is_part_of_its_act() -> None:
    assert Edge(
        "IND_central_2385_388", "IND_central_2385", EdgeKind.PART_OF
    ) in provision_edges(_provision())


def test_section_references_other_acts() -> None:
    assert Edge(
        "IND_central_2385_388", "Code of Civil Procedure, 1908", EdgeKind.REFERENCES
    ) in provision_edges(_provision())


def test_section_defines_its_terms_lowercased() -> None:
    assert Edge(
        "IND_central_2385_388", "executor", EdgeKind.DEFINES
    ) in provision_edges(_provision())


def test_section_has_its_legal_subject() -> None:
    assert Edge(
        "IND_central_2385_388", "family_law", EdgeKind.HAS_SUBJECT
    ) in provision_edges(_provision())


def test_edges_are_deduplicated() -> None:
    edges = judgment_edges(_chunk("see [1979] 3 S.C.R. 453 and [1979] 3 S.C.R. 453"))
    cites = [edge for edge in edges if edge.kind is EdgeKind.CITES]
    assert len(cites) == len(set(cites))


def test_group_by_kind_partitions_edges() -> None:
    grouped = group_by_kind(judgment_edges(_chunk()))
    assert EdgeKind.IN_COURT in grouped
    assert EdgeKind.DECIDED_BY in grouped


def test_group_by_kind_of_empty_input_is_empty() -> None:
    assert group_by_kind([]) == {}


def test_duplicate_raw_judge_names_collapse_to_one_edge() -> None:
    """`clean_judge_names` filters noise but does not dedupe; `judgment_edges`
    must, so a judge listed twice on one chunk still yields one DECIDED_BY."""
    chunk = JudgmentChunk.from_row(
        {
            "chunk_id": "2016_INSC_1151_001",
            "case_id": "2016_INSC_1151",
            "chunk_index": 1,
            "text": "the appeal is dismissed",
            "text_original": "the appeal is dismissed",
            "court_code": "SCI",
            "judges": ["T.S. THAKUR", "T.S. THAKUR"],
            "decision_date": "2016-12-15",
            "language_code": "en",
        },
        corpus_version="v2026.08",
    )
    decided_by = [
        edge for edge in judgment_edges(chunk) if edge.kind is EdgeKind.DECIDED_BY
    ]
    assert decided_by == [Edge("2016_INSC_1151", "T.S. THAKUR", EdgeKind.DECIDED_BY)]


def test_edges_from_sibling_chunks_of_one_case_need_a_further_dedup_pass() -> None:
    """`judgment_edges` dedupes within one chunk only. Two chunks of the same
    judgment (a real `sc_judgments_v1` shape: one `case_id`, many `chunk_id`s)
    both re-derive the same IN_COURT edge, so a caller combining edges across
    a whole judgment's chunks must dedupe again (e.g. via `set()`/`group_by_kind`
    plus a set) before writing to the graph."""
    chunk_one = _chunk()
    chunk_two = _chunk()
    combined = judgment_edges(chunk_one) + judgment_edges(chunk_two)
    in_court = [edge for edge in combined if edge.kind is EdgeKind.IN_COURT]
    assert len(in_court) == 2
    assert len(set(in_court)) == 1


# --- Task 2 whole-plan review: the act hint must survive into the edge --------


def test_interprets_edge_carries_the_act_hint_the_text_gave() -> None:
    """`extract_section_references` already produces the act hint, and it is
    the only deterministic signal that says WHICH act's section 302 a
    judgment means. Discarding it here is what forced every judgment-to-
    section link onto a bare-number placeholder."""
    edges = judgment_edges(
        _chunk("an offence under Section 138 of the Negotiable Instruments Act")
    )
    interprets = [edge for edge in edges if edge.kind is EdgeKind.INTERPRETS]
    assert interprets == [
        Edge(
            "2016_INSC_1151",
            "138",
            EdgeKind.INTERPRETS,
            hint="Negotiable Instruments Act",
        )
    ]


def test_interprets_edge_hint_is_none_when_the_text_names_no_act() -> None:
    edges = judgment_edges(_chunk("an offence under Section 302"))
    interprets = [edge for edge in edges if edge.kind is EdgeKind.INTERPRETS]
    assert interprets == [Edge("2016_INSC_1151", "302", EdgeKind.INTERPRETS)]


def test_edges_default_to_no_hint_so_existing_callers_are_unaffected() -> None:
    assert Edge("a", "b", EdgeKind.CITES).hint is None


def test_the_same_section_with_and_without_a_hint_are_distinct_edges() -> None:
    """One chunk can name 'Section 138' bare and 'Section 138 of the X Act';
    only the second can resolve, so they must not collapse into one edge."""
    edges = judgment_edges(
        _chunk("Section 138 applies. See Section 138 of the Negotiable Instruments Act.")
    )
    interprets = [edge for edge in edges if edge.kind is EdgeKind.INTERPRETS]
    assert len(interprets) == 2
    assert {edge.hint for edge in interprets} == {None, "Negotiable Instruments Act"}
