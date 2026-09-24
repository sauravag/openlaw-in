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

from openlaw.answers.models import ModelRegistry, StubAdapter
from openlaw.answers.validate import Claim, Source, ValidatedAnswer
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
    assert coverage == {"hi": 2, "mr": 1}


# --- _coverage_note -----------------------------------------------------------


def test_coverage_note_flags_a_language_with_zero_glossary_terms() -> None:
    note = review_translations._coverage_note("te", {"hi": 12}, 12)
    assert "0 of 12" in note
    assert "No glossary backs this language" in note


def test_coverage_note_reports_partial_coverage() -> None:
    note = review_translations._coverage_note("mr", {"mr": 2, "hi": 12}, 12)
    assert "2 of 12" in note


def test_coverage_note_uses_the_language_display_name() -> None:
    note = review_translations._coverage_note("hi", {"hi": 12}, 12)
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


def test_question_section_with_no_adapter_says_not_generated_and_invents_nothing() -> None:
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="backlog -- no gold citation",
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        adapter=None,
        registry=None,
        retriever=None,
    )
    text = "\n".join(lines)
    assert "NOT GENERATED" in text
    assert "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।" in text
    # Nothing that looks like a fabricated Hindi answer should appear.
    assert "वापसी आवश्यक है" not in text


def test_question_section_with_a_working_adapter_renders_the_translated_claim() -> None:
    adapter = StubAdapter(
        [
            _safe_classifier_response(),
            "my landlord kept my deposit",  # translate_query
            _generation_response(),
            _translation_response(),  # translate_answer
        ]
    )
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="verified",
        coverage={"hi": 12},
        total_terms=12,
        glossary=_glossary(),
        adapter=adapter,
        registry=ModelRegistry(),
        retriever=_Retriever(),
    )
    text = "\n".join(lines)
    assert "वापसी आवश्यक है" in text  # translated answer
    assert "A refund is required." in text  # English answer, for comparison
    assert "my landlord kept my deposit" in text  # English query used for retrieval
    assert "NOT GENERATED" not in text
    assert "GENERATION FAILED" not in text


def test_question_section_reports_a_per_question_failure_without_crashing() -> None:
    lines = review_translations._question_section(
        question="मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है।",
        language="hi",
        category="tenancy",
        source_label="backlog -- no gold citation",
        coverage={},
        total_terms=12,
        glossary=_glossary(),
        adapter=StubAdapter([_safe_classifier_response()]),
        registry=ModelRegistry(),
        retriever=_RaisingRetriever(),
    )
    text = "\n".join(lines)
    assert "GENERATION FAILED" in text


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
        adapter=None,
        registry=None,
        retriever=None,
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
        adapter=None,
        registry=None,
        retriever=None,
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
        adapter=None,
        registry=None,
        retriever=None,
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
        adapter=None,
        registry=None,
        retriever=None,
    )
    assert "OPENLAW_ANTHROPIC_API_KEY" in sheet
