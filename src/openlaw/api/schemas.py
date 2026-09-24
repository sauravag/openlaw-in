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
from openlaw.api.jurisdictions import INDIAN_STATES_AND_UTS
from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.pipeline import LocalisedAnswer

#: Shown when validation left nothing safe to render -- never the model's raw
#: summary. Matches the spirit (not the exact wording, this is API-facing
#: rather than an internal review sheet) of
#: `scripts/review_translations.py`'s fallback.
_NO_VERIFIABLE_ANSWER = (
    "We could not find a verifiable answer to this question in the corpus."
)

#: Shown as `answer` on a crisis turn, ahead of the specific `crisis_response`
#: helpline text -- see `to_response`'s H3 fix below. A client that renders
#: only `answer` (the naive, most likely default) must still see something
#: true and reassuring, not the unrelated "could not find an answer" string.
_HELP_IS_AVAILABLE = "Help is available right now -- see below."


class AskRequest(BaseModel):
    """A question from a user.

    `state` is used only to route the escalation footer to the right State
    Legal Services Authority (see `openlaw.answers.escalation.escalation_footer`)
    and is forwarded to `answer_fn` unchanged. It is deliberately NOT turned
    into a retrieval `court_codes` filter anywhere in this module -- see
    `routes.ask`'s docstring for why that decision belongs to the retrieval
    wiring layer, not the API layer, and why leaving it unwired here is the
    safe default rather than an oversight.

    `state` and `language` are both allowlisted (Task 4 review, H2): `state`
    is interpolated verbatim into `escalation_footer` -- the one sentence
    that tells a citizen who to call for help -- and an unvalidated value let
    a proof-of-concept request land a `<script>` tag and a phishing URL
    inside a real response body. `language` reaches a model prompt via
    `answer_fn`/the translation pipeline. Neither is free text; both are
    rejected at this boundary rather than trusted downstream.
    """

    question: str = Field(min_length=1, max_length=2000)
    language: str | None = Field(default=None, max_length=8)
    state: str | None = Field(default=None, max_length=64)

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

    @field_validator("state")
    @classmethod
    def _state_must_be_a_real_jurisdiction(cls, value: str | None) -> str | None:
        """Reject anything not a real Indian state or union territory.

        See `openlaw.api.jurisdictions` and the class docstring: `state`
        reaches `escalation_footer` verbatim, so free text here is a direct
        injection vector into a safety-critical sentence.
        """
        if value is not None and value not in INDIAN_STATES_AND_UTS:
            raise ValueError(
                "state must be a recognised Indian state or union territory"
            )
        return value

    @field_validator("language")
    @classmethod
    def _language_must_be_supported(cls, value: str | None) -> str | None:
        """Reject any code the pipeline does not actually support.

        `SUPPORTED_LANGUAGES` (`openlaw.language.detect`) is the same
        allowlist `answer_in_language`'s glossary/translation machinery is
        built against -- reusing it here rather than a second, drifting copy.
        """
        if value is not None and value not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"language must be one of {sorted(SUPPORTED_LANGUAGES)}"
            )
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
    """Returned with HTTP 429 -- both for an exhausted quota AND a tripped
    short-burst rate limit (`openlaw.api.routes`). One shape for both, since
    a caller-facing refusal needs the same things regardless of which limit
    fired.

    A refusal must never be a bare "limit reached" dead end: someone can hit
    either limit in the middle of a genuine legal emergency. `escalation_footer`
    (routing to free legal aid) is therefore always present, and
    `crisis_response` is populated by `openlaw.api.refusal.crisis_extras` --
    see that module's docstring for why this still stops short of the paid
    CLASSIFIER-role check the main answer path uses. `quota_reset_at` (ISO
    date, the reset for the *monthly/anonymous allowance*) and `signup_url`
    tell the caller what to do next; `quota_reset_at` is `None` for a
    rate-limit refusal, where "reset" is a `Retry-After` header (seconds
    away), not a calendar date.
    """

    model_config = ConfigDict(frozen=True)

    detail: str
    quota_remaining: int = 0
    signup_required: bool = False
    escalation_footer: str
    crisis_response: str | None = None
    quota_reset_at: str | None = None
    signup_url: str | None = None


def _render_answer(answer: ValidatedAnswer) -> str:
    """Render the only content ever safe to show a user: claim text.

    Never reads `answer.unverified_summary` -- see the module docstring.
    When nothing survived validation, a fixed, non-model string is returned
    rather than an empty answer. NOT called for a crisis turn -- see
    `to_response`, which branches before this function ever runs (H3).
    """
    if not answer.is_answerable or not answer.claims:
        return _NO_VERIFIABLE_ANSWER
    return "\n\n".join(claim.text for claim in answer.claims)


def to_response(
    localised: LocalisedAnswer, corpus_version: str, quota_remaining: int
) -> AskResponse:
    """Map a localised answer onto the API response.

    **A crisis turn is never rendered as unanswerable (Task 4 review, H3).**
    `localised.answer` on a crisis short-circuit is always the fixed,
    generation-free `_UNANSWERABLE` from `openlaw.language.pipeline`
    (`is_answerable=False`, no claims) -- correct internally (no legal claim
    was ever generated or validated), but wrong to surface verbatim as
    `answer`: the most safety-critical response this system produces would
    lead with "We could not find a verifiable answer", a discouraging
    falsehood, on the one turn where real help IS being given. When
    `localised.crisis` is set, `answer` instead carries `_HELP_IS_AVAILABLE`
    plus the same helpline text as `crisis_response` (a client rendering
    `answer` alone -- the naive, most likely default -- still sees the actual
    help), and `is_answerable` is reported `True`: this IS the complete,
    correct response for this turn, even though it contains no cited legal
    claim.
    """
    if localised.crisis is not None:
        help_text = crisis_text(localised.crisis)
        return AskResponse(
            answer=f"{_HELP_IS_AVAILABLE}\n\n{help_text}",
            sources=[],
            language=localised.language,
            corpus_version=corpus_version,
            footer=localised.footer,
            crisis_response=help_text,
            is_answerable=True,
            quota_remaining=quota_remaining,
        )

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
        crisis_response=None,
        is_answerable=localised.answer.is_answerable,
        quota_remaining=quota_remaining,
    )
