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

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openlaw.answers.escalation import Helpline
from openlaw.answers.schema import DocumentClaim
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


#: Heading rendered above `AskResponse.document_answer` (Plan 6 Task 15).
#: `openlaw.answers.validate.ValidatedAnswer` states the obligation --
#: "present these as what your document says, visibly apart from the legal
#: claims, never under a heading that reads as law" -- and until this task
#: nothing rendered `document_claims` at all, so the obligation was carried
#: by a docstring and nothing else. Two things make it real here: document
#: claims go out in their OWN field, never merged into `answer`, and that
#: field opens with this heading, so even a client that naively concatenates
#: the two still shows the reader where the law stops and their own paperwork
#: starts. Static EN/HI, the same rule as every other API-composed safety
#: string in this module -- a renderer must not depend on the translation
#: pipeline to tell a person which half of an answer is law.
_FROM_YOUR_DOCUMENT: dict[str, str] = {
    "en": "From the document you uploaded (this is what your paper says, not the law):",
    "hi": "आपने जो दस्तावेज़ भेजा, उसमें से (यह आपके कागज़ की बात है, क़ानून नहीं):",
}

#: Absolute cap on how many `document_ids` one request body may carry. This
#: is a parse bound, not the product limit: the product limit is
#: `Settings.max_documents_per_conversation`, which is configurable at
#: runtime and therefore cannot live in a pydantic field constraint, and which
#: `openlaw.api.routes` enforces with a help-carrying `too_many_documents`
#: refusal. Both exist on purpose -- a static bound stops an attacker posting
#: a hundred thousand ids and making the server validate every one of them,
#: and the configurable one is what a deployment actually tunes.
MAX_DOCUMENT_IDS = 32


def validated_uuid(value: str, field: str) -> str:
    """Return `value` if it is a UUID string, else raise.

    `conversation_id` and `document_id` are interpolated into database
    lookups and echoed back in responses, and arrive from the same untrusted
    place `state` and `language` do. Parsing them as UUIDs at the boundary is
    the same rule those two already follow (see `AskRequest`'s docstring):
    anything that is not one is rejected here rather than trusted downstream.
    Returns the ORIGINAL string, not `str(uuid.UUID(value))` -- stored ids are
    whatever `uuid.uuid4()` produced, and silently re-canonicalising a
    caller's value would make a lookup succeed or fail on formatting.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError(f"{field} must be a UUID") from error
    return value


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
    #: The conversation this turn continues, or `None` to start a new one.
    #: A conversation the caller does not own is a 404, never a 403 -- see
    #: `openlaw.api.refusal`'s `"not_found"` entry.
    conversation_id: str | None = Field(default=None, max_length=36)
    #: Documents already uploaded to `conversation_id` whose text this turn
    #: may quote. Ids only: this request never carries document bytes, so the
    #: upload path's size, type and page caps cannot be bypassed through
    #: `/ask`.
    document_ids: tuple[str, ...] = Field(default=(), max_length=MAX_DOCUMENT_IDS)

    @field_validator("conversation_id")
    @classmethod
    def _conversation_id_must_be_a_uuid(cls, value: str | None) -> str | None:
        """Reject a non-UUID conversation id at the boundary."""
        if value is None:
            return None
        return validated_uuid(value, "conversation_id")

    @field_validator("document_ids")
    @classmethod
    def _document_ids_must_be_uuids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject any non-UUID document id, and any duplicate.

        Duplicates are rejected rather than silently deduplicated: the same
        id twice would be audited twice, cost its excerpts twice in the
        prompt budget, and tell the caller nothing about why their answer
        looked odd.
        """
        for document_id in value:
            validated_uuid(document_id, "document_ids")
        if len(set(value)) != len(value):
            raise ValueError("document_ids must not contain duplicates")
        return value

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

    `judges` and `headnote` are on the wire but are not rendered by
    `web/components/SourceList.tsx` today, and that is a decision rather
    than an oversight (security review LOW-2): a corpus headnote is English
    prose, so printing it unconditionally would put English text inside a
    Hindi answer -- the same defect class as the translated-path bypass this
    task's review found. Rendering either one belongs to the task that
    designs the citation display, together with how it handles a
    non-English reader.

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


class DocumentCitationCard(BaseModel):
    """One pointer into the reader's OWN uploaded document.

    Mirrors `openlaw.answers.validate.DocumentCitation` field for field, and
    inherits the field that type most pointedly does not have: there is no
    `source_url` here, not optional, not `None`, absent. A lease has no
    government URL and must not be able to acquire one by passing through an
    API shape -- see `DocumentCitation`'s docstring and `SourceCard`'s closing
    paragraph. `filename` and `page_number` are here so a person can find the
    passage in their own paperwork, which is the only verification available
    for a statement about a document only they hold.

    `filename` is attacker-controlled text that reaches a browser. It is
    sanitised once, at the upload boundary (`openlaw.api.artifacts`, via
    `openlaw.documents.fence.sanitise`), so what is stored is already safe and
    this type never has to re-clean it on the way out.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    excerpt_id: str
    document_id: str
    filename: str
    page_number: int


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
    #: The conversation this turn belongs to -- minted on a first turn, so a
    #: client can continue the thread without a second round trip.
    conversation_id: str
    #: This turn's 0-based position in `conversation_id`, straight from
    #: `LocalisedAnswer.turn_index`.
    turn_index: int = 0
    #: What the reader's OWN uploaded document says, rendered SEPARATELY from
    #: `answer` and never merged into it (Plan 6 Task 15). `None` when no
    #: document contributed anything to this turn. See `_FROM_YOUR_DOCUMENT`
    #: for why separation alone was not judged enough, and
    #: `_render_document_answer` for what it is built from.
    document_answer: str | None = None
    #: The excerpts `document_answer` points at -- filename and page, no URL.
    document_citations: tuple[DocumentCitationCard, ...] = ()


class DocumentSummary(BaseModel):
    """What a successful upload returns.

    `expires_at` and `retention_notice` are the honest half of the anonymous
    path: a cookie-keyed identity cannot reliably delete anything later, so an
    anonymous upload is purged after `Settings.anonymous_upload_ttl_hours` and
    the person is TOLD so, with the one action that changes it (signing in)
    named in the same sentence. An authenticated upload carries
    `expires_at=None` and a notice that says the file stays until they delete
    it -- never an empty string, because a blank notice renders as no notice
    at all and silence about retention is the thing this field exists to
    prevent.

    `quota_remaining` is here for the same reason it is on `AskResponse`: an
    upload now costs more than one unit (see `openlaw.documents
    .PAGES_PER_QUOTA_UNIT`), so a client that only ever learned the balance
    from `/ask` would show a figure that silently jumped.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    filename: str
    page_count: int
    conversation_id: str
    expires_at: str | None = None
    retention_notice: str
    quota_remaining: int


class ConversationTurnCard(BaseModel):
    """One past turn, as it is replayed after a page reload.

    Built from `openlaw.answers.history.HistoryTurn` -- validated claims and
    the person's own question. There is deliberately no field here that could
    hold `unverified_summary`: `HistoryTurn` does not have one, nothing in
    `openlaw.db.conversations` stores one, and this shape could not render one
    if a future caller tried.

    `answer` and `document_answer` are split exactly as they are on
    `AskResponse`, for the identical reason -- a reloaded transcript must not
    be the one place where a statement about someone's lease reads as law.
    """

    model_config = ConfigDict(frozen=True)

    turn_index: int
    question: str
    answer: str
    document_answer: str | None = None
    language: str


class ConversationDocumentCard(BaseModel):
    """One document still attached to a conversation, after a reload.

    **Metadata only, and that is enforced by the type, not by the caller.**
    There is no field here that could carry document text, a decrypted
    excerpt, a ciphertext blob, or a URL -- the same reason
    `DocumentCitationCard` has no `source_url`. A reader needs to recognise
    their own file and withdraw it; neither needs its content back.

    Built from `openlaw.documents.store.StoredDocument`, which also cannot
    hold content. `retention_notice` is the same sentence the upload response
    carried (`openlaw.api.artifacts._retention_notice`) so a restored list
    repeats the promise that was made rather than paraphrasing it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    filename: str
    page_count: int
    expires_at: str | None = None
    retention_notice: str


class ConversationView(BaseModel):
    """A conversation's turns and its still-attached documents, for rendering
    after a reload.

    `documents` exists because an authenticated upload is retained *until the
    owner deletes it*, and per-document deletion is the only mechanism behind
    that promise. Before this field the list came back empty on reload, so the
    reader could delete the whole conversation but not one file out of three
    -- see `openlaw.documents.store.list_conversation_documents`.
    """

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    turns: tuple[ConversationTurnCard, ...] = ()
    documents: tuple[ConversationDocumentCard, ...] = ()


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


def render_document_claims(
    claims: tuple[DocumentClaim, ...], language: str
) -> str | None:
    """Render what the reader's own document says, or `None`.

    The ONE renderer for document claims, used both for a live turn
    (`to_response`) and for a reloaded transcript
    (`openlaw.api.artifacts.read_conversation`). One function rather than two,
    because the rule it enforces -- document claims are visibly apart from
    legal ones, under a heading that does not read as law -- is exactly the
    kind of property this project has lost to a second code path before.

    Built from claim text only, never from `unverified_summary`: a
    `DocumentClaim` has no such field, and neither does `HistoryTurn`, so this
    function could not reach one if it tried. Returns `None`, not an empty
    string, when no document claim survived -- `None` is a field a client can
    branch on, while `""` renders as a heading above nothing.

    The heading (`_FROM_YOUR_DOCUMENT`) is part of the returned text rather
    than a separate field so the distinction cannot be lost by a client that
    renders this value alone, or concatenates it onto `answer`.
    """
    if not claims:
        return None
    heading = _static_text(_FROM_YOUR_DOCUMENT, language)
    body = "\n\n".join(claim.text for claim in claims)
    return f"{heading}\n\n{body}"


def _document_citation_cards(
    localised: LocalisedAnswer,
) -> tuple[DocumentCitationCard, ...]:
    """Map this turn's document citations onto their API shape."""
    return tuple(
        DocumentCitationCard(
            excerpt_id=citation.excerpt_id,
            document_id=citation.document_id,
            filename=citation.filename,
            page_number=citation.page_number,
        )
        for citation in localised.document_citations
    )


def _crisis_only_response(
    localised: LocalisedAnswer,
    corpus_version: str,
    quota_remaining: int,
    conversation_id: str,
) -> AskResponse:
    """Render a question-derived crisis short-circuit (Task 4 review, H3).

    `answer` leads with `_HELP_IS_AVAILABLE` and then the helpline text, and
    `is_answerable` is reported True -- see `to_response`'s docstring for why
    this turn is complete and correct despite carrying no cited legal claim.

    `document_answer` and `document_citations` are spelled out as empty rather
    than left to their defaults: this short-circuit happens before generation,
    so no document claim was ever produced even when a document was attached,
    and this is the one branch where "the turn carries no legal answer" and
    "the turn carries no document answer" have to be true together.
    """
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
        conversation_id=conversation_id,
        turn_index=localised.turn_index,
        document_answer=None,
        document_citations=(),
    )


def to_response(
    localised: LocalisedAnswer,
    corpus_version: str,
    quota_remaining: int,
    *,
    conversation_id: str,
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

    **A DOCUMENT-derived crisis is the other shape, and takes the ordinary
    branch on purpose (Plan 6 Task 10, ruling 1).**
    `localised.document_crisis` means the question was clean and a crisis
    phrase was found only in an attached file -- weaker evidence, possibly
    historical or about someone else. That turn carries a real legal answer,
    so `answer`, `sources` and `is_answerable` render exactly as any other
    turn's, and the helpline card is attached beside them through the fields
    that already exist for it: `crisis_response` (set below) plus
    `helplines`/`footer`, which
    `openlaw.language.pipeline._with_document_crisis` has already prepended
    the category's numbers to. Branching on `document_crisis` the way this
    function branches on `crisis` would throw away the answer that path
    exists to preserve.

    **`document_claims` leave through their own field, never through
    `answer`.** `openlaw.answers.validate.ValidatedAnswer` has required since
    Task 7 that a statement about the reader's own paperwork be presented
    "visibly apart from the legal claims, never under a heading that reads as
    law" -- and until this task nothing on this API rendered them at all, so
    the requirement was enforced by a docstring and no code. `document_answer`
    and `document_citations` are that enforcement: `_render_answer` reads only
    `answer.claims`, `_render_document_answer` reads only
    `answer.document_claims`, and neither can reach the other's content. See
    `tests/api/test_conversations.py::
    test_document_claims_render_apart_from_legal_claims`, which fails if
    either half of that leaks into the other.
    """
    if localised.crisis is not None:
        return _crisis_only_response(
            localised, corpus_version, quota_remaining, conversation_id
        )

    document_help = (
        crisis_text(localised.document_crisis, localised.language)
        if localised.document_crisis is not None
        else None
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
        crisis_response=document_help,
        helplines=localised.helplines,
        is_answerable=localised.answer.is_answerable,
        quota_remaining=quota_remaining,
        conversation_id=conversation_id,
        turn_index=localised.turn_index,
        document_answer=render_document_claims(
            localised.document_claims, localised.language
        ),
        document_citations=_document_citation_cards(localised),
    )
