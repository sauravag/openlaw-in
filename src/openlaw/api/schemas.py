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

from openlaw.answers.escalation import Helpline
from openlaw.answers.escalation import crisis_response as crisis_text
from openlaw.answers.validate import ValidatedAnswer
from openlaw.api.jurisdictions import INDIAN_STATES_AND_UTS
from openlaw.language.pipeline import LocalisedAnswer
from openlaw.retrieval.context import ChunkKind

#: Languages the API will actually accept and serve (final review, H4).
#: Deliberately narrower than `openlaw.language.detect.SUPPORTED_LANGUAGES`
#: (13 codes): Plan 4's review gated launch to English and Hindi because only
#: Hindi has every glossary term verified by a native speaker
#: (`scripts/review_translations.py`) -- Marathi/Bengali/Tamil pin two terms
#: each, and Telugu/Gujarati/Kannada/Malayalam/Punjabi/Odia/Urdu/Assamese pin
#: none. Before this fix the gate existed ONLY in `web/lib/languages.ts`
#: (a UI affordance, easily bypassed): a plain `fetch` to `/ask` with
#: `{"language": "ta"}` returned Tamil legal prose translated with zero
#: pinned terminology, wearing a real government citation. This constant,
#: enforced by `AskRequest._language_must_be_supported` below, is what
#: actually stops that request -- `languages.ts`'s list stays as UX, not as
#: the control. `tests/api/test_schemas.py::
#: test_frontend_language_list_matches_the_launch_gate` keeps the two in
#: sync. Widen only alongside a glossary review, never alone.
LAUNCH_LANGUAGES: frozenset[str] = frozenset({"en", "hi"})

#: Shown when validation left nothing safe to render -- never the model's raw
#: summary. Matches the spirit (not the exact wording, this is API-facing
#: rather than an internal review sheet) of
#: `scripts/review_translations.py`'s fallback.
#:
#: Static EN/HI pair, not a live translation -- same rule as
#: `openlaw.api.refusal._REFUSAL_DETAIL` and
#: `openlaw.answers.escalation._CRISIS_RESPONSES_HI`: this string can be the
#: entire body of a Hindi user's unanswerable turn, and an error-path string
#: must not depend on the very translation pipeline that may have just
#: produced nothing (final review, M4). English is the fallback for any
#: `language` other than `"hi"` -- see `_static_text`.
_NO_VERIFIABLE_ANSWER: dict[str, str] = {
    "en": "We could not find a verifiable answer to this question in the corpus.",
    "hi": "हमें इस सवाल का कोई सत्यापित उत्तर इस कॉर्पस में नहीं मिला।",
}

#: Shown as `answer` on a crisis turn, ahead of the specific `crisis_response`
#: helpline text -- see `to_response`'s H3 fix below. A client that renders
#: only `answer` (the naive, most likely default) must still see something
#: true and reassuring, not the unrelated "could not find an answer" string.
#:
#: Static EN/HI pair (final review, H1) -- the same rule as
#: `_NO_VERIFIABLE_ANSWER` above. Before this fix, this one string was
#: hardcoded English and prepended to `answer` on EVERY crisis turn,
#: including Hindi: `localised.language` was already in hand one line above
#: (for `crisis_text`) but never consulted for this sentence, so a Hindi
#: domestic-violence crisis turn opened with an English sentence above
#: correctly-Hindi helpline text -- the single highest-stakes response this
#: system produces. See
#: `tests/api/test_routes.py::test_hindi_crisis_answer_contains_no_english_sentence`.
_HELP_IS_AVAILABLE: dict[str, str] = {
    "en": "Help is available right now -- see below.",
    "hi": "मदद अभी उपलब्ध है -- नीचे देखें।",
}


def _static_text(pairs: dict[str, str], language: str) -> str:
    """Return the static EN/HI string for `language`, falling back to English.

    Same rule as `openlaw.api.refusal.refusal_detail`: only English and Hindi
    have a hand-authored pair (Plan 4's launch gate), and there is never a
    live-translation fallback for these API-composed safety strings -- an
    error/no-answer path must not depend on the system that may have just
    failed to produce one.
    """
    return pairs.get(language, pairs["en"])


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
        """Reject any code outside the launch-gated language set.

        `LAUNCH_LANGUAGES` (module-level, above) is deliberately narrower
        than `openlaw.language.detect.SUPPORTED_LANGUAGES`: see that
        constant's docstring for why (final review, H4) -- this is the
        server-side enforcement of Plan 4's en/hi launch gate, not merely
        `web/lib/languages.ts`'s UI list.
        """
        if value is not None and value not in LAUNCH_LANGUAGES:
            raise ValueError(f"language must be one of {sorted(LAUNCH_LANGUAGES)}")
        return value


class SourceCard(BaseModel):
    """One verified citation of the LAW, rendered to the reader.

    Mirrors `openlaw.answers.validate.Source` field for field. It used to
    mirror only three of them -- `chunk_id`, `source_url`, `title` -- which
    meant `/ask` published no `kind`, no `case_id`, no `decision_date`, no
    `judges` and no `headnote`, so every judgment reached the browser as a
    null title above a raw tarball URL and `web/lib/sourceKind.ts` had to
    infer the citation's kind from its hostname. The server has always known
    all of it; this shape was simply throwing it away.

    Nothing is composed or inferred here: an absent field means the corpus
    does not record it (no judgment payload carries `title`, no provision
    payload carries `case_id`), and a renderer must fall back honestly
    rather than print a placeholder.

    There is deliberately no shape on this API for a citation of the user's
    own uploaded document. `openlaw.answers.validate.DocumentCitation` has
    no `source_url` and no counterpart here: the source list a reader treats
    as legal provenance can only ever be filled from the corpus.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_url: str
    kind: ChunkKind
    title: str | None = None
    case_id: str | None = None
    court_code: str | None = None
    decision_date: str | None = None
    judges: tuple[str, ...] = ()
    headnote: str | None = None


class AskResponse(BaseModel):
    """A verified answer with everything needed to render it responsibly.

    `helplines` is structured tap-to-call data (Task 6 fix round, C2) --
    always at least the NALSA number `footer` cites, plus category-specific
    numbers on a crisis turn. It exists so a client renders `tel:` links
    directly from this list rather than pattern-matching digits out of
    `footer`/`crisis_response` prose, which cannot survive localisation (a
    Hindi crisis message using Devanagari numerals would silently defeat a
    regex built for ASCII digits).
    """

    answer: str
    sources: list[SourceCard]
    language: str
    corpus_version: str
    footer: str
    crisis_response: str | None = None
    helplines: tuple[Helpline, ...] = ()
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
    #: Structured tap-to-call data (Task 6 fix round, C2) -- see
    #: `AskResponse.helplines`'s docstring; same rule here.
    helplines: tuple[Helpline, ...] = ()
    quota_reset_at: str | None = None
    signup_url: str | None = None


def _render_answer(answer: ValidatedAnswer, language: str) -> str:
    """Render the only content ever safe to show a user: claim text.

    Never reads `answer.unverified_summary` -- see the module docstring.
    When nothing survived validation, a fixed, non-model string in `language`
    is returned rather than an empty (or wrong-language, final review M4)
    answer. NOT called for a crisis turn -- see `to_response`, which branches
    before this function ever runs (H3).
    """
    if not answer.is_answerable or not answer.claims:
        return _static_text(_NO_VERIFIABLE_ANSWER, language)
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
        help_text = crisis_text(localised.crisis, localised.language)
        lead_in = _static_text(_HELP_IS_AVAILABLE, localised.language)
        return AskResponse(
            answer=f"{lead_in}\n\n{help_text}",
            sources=[],
            language=localised.language,
            corpus_version=corpus_version,
            footer=localised.footer,
            crisis_response=help_text,
            helplines=localised.helplines,
            is_answerable=True,
            quota_remaining=quota_remaining,
        )

    return AskResponse(
        answer=_render_answer(localised.answer, localised.language),
        sources=[
            SourceCard(
                chunk_id=source.chunk_id,
                source_url=source.source_url,
                kind=source.kind,
                title=source.title,
                case_id=source.case_id,
                court_code=source.court_code,
                decision_date=source.decision_date,
                judges=source.judges,
                headnote=source.headnote,
            )
            for source in localised.answer.sources
        ],
        language=localised.language,
        corpus_version=corpus_version,
        footer=localised.footer,
        crisis_response=None,
        helplines=localised.helplines,
        is_answerable=localised.answer.is_answerable,
        quota_remaining=quota_remaining,
    )
