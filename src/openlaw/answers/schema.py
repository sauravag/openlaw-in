"""The structured answer contract.

The generator returns JSON — a list of claims, each carrying the `chunk_id`s that
support it — rather than prose with inline citations. This makes validation a set
membership check instead of a parsing problem: unsupported claims can be dropped
individually, and no regex can mis-read a citation.

**Two claim lists, because there are two kinds of statement and only one of
them is about the law.** `claims` holds legal assertions and cites
`chunk_id`s from the corpus. `document_claims` holds statements about a
document the person uploaded ("your notice is dated 4 March") and cites
`excerpt_id`s from that document. They are separate lists of separate,
field-incompatible types: a `Claim` has no `excerpt_ids` and a
`DocumentClaim` has no `chunk_ids`, so neither can be read as the other by a
caller, and `openlaw.answers.validate` checks each against its own
namespace's id set. Asking the model to keep them apart is not the control
— the validator dropping anything that mixes them is (see
`SYSTEM_PROMPT` rule 9, and `validate`'s rules 2 and 4).
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Claim(BaseModel):
    """One factual legal assertion and the chunks that support it."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    chunk_ids: tuple[str, ...] = Field(min_length=1)


class DocumentClaim(BaseModel):
    """One statement about the user's own uploaded document.

    Deliberately NOT a `Claim` and deliberately not a subclass of one: this
    says what a person's lease or notice says, never what the law says, and
    it cites `excerpt_id`s (`openlaw.documents.DOCUMENT_EXCERPT_PREFIX`)
    rather than corpus `chunk_id`s. The two types share no field name, so no
    caller reading `chunk_ids` can ever be handed one of these by mistake.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    excerpt_ids: tuple[str, ...] = Field(min_length=1)


class DraftAnswer(BaseModel):
    """The model's unvalidated response.

    `document_claims` defaults to empty, so a response from a turn with no
    uploaded document — every turn today — parses and validates exactly as
    it did before the field existed.
    """

    model_config = ConfigDict(frozen=True)

    summary: str
    claims: tuple[Claim, ...] = ()
    document_claims: tuple[DocumentClaim, ...] = ()
    sufficient: bool

    @classmethod
    def from_json(cls, payload: str) -> "DraftAnswer":
        """Parse `payload`, raising ValueError when malformed or invalid."""
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"generator returned malformed JSON: {error}") from error
        try:
            return cls.model_validate(data)
        except ValidationError as error:
            raise ValueError(f"generator JSON failed validation: {error}") from error


ANSWER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Plain-language answer for a non-lawyer.",
        },
        "claims": {
            "type": "array",
            "description": "Each legal assertion with its supporting chunk ids.",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["text", "chunk_ids"],
                "additionalProperties": False,
            },
        },
        "document_claims": {
            "type": "array",
            "description": (
                "Each statement about the user's uploaded document, with the "
                "excerpt ids it came from. Never legal assertions."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "excerpt_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["text", "excerpt_ids"],
                "additionalProperties": False,
            },
        },
        "sufficient": {
            "type": "boolean",
            "description": "False when the provided sources cannot support an answer.",
        },
    },
    # `document_claims` is listed here — an empty array on a turn with no
    # uploaded document — because OpenRouter's `strict` structured-output
    # mode requires every declared property to appear in `required`.
    "required": ["summary", "claims", "document_claims", "sufficient"],
    "additionalProperties": False,
}
