"""Query and answer translation.

Translation sits at both edges of the pipeline and never in the middle. The
query is translated into English before retrieval; the answer is translated
out of English only after the citation validator has run
(`openlaw.answers.validate.validate_answer`). Because validation has already
finished by the time `translate_answer` is ever called, translation cannot
introduce an unverified citation or change which sources an answer cites:
`chunk_ids` and `source_url`s are copied onto the result verbatim and are
never sent through the model as translatable prose, and `sources` is passed
through untouched.

**Claims are reassembled by identity, not position.** Each claim is sent to
the translator tagged with a stable `id` (its index into `answer.claims`),
and the translator is required to echo that same `id` back with its
translated text. Reassembly looks up each original claim's `chunk_ids` by
that `id`, never by the position of the item in the response array. A
translator that reorders claims in its response is therefore handled
correctly -- translated text still lands on the `chunk_ids` of the claim it
was actually given, not on whichever claim happens to occupy that slot in
the reply. Binding on position alone (matching purely by count, so index 0
of the response paired with index 0 of the request regardless of content)
would let a reordering translator silently attach a translated statement to
citations that do not support it -- the same failure the citation validator
exists to prevent, arriving through a different door. If the response is
missing an `id`, invents one that was never sent, or duplicates one, the
whole translation is rejected in favour of the English answer -- see
`test_reordered_claims_reassemble_onto_their_own_chunk_ids`,
`test_missing_claim_id_falls_back_to_english`, and
`test_invented_claim_id_falls_back_to_english`.

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

`translate_answer` keeps the brief's original gate on `language == "en"`,
and that is now honest: the answer's target language really is a caller
decision. `openlaw.language.pipeline.answer_in_language` takes an explicit
`language` parameter that overrides detection outright, so an interface
whose user asked in Hinglish (or picked Marathi, which no detector can
distinguish from Hindi) supplies the target language itself and the gate
never sees the detector's blind spot. Detection is only the fallback when
the caller says nothing; when it is, the gate does inherit the "en" verdict
and a Hinglish speaker who was never offered a language selector gets an
English answer to a query that was nonetheless translated for retrieval.

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
- If a claim quotes the words of a statute, keep that quoted statutory text \
in English, exactly as given. The English text is the law; a translation of \
it is not. You may add your own {language} rendering after the quote, in \
brackets, clearly as an explanation -- never in place of the English words.
- Never invent, add, or remove any legal content.
- Each claim you are given carries an "id". Return every claim, each paired \
with its exact original "id" and its translated "text". Do not add, drop, \
duplicate, renumber, or invent an "id" -- copy each one exactly as given. \
You may return claims in any order.

{glossary}

Return only a JSON object of the form:
{{"summary": "...", "claims": [{{"id": 0, "text": "..."}}, ...]}}"""


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
    payload: str, expected_ids: frozenset[int]
) -> tuple[str, dict[int, str]] | None:
    """Return `(summary, text_by_id)` parsed from `payload`, or None on any
    failure.

    Every claim in the response must carry an `id` from `expected_ids`, and
    every id in `expected_ids` must appear exactly once -- a missing id, an
    id that was never sent (invented), or a duplicate id all fail the parse.

    Each claim's `text` must be a genuine, non-blank string. A missing or
    empty `text` fails the parse rather than reaching `Claim(text="")`, whose
    `ValidationError` would escape `translate_answer` and
    `answer_in_language` entirely and turn one lazy model response into a
    crashed answer. A non-string `text` fails it too rather than being
    coerced with `str()`: `{"x": 1}` rendered as the claim text `"{'x': 1}"`
    would be text nobody wrote or validated, bound to real `chunk_ids` and
    displayed wearing a real citation.
    This is what lets the caller reassemble translated text by identity
    rather than by position: `text_by_id` can be looked up by each original
    claim's own id regardless of what order the response listed claims in.
    """
    try:
        translated = json.loads(payload)
        summary = str(translated["summary"])
        seen_ids: list[int] = []
        text_by_id: dict[int, str] = {}
        for item in translated["claims"]:
            claim_id = int(item["id"])
            text = item["text"]
            if not isinstance(text, str) or not text.strip():
                return None  # empty or non-string text -- see the docstring
            seen_ids.append(claim_id)
            text_by_id[claim_id] = text
    except (TypeError, KeyError, ValueError):
        return None

    if len(seen_ids) != len(set(seen_ids)):
        return None  # a duplicated id -- cannot say which text is authoritative
    if set(seen_ids) != expected_ids:
        return None  # a missing or invented id -- cannot fully reassemble

    return summary, text_by_id


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
    which sources an answer cites.

    Each claim is tagged with a stable `id` (its index into `answer.claims`)
    and reassembled by that id, not by its position in the response -- see
    the module docstring for why binding by position alone would let a
    reordering translator attach translated text to the wrong `chunk_ids`.
    Any failure -- a refusal, malformed JSON, or a response whose ids don't
    exactly match the ids sent (missing, invented, or duplicated) -- returns
    the English `answer` unchanged: a mangled translation is worse than none.

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
            "claims": [
                {"id": index, "text": claim.text}
                for index, claim in enumerate(answer.claims)
            ],
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

    expected_ids = frozenset(range(len(answer.claims)))
    parsed = _parse_translation(completion.text, expected_ids)
    if parsed is None:
        return answer
    summary, text_by_id = parsed

    return ValidatedAnswer(
        unverified_summary=summary,
        claims=tuple(
            Claim(text=text_by_id[index], chunk_ids=claim.chunk_ids)
            for index, claim in enumerate(answer.claims)
        ),
        sources=answer.sources,
        dropped=answer.dropped,
        is_answerable=answer.is_answerable,
    )
