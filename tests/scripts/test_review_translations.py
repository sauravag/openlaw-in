"""Unit tests for `scripts/review_translations.py`'s pure helpers.

This script never talks to Qdrant or Anthropic in its own tests -- it is
loaded directly from its file path (same pattern as `test_run_eval.py`), and
every live network call is either absent (the "no API key" degrade path) or
replaced with `StubAdapter`/a fake `Retriever`, exactly as
`tests/language/test_pipeline.py` exercises `answer_in_language` itself.

Coverage here focuses on the two things a reviewer of this task must trust:
the sheet never fabricates translated content when it cannot generate any,
and it never renders `ValidatedAnswer.unverified_summary` -- only `claims`.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from openlaw.answers.models import Completion, ModelRegistry, StubAdapter
from openlaw.answers.validate import Claim, Source, ValidatedAnswer
from openlaw.config import Settings
from openlaw.evaluation.dataset import EvalQuestion
from openlaw.language.glossary import Glossary, GlossaryTerm
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "review_translations.py"


def _load_review_translations() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_translations", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_translations = _load_review_translations()


class _Retriever:
    """A fixed-context fake, mirroring `tests/language/test_pipeline.py`."""

    def retrieve(self, english_query: str) -> RetrievalContext:
        return RetrievalContext(
            query=english_query,
            chunks=(
                RetrievedChunk(
                    chunk_id="a",
                    text="A landlord shall refund the deposit.",
                    source_url="https://www.indiacode.nic.in/handle/123456789/1",
                    score=0.9,
                    kind=ChunkKind.PROVISION,
                ),
            ),
        )


class _RaisingRetriever:
    """A retriever that always fails -- proves a per-question failure is
    caught and reported rather than crashing the whole sheet."""

    def retrieve(self, english_query: str) -> RetrievalContext:
        raise RuntimeError("simulated Qdrant outage")


def _safe_classifier_response() -> str:
    return json.dumps({"crisis": "none"})


def _generation_response() -> str:
    return json.dumps(
        {
            "summary": "The deposit must be refunded.",
            "claims": [{"text": "A refund is required.", "chunk_ids": ["a"]}],
            "sufficient": True,
        }
    )


def _translation_response() -> str:
    return json.dumps(
        {
            "summary": "जमा राशि वापस करनी होगी।",
            "claims": [{"id": 0, "text": "वापसी आवश्यक है।"}],
        },
        ensure_ascii=False,
    )


def _glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत"},
            ),
        )
    )


def _marathi_glossary() -> Glossary:
    return Glossary(
        (
            GlossaryTerm(
                term="anticipatory bail",
                preserve_english=True,
                translations={"hi": "अग्रिम जमानत", "mr": "अटकपूर्व जामीन"},
            ),
        )
    )


class _GhostCitationAdapter:
    """A classifier outage AND a hallucinated citation in one turn -- the two
    event kinds `persist_answer_events` exists to write. Mirrors
    `tests/language/test_pipeline.py`'s `_ClassifierFailsAdapter`."""

    def __init__(self) -> None:
        self._responses = [
            "my landlord kept my deposit",
            json.dumps(
                {
                    "summary": "The deposit must be refunded.",
                    "claims": [
                        {"text": "A refund is required.", "chunk_ids": ["a"]},
                        {"text": "The landlord goes to prison.", "chunk_ids": ["ghost"]},
                    ],
                    "sufficient": True,
                }
            ),
            _translation_response(),
        ]

    def complete(self, **kwargs: Any) -> Completion:
        if "safety classifier" in kwargs["system"]:
            raise RuntimeError("simulated classifier outage")
        return Completion(text=self._responses.pop(0))


# --- _load_verified ---------------------------------------------------------


def test_load_verified_returns_empty_tuple_for_a_missing_file(tmp_path: Path) -> None:
    assert review_translations._load_verified(tmp_path / "absent.jsonl") == ()


def test_load_verified_loads_every_language(tmp_path: Path) -> None:
    path = tmp_path / "verified.jsonl"
    path.write_text(
        '{"question": "q1", "gold_chunk_ids": ["a"], "language": "en", "category": "tenancy"}\n'
        '{"question": "q2", "gold_chunk_ids": ["b"], "language": "hi", "category": "labour"}\n',
        encoding="utf-8",
    )
    questions = review_translations._load_verified(path)
    assert len(questions) == 2
    assert {q.language for q in questions} == {"en", "hi"}


# --- _load_backlog -----------------------------------------------------------


def test_load_backlog_returns_empty_tuple_for_a_missing_file(tmp_path: Path) -> None:
    assert review_translations._load_backlog(tmp_path / "absent.jsonl") == ()


def test_load_backlog_loads_rows_with_no_gold_chunk_ids(tmp_path: Path) -> None:
    """The defining property this helper exists for: `EvalQuestion` rejects
    an empty `gold_chunk_ids` (`Field(min_length=1)`), but every backlog row
    has one -- this loader must not go through `load_eval_set`."""
    path = tmp_path / "backlog.jsonl"
    path.write_text(
        '{"question": "q1", "gold_chunk_ids": [], "language": "hi", "category": "family"}\n'
        "\n"
        '{"question": "q2", "gold_chunk_ids": [], "language": "en", "category": "labour"}\n',
        encoding="utf-8",
    )
    questions = review_translations._load_backlog(path)
    assert len(questions) == 2
    assert questions[0].language == "hi"
    assert questions[0].question == "q1"


# --- _glossary_term_count / _glossary_coverage -------------------------------


def test_glossary_term_count_is_zero_for_a_missing_file(tmp_path: Path) -> None:
    assert review_translations._glossary_term_count(tmp_path / "absent.json") == 0


def test_glossary_term_count_counts_every_entry(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            [
                {"term": "a", "preserve_english": True, "translations": {}},
                {"term": "b", "preserve_english": False, "translations": {"hi": "x"}},
            ]
        ),
        encoding="utf-8",
    )
    assert review_translations._glossary_term_count(path) == 2


def test_glossary_coverage_is_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert review_translations._glossary_coverage(tmp_path / "absent.json") == {}


def test_glossary_coverage_counts_translations_per_language(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            [
                {
                    "term": "a",
                    "preserve_english": True,
                    "translations": {"hi": "x", "mr": "y"},
                },
                {"term": "b", "preserve_english": False, "translations": {"hi": "z"}},
                {"term": "c", "preserve_english": True, "translations": {}},
            ]
        ),
        encoding="utf-8",
    )
    coverage = review_translations._glossary_coverage(path)
    assert coverage["hi"] == review_translations.LanguageCoverage(
        translated=1, bracket_glossed=1
    )
    assert coverage["mr"] == review_translations.LanguageCoverage(
        translated=0, bracket_glossed=1
    )


def test_glossary_coverage_does_not_count_a_bracket_gloss_as_a_translation(
    tmp_path: Path,
) -> None:
    """A `preserve_english` term is kept in English with the local rendering
    beside it in brackets -- counting those as "an accepted rendering" told a
    reviewer the term was translated when by design it never is."""
    path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            [
                {
                    "term": "anticipatory bail",
                    "preserve_english": True,
                    "translations": {"mr": "अटकपूर्व जामीन"},
                }
            ]
        ),
        encoding="utf-8",
    )
    coverage = review_translations._glossary_coverage(path)
    assert coverage["mr"].translated == 0
    assert coverage["mr"].bracket_glossed == 1


# --- _coverage_note -----------------------------------------------------------


def _coverage(translated: int = 0, bracket_glossed: int = 0):
    return review_translations.LanguageCoverage(
        translated=translated, bracket_glossed=bracket_glossed
    )


def test_coverage_note_flags_a_language_with_zero_glossary_terms() -> None:
    note = review_translations._coverage_note("te", {"hi": _coverage(3, 9)}, 12)
    assert "0 of 12" in note
    assert "No glossary backs this language" in note


def test_coverage_note_reports_partial_coverage() -> None:
    note = review_translations._coverage_note("hi", {"hi": _coverage(3, 9)}, 12)
    assert "3 of 12" in note


def test_coverage_note_counts_bracket_glosses_separately() -> None:
    """Marathi pins two terms, both `preserve_english`: neither is ever
    translated, so a reviewer must be told they stay in English with a
    Marathi gloss in brackets -- not that Marathi has 2 of 12 renderings."""
    note = review_translations._coverage_note("mr", {"mr": _coverage(0, 2)}, 12)
    assert "0 of 12" in note
    assert "2" in note
    assert "brackets" in note


def test_coverage_note_uses_the_language_display_name() -> None:
    note = review_translations._coverage_note("hi", {"hi": _coverage(3, 9)}, 12)
    assert "Hindi" in note


# --- _render_claims -----------------------------------------------------------


def test_render_claims_returns_a_placeholder_for_an_unanswerable_result() -> None:
    answer = ValidatedAnswer(unverified_summary=None, is_answerable=False)
    rendered = review_translations._render_claims(answer)
    assert "no verifiable answer" in rendered.lower()


def test_render_claims_never_reads_unverified_summary() -> None:
    """Binding requirement across the whole pipeline: `unverified_summary`
    is unchecked model prose and must never be rendered."""
    answer = ValidatedAnswer(
        unverified_summary="THIS MUST NEVER APPEAR",
        claims=(Claim(text="A refund is required.", chunk_ids=("a",)),),
        sources=(Source(chunk_id="a", source_url="https://example.gov.in"),),
        is_answerable=True,
    )
    rendered = review_translations._render_claims(answer)
    assert "THIS MUST NEVER APPEAR" not in rendered
    assert "A refund is required." in rendered


# --- _question_section --------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    """Settings whose two event logs land in `tmp_path`, never in `data/`."""
    return Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        hallucination_log_path=tmp_path / "hallucinations.jsonl",
        classifier_failure_log_path=tmp_path / "classifier_failures.jsonl",
    )


def _generation(
    tmp_path: Path, adapter: Any, retriever: Any = None
):
    return review_translations.Generation(
        adapter=adapter,
        registry=ModelRegistry(),
        retriever=retriever or _Retriever(),
        settings=_settings(tmp_path),
    )


def _working_adapter() -> StubAdapter:
    return StubAdapter(
        [
            _safe_classifier_response(),
            "my landlord kept my deposit",  # translate_query
            _generation_response(),
            _translation_response(),  # translate_answer
        ]
    )


def test_question_section_with_no_generation_says_not_generated_and_invents_nothing() -> None:
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="backlog -- no gold citation",
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=None,
    )
    text = "\n".join(lines)
    assert "NOT GENERATED" in text
    assert "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।" in text
    # Nothing that looks like a fabricated Hindi answer should appear.
    assert "वापसी आवश्यक है" not in text


def test_question_section_with_a_working_adapter_renders_the_translated_claim(
    tmp_path: Path,
) -> None:
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="verified",
        coverage={"hi": review_translations.LanguageCoverage(3, 9)},
        total_terms=12,
        glossary=_glossary(),
        generation=_generation(tmp_path, _working_adapter()),
    )
    text = "\n".join(lines)
    assert "वापसी आवश्यक है" in text  # translated answer
    assert "A refund is required." in text  # English answer, for comparison
    assert "my landlord kept my deposit" in text  # English query used for retrieval
    assert "NOT GENERATED" not in text
    assert "GENERATION FAILED" not in text


def test_question_section_reports_a_per_question_failure_without_crashing(
    tmp_path: Path,
) -> None:
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="backlog -- no gold citation",
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=_generation(
            tmp_path,
            StubAdapter([_safe_classifier_response()]),
            _RaisingRetriever(),
        ),
    )
    text = "\n".join(lines)
    assert "GENERATION FAILED" in text


def test_question_section_asks_for_the_declared_language_explicitly(
    tmp_path: Path,
) -> None:
    """The sheet labels each row with the language its source file declares.
    Passing that choice into `answer_in_language` is what makes the label
    true: detection maps all Devanagari to Hindi, so a Marathi row would
    otherwise be reviewed as Marathi while holding a Hindi answer."""
    adapter = StubAdapter(
        [
            _safe_classifier_response(),
            "my landlord kept my deposit",
            _generation_response(),
            json.dumps(
                {"summary": "ठेव.", "claims": [{"id": 0, "text": "परतावा आवश्यक आहे."}]},
                ensure_ascii=False,
            ),
        ]
    )
    lines = review_translations._question_section(
        question="माझ्या घरमालकाने ठेव परत केली नाही",
        language="mr",
        category="tenancy",
        source_label="backlog -- no gold citation",
        coverage={},
        total_terms=12,
        glossary=_marathi_glossary(),
        generation=_generation(tmp_path, adapter),
    )
    text = "\n".join(lines)

    assert "Marathi" in adapter.calls[-1]["system"]  # Marathi glossary rules
    assert "परतावा आवश्यक आहे." in text
    assert "**Answer language:** Marathi" in text


def test_question_section_persists_the_events_the_turn_produced(
    tmp_path: Path,
) -> None:
    """Binding requirement: a run of hallucinations or a classifier outage on
    the multilingual path must be discoverable after the fact. The events are
    built inside the pipeline and persisted here, at the boundary that holds
    the `Settings`."""
    settings = _settings(tmp_path)
    generation = review_translations.Generation(
        adapter=_GhostCitationAdapter(),
        registry=ModelRegistry(),
        retriever=_Retriever(),
        settings=settings,
    )
    review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="verified",
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=generation,
    )

    assert settings.hallucination_log_path.read_text(encoding="utf-8").strip()
    assert settings.classifier_failure_log_path.read_text(encoding="utf-8").strip()


# --- build_sheet ----------------------------------------------------------


def _verified_question(language: str) -> EvalQuestion:
    return EvalQuestion(
        question="q", gold_chunk_ids=("a",), language=language, category="tenancy"
    )


def test_build_sheet_states_the_verified_and_backlog_counts_honestly() -> None:
    sheet = review_translations.build_sheet(
        verified=(_verified_question("en"),),
        backlog=(),
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=None,
    )
    assert "Verified evaluation questions: 1" in sheet
    assert "0 are in a language other than English" in sheet


def test_build_sheet_says_so_plainly_when_there_is_nothing_non_english_to_review() -> None:
    sheet = review_translations.build_sheet(
        verified=(_verified_question("en"),),
        backlog=(),
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=None,
    )
    assert "No non-English question is available to review" in sheet


def test_build_sheet_includes_a_backlog_row_labelled_as_unverified() -> None:
    backlog = review_translations.BacklogQuestion(
        question="दहेज के लिए मुझे परेशान किया जा रहा है।",
        language="hi",
        category="family",
    )
    sheet = review_translations.build_sheet(
        verified=(),
        backlog=(backlog,),
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=None,
    )
    assert "backlog" in sheet.lower()
    assert "दहेज के लिए मुझे परेशान किया जा रहा है।" in sheet


def test_build_sheet_notes_when_no_api_key_is_configured() -> None:
    backlog = review_translations.BacklogQuestion(
        question="q", language="hi", category="family"
    )
    sheet = review_translations.build_sheet(
        verified=(),
        backlog=(backlog,),
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        generation=None,
    )
    assert "OPENLAW_ANTHROPIC_API_KEY" in sheet
