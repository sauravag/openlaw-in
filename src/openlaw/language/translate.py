"""Query and answer translation.

Translation sits at both edges of the pipeline and never in the middle. The
query is translated into English before retrieval; the answer is translated
out of English only after the citation validator has run
(`openlaw.answers.validate.validate_answer`). Because validation has already
finished by the time `translate_answer` is ever called, translation cannot
introduce an unverified citation or change which sources an answer cites:
`chunk_ids` and `source_url`s are copied onto the result verbatim and are
never sent through the model as translatable prose, `sources` is passed
through untouched, and a translator that returns the wrong number of claims
(so its output cannot be aligned back to the original `chunk_ids`) is
rejected outright in favour of the English answer -- see
`test_claim_count_mismatch_falls_back_to_english`.

**Binding correction to query-translation gating (overrides the original
task brief).** The brief gated `translate_query` on the caller-supplied
`language` and skipped the model call whenever it was `"en"`. That is wrong
given a measured, honestly-reported gap in `openlaw.language.detect`: a
question typed in Latin-script transliteration of an Indian language
("Hinglish", e.g. "mera pati mujhe maarta hai") carries no Indic Unicode
signal, and `langdetect` has no romanised-Hindi model, so such text silently
resolves to `"en"` -- indistinguishable, at the detector, from a genuinely
English question. Gating translation on that verdict would skip it for
exactly the Hinglish questions that most need it, sending them straight into
an English-only corpus. `translate_query` therefore no longer skips the
model call based on the detected language: it always asks the translator,
and the system prompt instructs the translator itself to return
already-English text unchanged. A genuinely English question now costs one
cheap no-op call on the TRANSLATOR-role model (Haiku by default) instead of
being silently mis-served when detection was wrong. The only remaining
short-circuit is empty/whitespace-only text, which has nothing to translate
either way.

`translate_answer` keeps the brief's original gate on `language == "en"`:
the answer's target language is a caller decision (typically the same
language surfaced to the user for the whole turn), not a per-message
detector verdict, so it does not carry the same blind spot.

Every failure path degrades to English (or to the original text, for
queries). An English answer is useful; a mangled one is worse than none.
"""

import json

from openlaw.answers.models import LLMAdapter, ModelRegistry, ModelRole
from openlaw.answers.schema import Claim
from openlaw.answers.validate import ValidatedAnswer
from openlaw.language.detect import SUPPORTED_LANGUAGES
from openlaw.language.glossary import Glossary

MAX_TRANSLATION_TOKENS = 2048

QUERY_SYSTEM_PROMPT = """\
You translate legal questions from Indian languages, including Hinglish and \
other Latin-script transliterations of Indian languages, into English so \
they can be searched against a corpus of Indian legislation and court \
judgments.

If the question is already written in English, return it exactly as given, \
unchanged. Otherwise translate it faithfully into English. Preserve every \
legal term, statute name, section number and proper noun. Do not answer the \
question, do not add context, and do not correct the user. Return only the \
English text (the translation, or the original if it was already English) \
as plain text, with no commentary."""

ANSWER_SYSTEM_PROMPT = """\
You translate legal information from English into {language} for an ordinary \
person with no legal training.

Rules:
- Translate the meaning, not word by word. The reader must be able to act on it.
- Keep statute names, section numbers, and case citations in English.
- Never invent, add, or remove any legal content.
- Return exactly the same number of claims you were given, in the same order.

{glossary}

Return only a JSON object of the form:
{{"summary": "...", "claims": ["...", "..."]}}"""


def translate_query(
    text: str, language: str, adapter: LLMAdapter, registry: ModelRegistry
) -> str:
    """Translate `text` into English.

    Deliberately does not gate this call on `language`: see the module
    docstring for why the detector's "en" verdict cannot be trusted to mean
    "already English" (it also covers Hinglish and other transliterations).
    The translator is always asked, and is instructed to return
    already-English text unchanged, so the one case that does not need
    translating still costs only a cheap no-op call.

    Falls back to the original text on any failure -- an empty completion --
    so a translation problem never loses the user's question.
    """
    if not text.strip():
        return text

    completion = adapter.complete(
        model=registry.for_role(ModelRole.TRANSLATOR, language=language),
        system=QUERY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        output_schema=None,
        max_tokens=MAX_TRANSLATION_TOKENS,
    )
    return completion.text.strip() or text


def _parse_translation(
    payload: str, expected_claims: int
) -> tuple[str, list[str]] | None:
    """Return `(summary, claim_texts)` parsed from `payload`, or None on any
    failure -- malformed JSON, missing keys, wrong types, or a claim count
    that does not match `expected_claims` and so cannot be aligned back to
    the original claims' `chunk_ids`."""
    try:
        translated = json.loads(payload)
        summary = str(translated["summary"])
        claim_texts = [str(text) for text in translated["claims"]]
    except (TypeError, KeyError, ValueError):
        return None

    if len(claim_texts) != expected_claims:
        return None
    return summary, claim_texts


def translate_answer(
    answer: ValidatedAnswer,
    language: str,
    adapter: LLMAdapter,
    registry: ModelRegistry,
    glossary: Glossary,
) -> ValidatedAnswer:
    """Translate a validated answer into `language`.

    Only `unverified_summary` and each claim's `text` are ever sent to the
    model as translatable prose. `claims[].chunk_ids`, `sources` (including
    every `source_url`), `dropped` and `is_answerable` are copied onto the
    result verbatim -- translation runs strictly after validation
    (`openlaw.answers.validate.validate_answer`) and has no path to alter
    which sources an answer cites. Any failure -- a refusal, malformed JSON,
    or a claim count that cannot be aligned back to the original claims --
    returns the English `answer` unchanged: a mangled translation is worse
    than none.

    A no-op when `language` is English or the answer has no claims (an
    unanswerable result carries no claims and a `None` summary, so there is
    nothing to translate).
    """
    if language == "en" or not answer.claims:
        return answer

    system = ANSWER_SYSTEM_PROMPT.format(
        language=SUPPORTED_LANGUAGES.get(language, language),
        glossary=glossary.instruction_for(language),
    )
    payload = json.dumps(
        {
            "summary": answer.unverified_summary or "",
            "claims": [claim.text for claim in answer.claims],
        },
        ensure_ascii=False,
    )

    completion = adapter.complete(
        model=registry.for_role(ModelRole.TRANSLATOR, language=language),
        system=system,
        messages=[{"role": "user", "content": [{"type": "text", "text": payload}]}],
        output_schema=None,
        max_tokens=MAX_TRANSLATION_TOKENS,
    )

    parsed = _parse_translation(completion.text, len(answer.claims))
    if parsed is None:
        return answer
    summary, claim_texts = parsed

    return ValidatedAnswer(
        unverified_summary=summary,
        claims=tuple(
            Claim(text=text, chunk_ids=claim.chunk_ids)
            for text, claim in zip(claim_texts, answer.claims, strict=True)
        ),
        sources=answer.sources,
        dropped=answer.dropped,
        is_answerable=answer.is_answerable,
    )
