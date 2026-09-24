"""Node payload builders — pure functions from corpus records to Neo4j rows.

Each builder returns a plain dict (or a tuple of them) suitable for an `UNWIND`
batch write. Node key properties match the uniqueness constraints declared in
`openlaw.graph.schema`, so the Task 5 loader can `MERGE` on them without
creating duplicate nodes across reruns.

All builders are pure: they read their input record and return brand-new dicts,
never mutating the record or anything nested inside it.
"""

from typing import Any

from openlaw.corpus.schema import JudgmentChunk, Provision


def judgment_node(chunk: JudgmentChunk) -> dict[str, Any]:
    """Return the `Judgment` node for `chunk`'s case.

    Keyed on `case_id` per the `judgment_id` constraint. `join_key` and
    `court_code` back the `judgment_join_key` and `judgment_court` indexes;
    `decision_year` backs `judgment_year`.
    """
    return {
        "case_id": chunk.case_id,
        "join_key": chunk.join_key,
        "court_code": chunk.court_code,
        "decision_date": (
            chunk.decision_date.isoformat() if chunk.decision_date else None
        ),
        "decision_year": chunk.decision_date.year if chunk.decision_date else None,
        "corpus_version": chunk.corpus_version,
    }


def judge_nodes(chunk: JudgmentChunk) -> tuple[dict[str, Any], ...]:
    """Return one `Judge` node per cleaned judge name on `chunk`.

    `chunk.judges` is already filtered by `clean_judge_names` in
    `JudgmentChunk.from_row`, so parser noise like "vide" never reaches here.
    """
    return tuple({"name": name} for name in chunk.judges)


def court_node(court_code: str) -> dict[str, Any]:
    """Return the `Court` node for `court_code`. Keyed on `code`."""
    return {"code": court_code}


def act_node(provision: Provision) -> dict[str, Any]:
    """Return the `Act` node that `provision` belongs to. Keyed on `act_id`."""
    return {
        "act_id": provision.act_id,
        "title": provision.title,
        "jurisdiction": provision.jurisdiction,
        "in_force": provision.in_force,
        "corpus_version": provision.corpus_version,
    }


def section_node(provision: Provision) -> dict[str, Any]:
    """Return the `Section` node for `provision`. Keyed on `chunk_id`.

    Carries `act_id` and `section_number` for the `section_act` and
    `section_number` indexes.
    """
    return {
        "chunk_id": provision.chunk_id,
        "act_id": provision.act_id,
        "section_number": provision.section_number,
        "in_force": provision.in_force,
        "source_url": provision.source_url,
        "corpus_version": provision.corpus_version,
    }


def term_nodes(provision: Provision) -> tuple[dict[str, Any], ...]:
    """Return one `DefinedTerm` node per term defined by `provision`.

    Terms are lowercased so "Executor" and "executor" collapse onto the same
    `DefinedTerm` node under the `term_text` constraint.
    """
    return tuple(
        {"term": term.strip().lower()}
        for term in provision.defined_terms
        if term.strip()
    )


def subject_nodes(provision: Provision) -> tuple[dict[str, Any], ...]:
    """Return one `LegalSubject` node per subject tag on `provision`.

    Keyed on `name` per the `subject_name` constraint.
    """
    return tuple({"name": subject} for subject in provision.legal_subjects if subject)
