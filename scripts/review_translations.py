"""Generate a native-speaker translation review sheet.

Runs every non-English question available -- both the verified evaluation
set and the still-unresearched backlog -- through the localised pipeline and
writes a Markdown sheet pairing the original question, the English
translation used for retrieval, the untranslated English answer, and the
translated answer. Both answers are shown side by side because that is what
a native speaker needs to judge whether the legal meaning survived
translation -- the translated text alone is not enough. A native speaker
marks each row OK or WRONG.

**Why this reads from two files, not one.** The spec gates multilingual
launch on native-speaker review, and this script is the only artefact that
makes that review possible. `openlaw.evaluation.dataset.load_eval_set`
raises on any row with an empty `gold_chunk_ids` (`EvalQuestion`'s own
`Field(min_length=1)`) -- deliberately, so an unresearched row cannot
silently inflate a *measurement* run (see that module's docstring). Today
`data/eval/citizen_questions_verified.jsonl` holds exactly one verified
question, and it is English, so filtering the verified set alone for
non-English rows yields nothing to review at all. This script also reads
`data/eval/citizen_questions.jsonl` -- 18 real citizen questions with no
gold answer researched yet -- directly, bypassing `load_eval_set`'s
validation, because a review sheet is not a measurement: showing a Hindi
speaker three real, unverified questions clearly labelled "backlog, no gold
citation" is more honest and more useful than showing them nothing. The
sheet states both counts plainly rather than looking complete when it is
not.

**Why a missing Anthropic API key degrades instead of failing.** Generating
a real translated answer needs `OPENLAW_ANTHROPIC_API_KEY`, which is not
configured in every environment this script runs in. Rather than fail, or
worse, invent a plausible-looking translated answer that a reviewer could
mistake for real output, every row says explicitly "NOT GENERATED" when no
key is present. The same degrade covers a live failure on one particular
question (a Qdrant outage, a rate limit): that row alone reports
"GENERATION FAILED" and every other row still gets its normal chance to
generate, rather than one bad question sinking the whole sheet.

**Why retrieval is wired through `build_retrieval_context`, not a
hand-built `VoyageQueryEmbedder`.** `openlaw.answers.generate`'s module
docstring is explicit that constructing `VoyageQueryEmbedder` directly would
silently make a paid API the default; `openlaw.language.pipeline`'s module
docstring says the same about any caller of the `Retriever` protocol. This
script's `_QdrantRetriever` therefore delegates to `build_retrieval_context`,
which resolves the embedder through
`openlaw.retrieval.factory.build_query_embedder(settings)` -- honouring
whatever backend the environment is actually configured for -- exactly as
`scripts/run_eval.py` already does.

**Why the sheet never renders `ValidatedAnswer.unverified_summary`.** That
field is unchecked model prose; `openlaw.answers.validate.ValidatedAnswer`'s
own docstring says the only content ever safe to show a user is built from
`claims` and `sources`. `_render_claims` reads only `claims`.

**Glossary coverage per language, counted the way the glossary actually
works.** The glossary is deliberately uneven -- Hindi pins all 12 terms,
Marathi/Bengali/Tamil 2 each, and several supported languages (Telugu,
Gujarati, Kannada, Malayalam, Punjabi, Odia, Urdu, Assamese) none at all,
falling through to the English term untranslated. Nine of the twelve terms
are `preserve_english`, which means their pinned rendering is a bracket
gloss beside the English term, never a replacement for it: an earlier
version of this report counted those as "an accepted rendering", which told
a Marathi reviewer that 2 of 12 terms were translated into Marathi when in
fact none are and never will be. `LanguageCoverage` therefore counts
translations and bracket glosses separately and every section states both.
`_glossary_coverage` reads the raw glossary JSON directly rather than adding
an enumeration method to the shipped `Glossary` class, which has no need for
one outside this report.

**Why each row asks for its language explicitly.** Every row is labelled
with the language its source file declares, and `answer_in_language` is
given that same language rather than being left to detect one: detection
maps all Devanagari to Hindi and all Bengali script to Bengali, so a Marathi
or Assamese row would otherwise be reviewed as Marathi while holding Hindi
output with Hindi-pinned terms -- the single hardest error for a reviewer to
attribute. The answer's own reported language is printed next to it so the
label can be checked rather than trusted.

**Why this script persists the pipeline's events.** `answer_in_language`
builds `HallucinationEvent`s and `ClassifierFailureEvent`s and writes
neither -- it holds no `Settings` by design (see its module docstring's
"build now, persist later" split). This script holds one, so it calls
`openlaw.answers.generate.persist_answer_events` on every generated row,
exactly as `answer_question` does on the English path. Without it a review
run -- the one time the multilingual path is exercised in bulk -- would
discard every hallucination and classifier failure it uncovered.

Run: python scripts/review_translations.py
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from openlaw.answers.escalation import (
    DEVANAGARI_CRISIS_PATTERNS,
    DEVANAGARI_PATTERN_COUNT,
)
from openlaw.answers.generate import build_retrieval_context, persist_answer_events
from openlaw.answers.models import LLMAdapter, ModelRegistry
from openlaw.answers.validate import ValidatedAnswer
from openlaw.api.deps import build_llm_adapter
from openlaw.config import Settings, get_settings
from openlaw.evaluation.dataset import MINIMUM_QUESTIONS, EvalQuestion, load_eval_set
from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import DEFAULT_GLOSSARY_PATH, Glossary, load_glossary
from openlaw.language.pipeline import Retriever, answer_in_language
from openlaw.retrieval.context import RetrievalContext

VERIFIED_EVAL_PATH = Path("data/eval/citizen_questions_verified.jsonl")
BACKLOG_EVAL_PATH = Path("data/eval/citizen_questions.jsonl")
OUTPUT = Path("data/eval/translation_review.md")


class BacklogQuestion(BaseModel):
    """A real citizen question with no verified gold citation yet.

    Loaded independently of `EvalQuestion`/`load_eval_set` -- see the module
    docstring for why every backlog row's empty `gold_chunk_ids` would make
    `load_eval_set` raise, and why this script reads the file directly
    instead.
    """

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    language: str = "en"
    category: str = "general"


class _QdrantRetriever:
    """Retrieves from the live corpus through the production wiring.

    Delegates to `build_retrieval_context` rather than constructing a query
    embedder itself -- see the module docstring for why.
    """

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def retrieve(self, english_query: str) -> RetrievalContext:
        """Return the retrieval context for `english_query`."""
        return build_retrieval_context(english_query, self._settings, self._client)


@dataclass(frozen=True)
class Generation:
    """Everything one row needs to actually generate (and record) an answer.

    Bundled rather than threaded through as four more parameters, and carried
    as one `None`-able value so "no API key" stays a single check. `settings`
    is here for one reason: `answer_in_language` builds hallucination and
    classifier-failure events and deliberately persists neither (it holds no
    `Settings` and writes nothing -- see its module docstring's "build now,
    persist later" split). This script is the boundary that holds one, so it
    does the persisting, exactly as `answer_question` does on the English
    path.
    """

    adapter: LLMAdapter
    registry: ModelRegistry
    retriever: Retriever
    settings: Settings


@dataclass(frozen=True)
class LanguageCoverage:
    """How many pinned glossary terms one language actually covers.

    Split in two because the glossary means two different things by a
    "rendering". A `preserve_english` term is never translated by design --
    the English term is kept and the local rendering appears beside it in
    brackets -- so counting those as coverage told a reviewer the term was
    translated when it structurally cannot be. Nine of the twelve shipped
    terms are `preserve_english`.
    """

    translated: int = 0
    bracket_glossed: int = 0


def _load_verified(path: Path) -> tuple[EvalQuestion, ...]:
    """Return every verified eval question, or () if the file is missing."""
    try:
        return load_eval_set(path)
    except FileNotFoundError:
        return ()


def _load_backlog(path: Path) -> tuple[BacklogQuestion, ...]:
    """Return every backlog question, real but not yet gold-verified.

    Reads the raw JSONL directly rather than through `load_eval_set`: every
    backlog row carries an empty `gold_chunk_ids`, which `EvalQuestion`
    rejects by design -- see the module docstring.
    """
    if not path.exists():
        return ()
    questions: list[BacklogQuestion] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            questions.append(BacklogQuestion.model_validate(json.loads(stripped)))
    return tuple(questions)


def _glossary_term_count(path: Path) -> int:
    """Return how many terms the glossary file at `path` defines, or 0 if
    the file is missing."""
    if not path.exists():
        return 0
    return len(json.loads(path.read_text(encoding="utf-8")))


def _glossary_coverage(path: Path) -> dict[str, LanguageCoverage]:
    """Return, per language code, how many glossary terms that language
    actually translates and how many it only glosses in brackets, or {} if
    the file is missing.

    Reads the raw glossary JSON directly -- see the module docstring for why
    this is not a method on `Glossary` itself.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    translated: dict[str, int] = {}
    glossed: dict[str, int] = {}
    for entry in raw:
        counts = glossed if entry.get("preserve_english") else translated
        for language in entry.get("translations", {}):
            counts[language] = counts.get(language, 0) + 1
    return {
        language: LanguageCoverage(
            translated=translated.get(language, 0),
            bracket_glossed=glossed.get(language, 0),
        )
        for language in set(translated) | set(glossed)
    }


def _coverage_note(
    language: str, coverage: dict[str, LanguageCoverage], total_terms: int
) -> str:
    """Return the glossary-coverage line a reviewer of `language` should see.

    Counts a bracket gloss separately from a translation: a
    `preserve_english` term stays in English however many renderings the
    glossary pins for it.
    """
    covered = coverage.get(language, LanguageCoverage())
    language_name = SUPPORTED_LANGUAGES.get(language, language)
    note = (
        f"**Glossary coverage:** {covered.translated} of {total_terms} pinned "
        f"legal terms are translated into {language_name}"
    )
    if covered.bracket_glossed:
        note += (
            f", and {covered.bracket_glossed} more are kept in English with a "
            f"{language_name} rendering in brackets"
        )
    note += "."
    if covered.translated == 0 and covered.bracket_glossed == 0:
        note += (
            " No glossary backs this language yet -- every pinned term falls "
            "through to the English original. Read statutory-term choices "
            "with extra care."
        )
    return note


def _render_claims(answer: ValidatedAnswer) -> str:
    """Render the only content ever safe to show a user: `claims` text.

    Never reads `unverified_summary` -- see the module docstring and
    `ValidatedAnswer`'s own docstring for why that field must never be
    rendered.
    """
    if not answer.is_answerable or not answer.claims:
        return "_(no verifiable answer was produced for this question)_"
    return "\n".join(f"- {claim.text}" for claim in answer.claims)


def _generation_lines(
    question: str, language: str, glossary: Glossary, generation: Generation
) -> list[str]:
    """Return the Markdown lines for one question's actual (or failed)
    generation.

    `language` is passed to `answer_in_language` as an explicit choice, not
    left to detection: the sheet labels each row with the language its source
    file declares, and detection maps every Devanagari question to Hindi, so
    a Marathi row would otherwise be reviewed as Marathi while holding a
    Hindi answer with Hindi-pinned terms. The answer's own reported language
    is printed alongside, so the label is checkable rather than asserted.

    Shows the English answer alongside the translated one -- a reviewer
    judging whether the legal meaning survived translation needs both, not
    just the translated text on its own (see `LocalisedAnswer.english_answer`'s
    docstring in `openlaw.language.pipeline`).

    Persists whatever events the turn produced before returning: the pipeline
    builds them and writes nothing itself (see `Generation`).
    """
    try:
        result = answer_in_language(
            question,
            generation.retriever,
            generation.adapter,
            generation.registry,
            glossary,
            language=language,
        )
    except Exception as exc:  # noqa: BLE001 -- one bad question must not sink the sheet
        return [f"**Translated answer:** GENERATION FAILED -- {exc}", ""]

    persist_answer_events(result, generation.settings)
    answer_language = SUPPORTED_LANGUAGES.get(result.language, result.language)
    return [
        f"**English used for retrieval:** {result.english_query}",
        "",
        f"**Answer language:** {answer_language}",
        "",
        "**English answer:**",
        _render_claims(result.english_answer),
        "",
        "**Translated answer:**",
        _render_claims(result.answer),
        "",
    ]


def _question_section(
    question: str,
    language: str,
    category: str,
    source_label: str,
    coverage: dict[str, LanguageCoverage],
    total_terms: int,
    glossary: Glossary,
    generation: Generation | None,
) -> list[str]:
    """Return the Markdown lines for one question's review row."""
    language_name = SUPPORTED_LANGUAGES.get(language, language)
    lines = [
        f"## {language_name} -- {category} ({source_label})",
        "",
        f"**Original question:** {question}",
        "",
        _coverage_note(language, coverage, total_terms),
        "",
    ]

    if generation is None:
        lines.extend(
            [
                "**Translated answer:** NOT GENERATED -- no "
                "`OPENLAW_ANTHROPIC_API_KEY` is configured in this "
                "environment. Set it (and ensure Qdrant and the query "
                "embedder are reachable) and re-run this script to fill in "
                "this row.",
                "",
            ]
        )
    else:
        lines.extend(_generation_lines(question, language, glossary, generation))

    lines.extend(["**Verdict:** ( ) OK  ( ) WRONG -- notes:", "", "---", ""])
    return lines


def _sheet_header(
    verified_total: int,
    verified_non_english: int,
    backlog_total: int,
    backlog_non_english: int,
    minimum: int,
    generation_available: bool,
) -> list[str]:
    """Return the sheet's opening Markdown lines, stating the honest state
    of the inputs before any question section."""
    lines = [
        "# Translation review sheet",
        "",
        "Mark each row **OK** or **WRONG**. Flag any legal term that reads",
        "plausibly but means something different -- that is the failure mode",
        "automated metrics cannot catch. You do not need to be a lawyer or an",
        "engineer, only a fluent reader of the language. For each row, judge:",
        "",
        "- Is the legal meaning of the English answer preserved in translation?",
        "- Is the register right for an ordinary person with no legal training?",
        "- Are the statutory terms (section numbers, procedure names) correct?",
        "",
        "## Honest state of the inputs",
        "",
        f"- Verified evaluation questions: {verified_total} "
        f"(target: {minimum}). {verified_non_english} are in a language "
        "other than English.",
        f"- Backlog questions (real citizen questions, no gold citation "
        f"researched yet): {backlog_total} total, {backlog_non_english} in "
        "a language other than English. Rows below labelled 'backlog' have "
        "no verified source to check retrieval against -- review only the "
        "translation quality, not whether the right law was cited.",
    ]
    if not generation_available:
        lines.append(
            "- No `OPENLAW_ANTHROPIC_API_KEY` is configured in this "
            "environment, so no translated answer below was actually "
            "generated. Every row says so explicitly rather than showing "
            "fabricated content -- re-run this script with the key set to "
            "fill them in."
        )
    lines.extend(["", ""])
    lines.extend(_devanagari_crisis_section())
    return lines


def _devanagari_crisis_section() -> list[str]:
    """Return the review section for the UNVERIFIED Devanagari crisis
    patterns (Plan 6 Task 10, ruling 2).

    These decide whether a Hindi speaker in danger is shown a helpline, and
    they are in code rather than in prose -- which is exactly why they are
    printed here. Being compiled by Python is not review. They carry the
    same "pending native-speaker review" caveat as the static Hindi crisis
    and refusal strings, and this section is how a reviewer is actually
    asked about them rather than being expected to go reading
    `openlaw.answers.escalation`.
    """
    lines = [
        "## Hindi crisis patterns -- UNVERIFIED, please review",
        "",
        f"These {DEVANAGARI_PATTERN_COUNT} Devanagari patterns decide whether "
        "a Hindi speaker is shown a crisis helpline. They are machine-written "
        "and have had **no native-speaker review** -- the same caveat the "
        "static Hindi crisis and refusal strings carry. For each one, judge:",
        "",
        "- Does it read as something a person in this situation would "
        "actually write?",
        "- Would it fire on ordinary Hindi legal text (a lease, a rent "
        "notice, a divorce petition) where no one is in danger? Mark it "
        "**TOO BROAD** if so.",
        "- Is a common phrasing missing entirely? Write it in.",
        "",
        "They are tuned for precision rather than recall on purpose: a match "
        "found only in an uploaded document adds a helpline card beside a "
        "normal legal answer, and a card that appears on every Hindi "
        "document is one people learn to ignore.",
        "",
    ]
    for kind, patterns in DEVANAGARI_CRISIS_PATTERNS.items():
        lines.append(f"### {kind.value}")
        lines.append("")
        lines.extend(f"- `{pattern}`  -- OK / TOO BROAD / WRONG" for pattern in patterns)
        lines.append("")
    lines.append("")
    return lines


def build_sheet(
    verified: tuple[EvalQuestion, ...],
    backlog: tuple[BacklogQuestion, ...],
    coverage: dict[str, LanguageCoverage],
    total_terms: int,
    glossary: Glossary,
    generation: Generation | None,
) -> str:
    """Return the full Markdown review sheet for `verified` and `backlog`."""
    verified_non_english = tuple(q for q in verified if q.language != "en")
    backlog_non_english = tuple(q for q in backlog if q.language != "en")

    lines = _sheet_header(
        len(verified),
        len(verified_non_english),
        len(backlog),
        len(backlog_non_english),
        MINIMUM_QUESTIONS,
        generation is not None,
    )

    rows = [(q, "verified") for q in verified_non_english]
    rows.extend((q, "backlog -- no gold citation") for q in backlog_non_english)
    for question, source_label in rows:
        lines.extend(
            _question_section(
                question.question,
                question.language,
                question.category,
                source_label,
                coverage,
                total_terms,
                glossary,
                generation,
            )
        )

    if not rows:
        lines.append(
            "No non-English question is available to review yet, in either "
            "the verified evaluation set or the backlog."
        )

    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    glossary = load_glossary()
    total_terms = _glossary_term_count(DEFAULT_GLOSSARY_PATH)
    coverage = _glossary_coverage(DEFAULT_GLOSSARY_PATH)

    verified = _load_verified(VERIFIED_EVAL_PATH)
    backlog = _load_backlog(BACKLOG_EVAL_PATH)

    generation: Generation | None = None
    client: httpx.Client | None = None

    # Routed through `build_llm_adapter`, not a hard-wired AnthropicAdapter:
    # the configured model for a role may name its provider
    # (`openrouter:z-ai/glm-5.3-flash`), and handing that whole string to the
    # Anthropic SDK would fail confusingly. Skips generation entirely when a
    # referenced provider has no key, exactly as the old Anthropic-key check
    # did -- this review sheet is still useful without it.
    registry = ModelRegistry.from_settings(settings)
    try:
        adapter = build_llm_adapter(settings, registry)
    except RuntimeError as error:
        print(f"[Review] generation skipped: {error}")
    else:
        client = httpx.Client(timeout=60)
        generation = Generation(
            adapter=adapter,
            registry=registry,
            retriever=_QdrantRetriever(settings, client),
            settings=settings,
        )

    try:
        sheet = build_sheet(
            verified, backlog, coverage, total_terms, glossary, generation
        )
    finally:
        if client is not None:
            client.close()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(sheet, encoding="utf-8")

    print(f"[Review] wrote sheet to {OUTPUT}")
    if generation is None:
        print(
            "[Review] OPENLAW_ANTHROPIC_API_KEY is not set -- translated "
            "answers were not generated; re-run with it configured"
        )
    print("[Review] send this to a native speaker per language before launch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
