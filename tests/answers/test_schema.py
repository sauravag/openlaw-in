import pytest
from pydantic import ValidationError

from openlaw.answers.schema import (
    ANSWER_JSON_SCHEMA,
    Claim,
    DocumentClaim,
    DraftAnswer,
)


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


# --- the second citation class: claims about the user's own document -------
#
# Plan 6 Task 7. A `DocumentClaim` says what the person's uploaded paperwork
# says. It cites `excerpt_ids`, never `chunk_ids`, and the two lists are kept
# apart at every layer: here in the contract the model must fill in, and in
# `openlaw.answers.validate`, which validates each against its own namespace.


def test_document_claim_requires_at_least_one_excerpt_id() -> None:
    """An uncited document claim is as unverifiable as an uncited legal one."""
    with pytest.raises(ValidationError):
        DocumentClaim(text="your notice is dated 4 March", excerpt_ids=())


def test_document_claim_requires_non_empty_text() -> None:
    with pytest.raises(ValidationError):
        DocumentClaim(text="", excerpt_ids=("doc:a:p1:0",))


def test_document_claim_is_immutable() -> None:
    claim = DocumentClaim(text="t", excerpt_ids=("doc:a:p1:0",))
    with pytest.raises(ValidationError):
        claim.text = "changed"


def test_document_claim_has_no_chunk_ids_field() -> None:
    """The two claim classes must not be field-compatible: a `DocumentClaim`
    can never be mistaken for a `Claim` by a caller that reads `chunk_ids`,
    and vice versa."""
    assert "chunk_ids" not in DocumentClaim.model_fields
    assert "excerpt_ids" not in Claim.model_fields


def test_draft_answer_defaults_document_claims_to_empty() -> None:
    """A model response predating this field parses unchanged — the
    no-document turn must behave exactly as it did before Task 7."""
    answer = DraftAnswer.from_json(
        '{"summary": "s", "claims": [], "sufficient": false}'
    )
    assert answer.document_claims == ()


def test_draft_answer_parses_document_claims() -> None:
    answer = DraftAnswer.from_json(
        '{"summary": "s", "claims": [], '
        '"document_claims": [{"text": "Your lease says 11 months.", '
        '"excerpt_ids": ["doc:abc:p1:0"]}], '
        '"sufficient": true}'
    )
    assert answer.document_claims[0].excerpt_ids == ("doc:abc:p1:0",)


def test_json_schema_requires_document_claims() -> None:
    """OpenRouter `strict` mode demands every property appear in `required`,
    so the model must always emit the key — an empty array on a turn with no
    uploaded document."""
    assert "document_claims" in ANSWER_JSON_SCHEMA["required"]


def test_json_schema_requires_text_and_excerpt_ids_on_every_document_claim() -> None:
    item = ANSWER_JSON_SCHEMA["properties"]["document_claims"]["items"]
    assert item["required"] == ["text", "excerpt_ids"]
    assert item["additionalProperties"] is False


def test_json_schema_keeps_the_two_citation_lists_apart() -> None:
    """Nothing in the contract invites the model to put a chunk_id in a
    document claim or an excerpt id in a legal claim."""
    claim_item = ANSWER_JSON_SCHEMA["properties"]["claims"]["items"]
    document_item = ANSWER_JSON_SCHEMA["properties"]["document_claims"]["items"]
    assert "excerpt_ids" not in claim_item["properties"]
    assert "chunk_ids" not in document_item["properties"]
