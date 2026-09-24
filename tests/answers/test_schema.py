import pytest
from pydantic import ValidationError

from openlaw.answers.schema import ANSWER_JSON_SCHEMA, Claim, DraftAnswer


def test_claim_requires_at_least_one_chunk_id() -> None:
    """An uncited claim is exactly what this design forbids."""
    with pytest.raises(ValidationError):
        Claim(text="landlords must give notice", chunk_ids=())


def test_claim_requires_non_empty_text() -> None:
    with pytest.raises(ValidationError):
        Claim(text="", chunk_ids=("c1",))


def test_claim_is_immutable() -> None:
    claim = Claim(text="t", chunk_ids=("c1",))
    with pytest.raises(ValidationError):
        claim.text = "changed"


def test_draft_answer_parses_model_json() -> None:
    payload = (
        '{"summary": "You may be entitled to notice.", '
        '"claims": [{"text": "Notice is required.", "chunk_ids": ["c1", "c2"]}], '
        '"sufficient": true}'
    )
    answer = DraftAnswer.from_json(payload)

    assert answer.sufficient is True
    assert answer.claims[0].chunk_ids == ("c1", "c2")


def test_draft_answer_parses_an_insufficient_response() -> None:
    answer = DraftAnswer.from_json(
        '{"summary": "I cannot answer this.", "claims": [], "sufficient": false}'
    )
    assert answer.sufficient is False
    assert answer.claims == ()


def test_draft_answer_rejects_malformed_json() -> None:
    with pytest.raises(ValueError):
        DraftAnswer.from_json("not json at all")


def test_draft_answer_rejects_json_missing_required_fields() -> None:
    with pytest.raises(ValueError):
        DraftAnswer.from_json('{"summary": "x"}')


def test_json_schema_forbids_additional_properties() -> None:
    """A strict schema keeps the validator's contract narrow."""
    assert ANSWER_JSON_SCHEMA["additionalProperties"] is False


def test_json_schema_requires_text_and_chunk_ids_on_every_claim() -> None:
    claim_schema = ANSWER_JSON_SCHEMA["properties"]["claims"]["items"]
    assert "chunk_ids" in claim_schema["required"]
    assert "text" in claim_schema["required"]


def test_json_schema_requires_the_sufficiency_flag() -> None:
    assert "sufficient" in ANSWER_JSON_SCHEMA["required"]
