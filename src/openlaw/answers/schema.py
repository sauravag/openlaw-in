"""The structured answer contract.

The generator returns JSON — a list of claims, each carrying the `chunk_id`s that
support it — rather than prose with inline citations. This makes validation a set
membership check instead of a parsing problem: unsupported claims can be dropped
individually, and no regex can mis-read a citation.
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Claim(BaseModel):
    """One factual legal assertion and the chunks that support it."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    chunk_ids: tuple[str, ...] = Field(min_length=1)


class DraftAnswer(BaseModel):
    """The model's unvalidated response."""

    model_config = ConfigDict(frozen=True)

    summary: str
    claims: tuple[Claim, ...] = ()
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
        "sufficient": {
            "type": "boolean",
            "description": "False when the provided sources cannot support an answer.",
        },
    },
    "required": ["summary", "claims", "sufficient"],
    "additionalProperties": False,
}
