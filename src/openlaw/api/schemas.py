"""API request and response shapes.

`crisis_response` is a separate field rather than folded into the answer text, so
the interface can render it above the legal content. A helpline printed below a
statutory explanation has already failed the person reading it.

**The rendered `answer` is built from `ValidatedAnswer.claims`, never from
`ValidatedAnswer.unverified_summary`.** That field is explicitly the model's
unchecked prose -- see `openlaw.answers.validate`'s module docstring: a model
can keep its `claims` well-cited while hiding a hallucination in free-form
summary text, and there is no citation check that ever runs against it. It is
`None` whenever the answer is not answerable, precisely so a careless render
fails loud (empty string / attribute error) rather than leaking unverified
text. `_render_answer` below mirrors `scripts/review_translations.py`'s
`_render_claims` -- the only other place in the codebase that turns a
`ValidatedAnswer` into displayable text, and for the identical reason.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openlaw.answers.escalation import crisis_response as crisis_text
from openlaw.answers.validate import ValidatedAnswer
from openlaw.language.pipeline import LocalisedAnswer

#: Shown when validation left nothing safe to render -- never the model's raw
#: summary. Matches the spirit (not the exact wording, this is API-facing
#: rather than an internal review sheet) of
#: `scripts/review_translations.py`'s fallback.
_NO_VERIFIABLE_ANSWER = (
    "We could not find a verifiable answer to this question in the corpus."
)


class AskRequest(BaseModel):
    """A question from a user.

    `state` is used only to route the escalation footer to the right State
    Legal Services Authority (see `openlaw.answers.escalation.escalation_footer`)
    and is forwarded to `answer_fn` unchanged. It is deliberately NOT turned
    into a retrieval `court_codes` filter anywhere in this module -- see
    `routes.ask`'s docstring for why that decision belongs to the retrieval
    wiring layer, not the API layer, and why leaving it unwired here is the
    safe default rather than an oversight.
    """

    question: str = Field(min_length=1, max_length=2000)
    language: str | None = None
    state: str | None = None

    @field_validator("question")
    @classmethod
    def _question_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only questions.

        `min_length=1` alone does not catch this: a string of three spaces has
        length 3. Without this check a blank question would sail through
        validation and reach `answer_fn` as an empty-content turn.
        """
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value


class SourceCard(BaseModel):
    """One verified citation rendered to the reader."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_url: str
    title: str | None = None


class AskResponse(BaseModel):
    """A verified answer with everything needed to render it responsibly."""

    answer: str
    sources: list[SourceCard]
    language: str
    corpus_version: str
    footer: str
    crisis_response: str | None = None
    is_answerable: bool
    quota_remaining: int


class QuotaExceeded(BaseModel):
    """Returned with HTTP 429 when the allowance is spent.

    A quota refusal must never be a bare "limit reached" dead end: someone
    can exhaust their monthly or anonymous allowance in the middle of a
    genuine legal emergency. `escalation_footer` (routing to free legal aid)
    is therefore always present, and `crisis_response` is populated whenever
    the free, no-model-call regex layer
    (`openlaw.answers.escalation.detect_crisis`) finds one in the question
    that was about to be refused. This is deliberately the regex layer only,
    never the CLASSIFIER-role second layer -- that costs a real model call,
    which a request that is being refused for exceeding its quota must not
    spend.
    """

    model_config = ConfigDict(frozen=True)

    detail: str
    quota_remaining: int = 0
    signup_required: bool = False
    escalation_footer: str
    crisis_response: str | None = None


def _render_answer(answer: ValidatedAnswer) -> str:
    """Render the only content ever safe to show a user: claim text.

    Never reads `answer.unverified_summary` -- see the module docstring.
    When nothing survived validation (or the answer was never answerable --
    e.g. a crisis short-circuit, which carries its help resources on
    `crisis_response`/`footer` instead), a fixed, non-model string is
    returned rather than an empty answer.
    """
    if not answer.is_answerable or not answer.claims:
        return _NO_VERIFIABLE_ANSWER
    return "\n\n".join(claim.text for claim in answer.claims)


def to_response(
    localised: LocalisedAnswer, corpus_version: str, quota_remaining: int
) -> AskResponse:
    """Map a localised answer onto the API response."""
    return AskResponse(
        answer=_render_answer(localised.answer),
        sources=[
            SourceCard(
                chunk_id=source.chunk_id,
                source_url=source.source_url,
                title=source.title,
            )
            for source in localised.answer.sources
        ],
        language=localised.language,
        corpus_version=corpus_version,
        footer=localised.footer,
        crisis_response=crisis_text(localised.crisis) if localised.crisis else None,
        is_answerable=localised.answer.is_answerable,
        quota_remaining=quota_remaining,
    )
