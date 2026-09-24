"""The localised end-to-end answer pipeline.

Order is load-bearing::

    detect language
    -> crisis-check the ORIGINAL text (regex, then classifier)
    -> translate the query to English
    -> crisis-check the TRANSLATED text (regex, then classifier, again)
    -> retrieve
    -> generate
    -> VALIDATE
    -> translate the answer

**Crisis routing runs twice, and both times before `retriever.retrieve` is
ever called.** `openlaw.answers.generate`'s module docstring establishes why
crisis routing must run before retrieval and generation at all: a citizen in
danger should never wait on a round trip whose output would be discarded
anyway. Checking the original text once is not enough here, though. The
regex layer in `openlaw.answers.escalation` matches English and
transliterated Hinglish, but misses Devanagari and every other Indic script
entirely (see that module's docstring) -- so a Hindi message typed in
Devanagari reaches only the CLASSIFIER-role model on its first pass, never
the free regex. Translating the query to English and re-running the exact
same two-layer check (`detect_crisis_with_classifier`) on the result gives
the regex layer a second, free chance on every language -- and gives the
classifier a second, independent read too. Running that second check BEFORE
`retriever.retrieve` (rather than relying on `generate_answer`'s own
built-in check, which would only run after this module had already called
the retriever) is what keeps "before retrieval" true for both languages a
question might end up expressed in, original and translated. A failed
translation can never suppress a helpline either way: `translate_query`
degrades to the original text on any failure, which the first check has
already cleared.

Once both checks pass, generation is handed to
`openlaw.answers.generate._generate_from_context` rather than the public
`generate_answer` -- the same trick `answer_question` uses in that module,
and for the same reason (see its own docstring): the text about to be
generated from (`english_query`) has already been run through
`detect_crisis_with_classifier` twice above, so calling the public entry
point a third time would ask the CLASSIFIER-role model again on text that
cannot have changed, for no benefit -- exactly the double-cost pattern that
module's own docstring warns against.

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
    CrisisDetection,
    detect_crisis_with_classifier,
)
from openlaw.answers.escalation import (
    CrisisKind,
    HallucinationEvent,
    crisis_response,
    escalation_footer,
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
    """

    model_config = ConfigDict(frozen=True)

    answer: ValidatedAnswer
    language: str
    english_query: str
    crisis: CrisisKind | None = None
    footer: str = ""
    events: tuple[HallucinationEvent, ...] = ()


#: The fixed, generation-free answer returned on a crisis short-circuit --
#: `unverified_summary` is `None` and there are no claims or sources, matching
#: `openlaw.answers.generate._UNANSWERABLE_ANSWER` exactly. Built locally
#: (rather than importing that private constant) since `ValidatedAnswer` is
#: the public type this module already depends on.
_UNANSWERABLE = ValidatedAnswer(unverified_summary=None, is_answerable=False)


def _crisis_answer(
    crisis: CrisisKind, language: str, english_query: str, state: str | None
) -> LocalisedAnswer:
    """Return the immediate, generation-free result for a crisis question.

    Mirrors `openlaw.answers.generate._crisis_result`: the footer leads with
    the category-specific helpline (`crisis_response`) followed by the
    standard `escalation_footer`, never the standard footer alone. Left in
    English regardless of `language` -- these are phone numbers and a short,
    fixed set of instructions, not content a translation failure should ever
    be allowed to hold hostage.
    """
    footer = f"{crisis_response(crisis)}\n\n{escalation_footer(state)}"
    return LocalisedAnswer(
        answer=_UNANSWERABLE,
        language=language,
        english_query=english_query,
        crisis=crisis,
        footer=footer,
    )


def _clear_crisis_and_translate(
    question: str,
    language: str,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    state: str | None,
) -> LocalisedAnswer | tuple[str, CrisisDetection]:
    """Run both crisis checks and, if neither fires, translate the query.

    Returns a crisis `LocalisedAnswer` the moment either check fires --
    before `translate_query` is even called for the first check, and before
    the caller may touch `retriever.retrieve` for either. Otherwise returns
    `(english_query, translated_detection)`, the second detection kept so
    the caller can forward any classifier failure it recorded onto
    generation without asking the classifier a third time. See the module
    docstring for why the check runs on both the original and translated
    text.
    """
    original_detection: CrisisDetection = detect_crisis_with_classifier(
        question, adapter, registry
    )
    if original_detection.crisis is not None:
        return _crisis_answer(original_detection.crisis, language, "", state)

    english_query = translate_query(question, language, adapter, registry)

    translated_detection: CrisisDetection = detect_crisis_with_classifier(
        english_query, adapter, registry
    )
    if translated_detection.crisis is not None:
        return _crisis_answer(
            translated_detection.crisis, language, english_query, state
        )

    return english_query, translated_detection


def answer_in_language(
    question: str,
    retriever: Retriever,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    glossary: Glossary,
    state: str | None = None,
) -> LocalisedAnswer:
    """Answer `question` in the language it was asked in.

    See the module docstring for why crisis routing runs twice -- once on
    `question` itself, once on its English translation -- and both times
    before `retriever.retrieve` is ever called.
    """
    language = detect_language(question)

    cleared = _clear_crisis_and_translate(question, language, adapter, registry, state)
    if isinstance(cleared, LocalisedAnswer):
        return cleared
    english_query, translated_detection = cleared

    context = retriever.retrieve(english_query)
    footer = escalation_footer(state)
    result = _generate_from_context(
        english_query,
        context,
        adapter,
        registry,
        footer,
        _failures_of(translated_detection),
    )
    localised = translate_answer(result.answer, language, adapter, registry, glossary)

    return LocalisedAnswer(
        answer=localised,
        language=language,
        english_query=english_query,
        crisis=result.crisis,
        footer=result.footer,
        events=result.events,
    )
