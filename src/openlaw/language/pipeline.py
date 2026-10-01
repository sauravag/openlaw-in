"""The localised end-to-end answer pipeline.

Order is load-bearing::

    detect language
    -> crisis-check the ORIGINAL text (regex, then classifier)  [short-circuits]
    -> translate the query to English
    -> crisis-check the TRANSLATED text (regex ONLY)            [short-circuits]
    -> retrieve
    -> generate
    -> VALIDATE
    -> translate the answer
    -> attach the STORED crisis verdict of every attached document
       (regex-only, computed once at upload, re-read every turn)
       ALONGSIDE the answer -- never instead of it

**This whole order runs on EVERY turn, not only the first.** This function
is one turn; a conversation is many calls to it, and there is no "already
checked" branch anywhere below. A person may reveal danger on the fourth
turn, and the fourth turn gets exactly the same three checks the first one
did.

**Crisis routing runs first, and every time before `retriever.retrieve` is
ever called.** `openlaw.answers.generate`'s module docstring establishes why
crisis routing must run before retrieval and generation at all: a citizen in
danger should never wait on a round trip whose output would be discarded
anyway. Checking the original text once is not enough here, though. The
regex layer in `openlaw.answers.escalation` matches English and
transliterated Hinglish, but misses Devanagari and every other Indic script
entirely (see that module's docstring). The CLASSIFIER-role model, asked on
the original text as the first check's second layer, already covers
non-Latin scripts and other languages -- its own docstring in
`openlaw.answers.crisis_classifier` says so explicitly. So the second check,
after translation, deliberately calls only the bare, free
`openlaw.answers.escalation.detect_crisis` -- NOT
`detect_crisis_with_classifier` again. Re-running the classifier on the
translated text would buy no new coverage (it already read the original
text in whatever script or language it was written in) while doubling
classifier cost on every ordinary turn. What the second pass DOES add is a
second, free chance for the regex layer, now reading English -- a language
it can actually match -- which is the entire, narrow point of checking
twice. Running it BEFORE `retriever.retrieve` (rather than relying on
`generate_answer`'s own built-in check, which would only run after this
module had already called the retriever) is what keeps "before retrieval"
true even for a crisis the regex only catches after translation. A failed
translation can never suppress a helpline either way: `translate_query`
degrades to the original text on any failure, which the first check has
already cleared.

**A document-derived crisis rides ALONGSIDE the answer; a question-derived
one replaces it.** The asymmetry is in the strength of the evidence, and it
is the whole design (Plan 6 Task 10, ruling 1). A question saying "my
husband beats me" is the person telling us, now, that they are in danger:
short-circuiting to helplines is right, and it is free. A crisis phrase
inside an uploaded document is much weaker -- it may be historical (an FIR
from two years ago, attached for context), incidental (a tenancy agreement
with a harassment clause), or about someone else entirely. Replacing the
question she actually asked with a helpline card would be unhelpful, and
for someone attaching an old document as background, close to insulting.
Suppressing the helplines would be equally wrong. So when, and only when,
the question and its translation are both clean, the legal question is
answered normally AND `_with_document_crisis` prepends the crisis response
and its helplines to the footer, setting `LocalisedAnswer.document_crisis`
rather than `crisis`.

That means a document-derived crisis costs a generation call. It is not a
regression: the question was going to be answered anyway. The FREE
short-circuit is preserved exactly where it matters -- the question-derived
path, the one that must keep working with a broken API key. A
question-derived crisis still returns before `translate_query`, before
`retriever.retrieve`, and before any generation, and a document attached to
that same turn changes none of that: the question's verdict is the stronger
evidence and it alone selects the response.

**The document verdict is STORED, and that is a cost decision, not a
shortcut.** A document can reveal what the question never says: a
protection order attached to a question about rent arrears. Its text is
scanned ONCE, at upload, by
`openlaw.documents.extract.scan_for_crisis` -- the free regex layer, never
the paid classifier -- and the verdict is written to
`openlaw.db.models.Document.crisis_kind`. Every later turn reads that
column (`document_crisis_kinds`, passed in verbatim) and never the
document's text. Re-classifying document text per turn would make the price
of a turn proportional to a length the uploader chooses, metered by a quota
that counts questions rather than bytes, and would hand attacker-controlled
text an argument with the one decision that routes a person in danger;
`scan_for_crisis`'s docstring sets both out, along with the limit that
comes with the choice -- the regex layer's Devanagari coverage is a narrow,
precision-tuned set (ruling 2) and every other Indic script is uncovered,
so a document in Tamil or Bengali contributes no verdict and the question's
own classifier call is what covers that user. Nothing in this module ever
re-derives a verdict from text, so a turn with ten attached documents costs
exactly what a turn with none costs.

Several attached documents resolve among THEMSELVES by
`openlaw.answers.escalation.most_urgent_crisis` -- the same severity order
`detect_crisis` applies within one message -- so two documents never
produce an arbitrary winner. They are never weighed against the question's
verdict, which short-circuits on its own and never reaches this step. The
crisis response is rendered in the user's own language whichever layer
found it (Task 6 fix round, H3), so a verdict produced by an
English-reading regex still reaches a Hindi speaker in Hindi.

Once both question-side checks pass, generation is handed to
`openlaw.answers.generate._generate_from_context` rather than the public
`generate_answer` -- the same trick `answer_question` uses in that module,
and for the same reason (see its own docstring): `english_query` has
already been cleared by the classifier once (on the original text) and by
the regex once more (on the translation), so calling the public entry point
would ask the CLASSIFIER-role model a second time on text that cannot have
changed, for no benefit -- exactly the double-cost pattern that module's own
docstring warns against. Consequently an ordinary, non-crisis question costs
at most one classifier call per turn, not two.

**A classifier failure on the original-text check must still reach the
caller.** `detect_crisis_with_classifier` never raises; on a failed or
unparseable classifier call it proceeds on the regex verdict (already "no
crisis") and returns the failure as `CrisisDetection.failure` instead (see
that module's docstring). Dropping that failure here -- as an earlier
version of this module did, having nowhere to put it once
`_generate_from_context`'s `AnswerResult.classifier_failures` was discarded
at the final `LocalisedAnswer` construction -- would silently defeat the
Plan 3 ruling that a classifier outage must be discoverable and countable
after the fact (see `openlaw.answers.generate.persist_answer_events`, which
exists for exactly that). `LocalisedAnswer.classifier_failures` carries it
through instead: built here, on every returned object (crisis or not), and
left for the caller to persist -- this function takes no `Settings` and
writes nothing itself, the same "build now, persist later" split
`generate_answer` uses for `HallucinationEvent`/`ClassifierFailureEvent`.
The caller does that persisting by handing the whole `LocalisedAnswer` to
`openlaw.answers.generate.persist_answer_events`, which accepts it
structurally (`AnswerEvents`) and writes both logs -- exactly what
`scripts/review_translations.py` now does for every row it generates.
Building the events and then never persisting them, as that script
previously did, defeated the same ruling just as thoroughly as dropping
them here would have. Neither of the other two checks can itself produce a
classifier failure -- the stored document verdict and the translated-text
regex never call a model -- so the only source is the one classifier call
on the original text, and a crisis found by EITHER of the other two must
still carry that failure out rather than letting the short-circuit swallow
it.

**Translation of the ANSWER happens strictly after generation returns**,
which is strictly after `openlaw.answers.validate.validate_answer` has run
inside `_generate_from_context`. By that point every surviving claim already
resolves to a retrieved chunk, so `translate_answer` cannot introduce an
unverified citation -- it can only render already-verified text badly, and
any failure there (a refusal, malformed JSON, or a claim-id mismatch)
degrades to the untouched English `ValidatedAnswer`
(`openlaw.language.translate`'s module docstring covers the id-based
reassembly and every fallback path in full).

**Nothing here ever reads `ValidatedAnswer.unverified_summary` for
display.** `LocalisedAnswer.answer` is the same `ValidatedAnswer` object
`generate_answer`/`translate_answer` hand back, untouched: `unverified_summary`
stays `None` whenever the answer is not answerable, on both the English and
the translated result, and the only content a caller may ever render is
`claims` and `sources` (see `openlaw.answers.validate`'s module docstring
for why that field is named the way it is).

**This module does not build a `Retriever` of its own.** The `Retriever`
protocol below exists purely so a caller can hand in whatever retrieves --
in production that should be built from
`openlaw.answers.generate.build_retrieval_context`, which already wires
`openlaw.retrieval.factory.build_query_embedder` (never a Voyage embedder
constructed directly), calls `search()` with the raw query text alongside
its vector, and gates graph expansion on `Settings.graph_expansion_enabled`
(default off). Duplicating any of that wiring here would just be a second
place for it to drift out of sync.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from openlaw.answers.crisis_classifier import (
    ClassifierFailureEvent,
    CrisisDetection,
    detect_crisis_with_classifier,
)
from openlaw.answers.escalation import (
    CrisisKind,
    HallucinationEvent,
    Helpline,
    crisis_helplines,
    crisis_kind_from_value,
    crisis_response,
    detect_crisis,
    escalation_footer,
    most_urgent_crisis,
    nalsa_helpline,
)
from openlaw.answers.generate import _failures_of, _generate_from_context
from openlaw.answers.models import LLMAdapter, ModelRegistry
from openlaw.answers.validate import ValidatedAnswer
from openlaw.language.detect import detect_language
from openlaw.language.glossary import Glossary
from openlaw.language.translate import translate_answer, translate_query
from openlaw.retrieval.context import RetrievalContext


class Retriever(Protocol):
    """Anything that can build a retrieval context from an English query.

    Build the production implementation from
    `openlaw.answers.generate.build_retrieval_context` -- see the module
    docstring for why this protocol deliberately does not duplicate that
    wiring itself.
    """

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        ...


class LocalisedAnswer(BaseModel):
    """A validated answer rendered in the user's language.

    `answer` is the exact `ValidatedAnswer` `generate_answer`/
    `translate_answer` produced: `unverified_summary` is `None` whenever
    `is_answerable` is False, and the only content ever safe to render is
    `claims` and `sources` -- see the module docstring.

    `english_answer` is the SAME validated answer, before `translate_answer`
    ever touched it -- the exact object `_generate_from_context` returned.
    It exists for exactly one consumer: `scripts/review_translations.py`,
    where a native-speaker reviewer must be able to see the untranslated
    English answer next to the translated one to judge whether the legal
    meaning survived translation -- neither the original question nor the
    translated answer alone is enough for that judgement. It carries the
    same "only `claims` and `sources` are safe to render" rule as `answer`.
    For a crisis short-circuit (no generation ever ran) and for an English
    question (`translate_answer` is a no-op there), `english_answer` and
    `answer` are equal.

    `classifier_failures` carries any `ClassifierFailureEvent` recorded by
    the original-text crisis check (see the module docstring for why the
    second, translated-text check can never itself produce one). It is
    build-only, same as `openlaw.answers.generate.AnswerResult
    .classifier_failures` -- this model carries no `Settings` and this
    module writes nothing; a caller holding a `Settings` persists it, and
    `events`, by passing this object to
    `openlaw.answers.generate.persist_answer_events` (it satisfies that
    function's `AnswerEvents` protocol structurally).
    """

    model_config = ConfigDict(frozen=True)

    answer: ValidatedAnswer
    english_answer: ValidatedAnswer
    language: str
    english_query: str
    crisis: CrisisKind | None = None
    #: Set when a crisis was found ONLY in an attached document, never in
    #: the question (Plan 6 Task 10, ruling 1). Distinct from `crisis`,
    #: which means "this turn was short-circuited and carries no legal
    #: answer": a `document_crisis` turn carries a complete, ordinary legal
    #: answer AND the helpline card, because a crisis phrase inside an
    #: uploaded file is weaker evidence than a person saying it -- it may be
    #: historical, incidental, or about someone else. Suppressing the
    #: helplines would be wrong; so would replacing the question she
    #: actually asked. `openlaw.api.schemas.to_response` renders both.
    document_crisis: CrisisKind | None = None
    footer: str = ""
    #: Structured, tap-to-call numbers for `footer` (Task 6 fix round, C2) --
    #: at minimum the NALSA number every footer cites, plus the
    #: category-specific numbers on a crisis turn. The API layer
    #: (`schemas.to_response`) forwards this verbatim as `AskResponse
    #: .helplines`; the frontend renders links from it directly rather than
    #: pattern-matching `footer`/`crisis_response` prose for digits.
    helplines: tuple[Helpline, ...] = ()
    events: tuple[HallucinationEvent, ...] = ()
    classifier_failures: tuple[ClassifierFailureEvent, ...] = ()


#: The fixed, generation-free answer returned on a crisis short-circuit --
#: `unverified_summary` is `None` and there are no claims or sources, matching
#: `openlaw.answers.generate._UNANSWERABLE_ANSWER` exactly. Built locally
#: (rather than importing that private constant) since `ValidatedAnswer` is
#: the public type this module already depends on.
_UNANSWERABLE = ValidatedAnswer(unverified_summary=None, is_answerable=False)


def _crisis_answer(
    crisis: CrisisKind,
    language: str,
    english_query: str,
    state: str | None,
    classifier_failures: tuple[ClassifierFailureEvent, ...] = (),
) -> LocalisedAnswer:
    """Return the immediate, generation-free result for a crisis question.

    Mirrors `openlaw.answers.generate._crisis_result`: the footer leads with
    the category-specific helpline (`crisis_response`) followed by the
    standard `escalation_footer`, never the standard footer alone.
    Rendered in `language` when it is `"hi"`, English otherwise (Task 6 fix
    round, H3) -- but never via live translation: both functions select
    between two STATIC, hand-authored strings (see their docstrings and
    `openlaw.answers.escalation`'s Hindi dict comment), so a translation
    failure can never hold this content hostage the way a model call could.
    `classifier_failures` is forwarded unconditionally -- a crisis found by
    the translated-text regex does not erase a classifier failure the
    original-text check already recorded.
    """
    footer = (
        f"{crisis_response(crisis, language)}\n\n{escalation_footer(state, language)}"
    )
    helplines = crisis_helplines(crisis, language) + (nalsa_helpline(language),)
    return LocalisedAnswer(
        answer=_UNANSWERABLE,
        english_answer=_UNANSWERABLE,
        language=language,
        english_query=english_query,
        crisis=crisis,
        footer=footer,
        helplines=helplines,
        classifier_failures=classifier_failures,
    )


def _document_crisis(crisis_kinds: tuple[str | None, ...]) -> CrisisKind | None:
    """Return the most urgent STORED verdict among the attached documents.

    Each entry is one document's `openlaw.db.models.Document.crisis_kind`
    column value, verbatim -- computed once at upload by
    `openlaw.documents.extract.scan_for_crisis` and read back here, never
    recomputed from document text (that function's docstring has the cost
    and injection reasoning). An unrecognised value is logged loudly and
    contributes nothing; see `crisis_kind_from_value`.
    """
    return most_urgent_crisis(crisis_kind_from_value(value) for value in crisis_kinds)


def _with_document_crisis(
    answer: LocalisedAnswer, crisis: CrisisKind | None
) -> LocalisedAnswer:
    """Return `answer` with a document-derived crisis riding ALONGSIDE it.

    The question was clean and has been answered normally; a document
    attached to this turn was not. The helpline card is prepended to the
    footer and its numbers are prepended to `helplines`, and the legal
    answer is left completely untouched -- see the module docstring for why
    a document verdict adds to the answer instead of replacing it.

    `document_crisis` is set, NOT `crisis`: `crisis` means "this turn was
    short-circuited and carries no legal answer", and
    `openlaw.api.schemas.to_response` branches on exactly that to decide
    whether to render claims and sources at all. Setting it here would
    throw away the answer this path exists to preserve.
    """
    if crisis is None:
        return answer
    footer = f"{crisis_response(crisis, answer.language)}\n\n{answer.footer}"
    helplines = crisis_helplines(crisis, answer.language) + answer.helplines
    return answer.model_copy(
        update={"footer": footer, "helplines": helplines, "document_crisis": crisis}
    )


def _clear_crisis_and_translate(
    question: str,
    language: str,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    state: str | None,
) -> LocalisedAnswer | tuple[str, tuple[ClassifierFailureEvent, ...]]:
    """Run both question-side crisis checks and, if neither fires, translate.

    The first check is the full two-layer `detect_crisis_with_classifier` on
    `question` itself. The second, on the translated `english_query`, is the
    bare regex `detect_crisis` -- see the module docstring for why the
    classifier is not asked a second time.

    Both of these are QUESTION-side: the person telling us, now, that they
    are in danger. Either one short-circuits the turn. A document's stored
    verdict is weaker evidence and is handled by the caller instead, after
    the answer is generated (`_with_document_crisis`).

    Returns a crisis `LocalisedAnswer` the moment either check fires --
    before `translate_query` is even called for the first, and before the
    caller may touch `retriever.retrieve` for either. Otherwise returns
    `(english_query, classifier_failures)`, where `classifier_failures` is
    whatever the (only possible) classifier call above recorded, so the
    caller can forward it onto generation and, eventually, the returned
    `LocalisedAnswer` without asking the classifier again.
    """
    original_detection: CrisisDetection = detect_crisis_with_classifier(
        question, adapter, registry
    )
    failures = _failures_of(original_detection)
    if original_detection.crisis is not None:
        return _crisis_answer(original_detection.crisis, language, "", state, failures)

    english_query = translate_query(question, language, adapter, registry)

    translated_crisis = detect_crisis(english_query)
    if translated_crisis is not None:
        return _crisis_answer(
            translated_crisis, language, english_query, state, failures
        )

    return english_query, failures


def answer_in_language(
    question: str,
    retriever: Retriever,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    glossary: Glossary,
    state: str | None = None,
    language: str | None = None,
    document_crisis_kinds: tuple[str | None, ...] = (),
) -> LocalisedAnswer:
    """Answer `question` in `language`, or in the language it was asked in.

    `language` is the caller's explicit choice -- an interface's language
    selector -- and it wins over detection outright: it selects the answer's
    target language, the glossary's pinned terminology, and the per-language
    translator model for both translation calls. Detection
    (`openlaw.language.detect.detect_language`) is only the default, for a
    caller that says nothing.

    Two things are unreachable without it, both measured rather than
    theoretical. `detect_by_script` maps every Devanagari question to Hindi
    and every Bengali-script question to Bengali, so Marathi and Assamese
    are never detected at all -- a Marathi speaker gets Hindi output with
    Hindi-pinned glossary terms, and the glossary's Marathi renderings can
    never be used. And a Hinglish question carries no Indic signal, so it
    detects as `"en"` (see `openlaw.language.detect`'s module docstring);
    `translate_query` already works around that for retrieval, but the
    ANSWER would still come back in English. `language="hi"` fixes the
    second half of that turn.

    `document_crisis_kinds` is the `openlaw.db.models.Document.crisis_kind`
    column value of each document attached to THIS turn, verbatim (`None`
    for a document the upload-time scan cleared). This function is called
    once per turn, so passing it on every turn -- not only the turn that
    uploaded -- is what makes a document's verdict outlive its upload. It
    carries stored verdicts and never document text: nothing here can
    re-scan, or re-classify, a document per turn.

    See the module docstring for why crisis routing runs three checks -- the
    full classifier check on `question` itself, the stored document
    verdicts, then a regex-only check on the English translation -- and all
    of them before `retriever.retrieve` is ever called.
    """
    target = language or detect_language(question)

    cleared = _clear_crisis_and_translate(question, target, adapter, registry, state)
    if isinstance(cleared, LocalisedAnswer):
        return cleared
    english_query, classifier_failures = cleared
    document_crisis = _document_crisis(document_crisis_kinds)

    context = retriever.retrieve(english_query)
    footer = escalation_footer(state, target)
    result = _generate_from_context(
        english_query, context, adapter, registry, footer, classifier_failures
    )
    localised = translate_answer(result.answer, target, adapter, registry, glossary)

    answered = LocalisedAnswer(
        answer=localised,
        english_answer=result.answer,
        language=target,
        english_query=english_query,
        crisis=result.crisis,
        footer=result.footer,
        # Every footer cites the NALSA number, crisis or not (Task 6 fix
        # round, C2) -- structured here so the frontend never has to
        # pattern-match it out of `footer`'s prose.
        helplines=(nalsa_helpline(target),),
        events=result.events,
        classifier_failures=result.classifier_failures,
    )
    return _with_document_crisis(answered, document_crisis)
