"""Tests for answering the person's actual goal, or saying it is not covered.

Retrieval quality item 4. Asked how to move a stuck divorce case faster, the
generator summarised the nearest retrieved material (transfer petitions)
without saying that nothing in front of it addressed speeding a case up.

The citation contract is unchanged: every legal claim still cites chunk ids
shown this turn, and the validator still drops anything else. A sentence
like "None of the sources covers X" has no chunk id to cite, so it can never
survive as a claim. The model instead sets a boolean, `goal_covered`, and
the service renders a fixed, pre-written notice (see
`tests/api/test_schemas.py`), so no unverified model prose reaches the
person on this path either.
"""

import json

from openlaw.answers.generate import generate_turn
from openlaw.answers.models import ModelRegistry, StubAdapter
from openlaw.answers.prompts import SYSTEM_PROMPT
from openlaw.answers.schema import ANSWER_JSON_SCHEMA, Claim, DraftAnswer
from openlaw.answers.validate import Source, ValidatedAnswer, validate_answer
from openlaw.language.glossary import Glossary
from openlaw.language.translate import translate_answer
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk


def _context() -> RetrievalContext:
    return RetrievalContext(
        query="my divorce case is stuck, how do I speed it up?",
        chunks=(
            RetrievedChunk(
                chunk_id="transfer",
                text="The Supreme Court may transfer a case from one court to another.",
                source_url="https://indiacode.nic.in/transfer",
                score=0.9,
                kind=ChunkKind.PROVISION,
            ),
        ),
    )


def _draft(goal_covered: bool, chunk_ids: tuple[str, ...] = ("transfer",)) -> DraftAnswer:
    return DraftAnswer(
        summary="s",
        claims=(Claim(text="A case can be transferred.", chunk_ids=chunk_ids),),
        sufficient=True,
        goal_covered=goal_covered,
    )


# --- the prompt --------------------------------------------------------------


def test_the_prompt_tells_the_model_to_answer_the_persons_goal_first() -> None:
    assert "what the person is trying to achieve" in SYSTEM_PROMPT
    assert "lead with the cited law that most directly serves that goal" in SYSTEM_PROMPT


def test_the_prompt_tells_the_model_to_flag_an_uncovered_goal_not_drift() -> None:
    assert 'set "goal_covered" to false' in SYSTEM_PROMPT
    assert "adjacent topics" in SYSTEM_PROMPT
    # The model must not try to say it in an (uncitable) claim.
    assert "never write a claim to say so" in SYSTEM_PROMPT


def test_the_citation_contract_rules_are_unchanged() -> None:
    assert (
        "1. Every legal assertion you make must be supported by at least one chunk_id from "
        "the excerpts provided in this turn. Never cite a chunk_id that is not listed."
    ) in SYSTEM_PROMPT
    assert (
        "2. Never rely on legal knowledge that is not in the provided excerpts."
    ) in SYSTEM_PROMPT


def test_the_prompt_numbers_the_new_rules_twelve_and_thirteen() -> None:
    assert "\n12. " in SYSTEM_PROMPT
    assert "\n13. " in SYSTEM_PROMPT


# --- the schema --------------------------------------------------------------


def test_the_answer_schema_requires_a_goal_covered_boolean() -> None:
    assert ANSWER_JSON_SCHEMA["properties"]["goal_covered"]["type"] == "boolean"
    # OpenRouter strict mode: every declared property must be required.
    assert "goal_covered" in ANSWER_JSON_SCHEMA["required"]


def test_a_draft_without_goal_covered_still_parses_as_covered() -> None:
    draft = DraftAnswer.from_json(
        json.dumps({"summary": "s", "claims": [], "sufficient": False})
    )

    assert draft.goal_covered is True


# --- validation --------------------------------------------------------------


def test_an_uncovered_goal_survives_validation_as_a_flag() -> None:
    answer = validate_answer(_draft(goal_covered=False), _context())

    assert answer.goal_not_covered is True
    assert answer.is_answerable is True
    assert [claim.text for claim in answer.claims] == ["A case can be transferred."]


def test_a_covered_goal_sets_no_flag() -> None:
    assert validate_answer(_draft(goal_covered=True), _context()).goal_not_covered is False


def test_the_flag_never_rescues_an_uncited_claim() -> None:
    answer = validate_answer(_draft(goal_covered=False, chunk_ids=("invented",)), _context())

    assert answer.claims == ()
    assert answer.is_answerable is False


# --- generation and translation carry it through -----------------------------


def test_generation_carries_the_flag_onto_the_validated_answer() -> None:
    reply = json.dumps(
        {
            "summary": "s",
            "claims": [{"text": "A case can be transferred.", "chunk_ids": ["transfer"]}],
            "document_claims": [],
            "sufficient": True,
            "goal_covered": False,
        }
    )

    result = generate_turn("q", _context(), StubAdapter([reply]), ModelRegistry(), "footer")

    assert result.answer.goal_not_covered is True
    assert result.answer.is_answerable is True


def test_translation_keeps_the_flag() -> None:
    answer = ValidatedAnswer(
        unverified_summary="s",
        claims=(Claim(text="A case can be transferred.", chunk_ids=("transfer",)),),
        sources=(
            Source(chunk_id="transfer", source_url="https://indiacode.nic.in/t", kind=ChunkKind.PROVISION),
        ),
        is_answerable=True,
        goal_not_covered=True,
    )
    translated = json.dumps(
        {"summary": "अ", "claims": [{"id": 0, "text": "मामला स्थानांतरित हो सकता है।"}]},
        ensure_ascii=False,
    )

    result = translate_answer(
        answer, "hi", StubAdapter([translated]), ModelRegistry(), Glossary(terms=())
    )

    assert result.claims[0].text == "मामला स्थानांतरित हो सकता है।"
    assert result.goal_not_covered is True
