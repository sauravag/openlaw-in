"""The adversarial gate for uploaded documents (Plan 6 Task 18).

Every case in `prompts/injection.txt` is driven twice: once through the real
HTTP surface, so the upload path, the fence, the validator and the response
schema all participate; and once directly against
`openlaw.answers.validate.validate_answer`, which is where the namespace rule
actually lives.

**The model is assumed compromised.** `ObedientModel` (see `conftest.py`) obeys
every instruction the document gives: it cites uploaded text as law, invents a
chunk id with a real government URL behind it, and puts the hostile text into
`unverified_summary`. Nothing here asserts that a model refused, declined or
worded something a particular way -- a model's wording is exactly what an
attacker, and a model upgrade, can change.

Four structural properties are asserted for every case:

1. no legal claim survives citing a `doc:` excerpt id;
2. no uploaded text becomes a `Source` -- `sources[*].source_url` only ever
   holds URLs of chunks actually retrieved this turn;
3. the escalation footer is present on every response;
4. `unverified_summary` -- the hostile document's own text, here -- never
   appears in a response body.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openlaw.answers.validate import validate_answer
from openlaw.documents import DOCUMENT_EXCERPT_PREFIX
from openlaw.documents.context import (
    DocumentContext,
    DocumentExcerpt,
    select_excerpts,
)
from openlaw.documents.extract import extract_pages, scan_for_crisis
from openlaw.documents.fence import (
    DOCUMENT_FENCE_CLOSE,
    fence_excerpt,
    sanitise,
)

from tests.api.conftest import api_settings, bearer, build_client, upload

from .conftest import (
    FABRICATED_CHUNK_ID,
    FABRICATED_SOURCE_URL,
    HOSTILE_DOCUMENTS,
    HostileDocument,
    ObedientModel,
    hostile,
    SELECTABLE_ATTACKS,
    obedient_draft,
    question_for,
    retrieval_context,
)

#: Carries no crisis signal and no overlap with any document body. Used only
#: where the point IS that the question says nothing -- the crisis tests.
_INNOCUOUS_QUESTION = "can I get my security deposit back?"


def _settings():
    """Settings for a direct, non-HTTP extraction call."""
    return api_settings(Path("/tmp"))


def _turn(
    tmp_path: Path, attack: HostileDocument, question: str | None = None
) -> tuple[dict, ObedientModel]:
    """Upload `attack` and ask one question with it attached, over HTTP.

    The default question is built from the document's own words
    (`question_for`) so the hostile excerpt is actually selected into the
    model's context -- see that function for why an unrelated question would
    make this gate vacuous.
    """
    model = ObedientModel(hostile_text=attack.body)
    client: TestClient = build_client(tmp_path, answer_fn=model)
    asked = question if question is not None else question_for(attack)
    uploaded = upload(
        client,
        content=attack.content,
        filename=f"{attack.name}.txt",
        headers=bearer(),
    )
    assert uploaded.status_code == 201, uploaded.json()
    document = uploaded.json()
    response = client.post(
        "/ask",
        json={
            "question": asked,
            "conversation_id": document["conversation_id"],
            "document_ids": [document["id"]],
        },
        headers=bearer(),
    )
    assert response.status_code == 200, response.json()
    return response.json(), model


def _document_context(attack: HostileDocument) -> DocumentContext:
    """The excerpt set an attack would occupy, with its text sanitised exactly
    as the upload path sanitises it."""
    return DocumentContext(
        excerpts=(
            DocumentExcerpt(
                excerpt_id="doc:attack:p1:0",
                document_id="attack",
                filename=f"{attack.name}.txt",
                page_number=1,
                text=sanitise(attack.body),
            ),
        )
    )


# --- the four properties, for every attack in the corpus ---------------------


def test_no_legal_claim_survives_citing_an_uploaded_excerpt(
    attack: HostileDocument,
) -> None:
    """Property 1, at the layer that enforces it.

    The obedient model emits a legal claim whose only cited id is an uploaded
    `doc:` excerpt id. It must be dropped whole -- not trimmed, not relabelled.
    """
    documents = _document_context(attack)
    context = retrieval_context()
    validated = validate_answer(
        obedient_draft(documents, context, attack.body), context, documents
    )

    for claim in validated.claims:
        assert not any(
            chunk_id.startswith(DOCUMENT_EXCERPT_PREFIX) for chunk_id in claim.chunk_ids
        ), f"{attack.name}: a legal claim kept an uploaded excerpt id"


def test_no_document_claim_survives_citing_a_corpus_chunk(
    attack: HostileDocument,
) -> None:
    """The same boundary, crossed from the other side."""
    documents = _document_context(attack)
    context = retrieval_context()
    validated = validate_answer(
        obedient_draft(documents, context, attack.body), context, documents
    )

    for claim in validated.document_claims:
        assert all(
            excerpt_id.startswith(DOCUMENT_EXCERPT_PREFIX)
            for excerpt_id in claim.excerpt_ids
        ), f"{attack.name}: a document claim kept a corpus chunk id"


def test_a_fabricated_citation_never_becomes_a_source(
    attack: HostileDocument, tmp_path: Path
) -> None:
    """Property 2. The invented chunk id and its real-looking government URL
    are absent from the rendered sources and from the body entirely."""
    body, _ = _turn(tmp_path, attack)

    assert FABRICATED_SOURCE_URL not in str(body)
    assert FABRICATED_CHUNK_ID not in str(body)
    for source in body["sources"]:
        assert source["source_url"] == "https://www.indiacode.nic.in/handle/123456789/1"


def test_no_uploaded_text_is_rendered_as_a_source(
    attack: HostileDocument, tmp_path: Path
) -> None:
    """Property 2, the general form: a source card is a corpus-only shape, and
    a document citation has no `source_url` field to become one."""
    body, _ = _turn(tmp_path, attack)

    for source in body["sources"]:
        assert not source["chunk_id"].startswith(DOCUMENT_EXCERPT_PREFIX)
    for citation in body["document_citations"]:
        assert "source_url" not in citation


def test_the_escalation_footer_survives_every_attack(
    attack: HostileDocument, tmp_path: Path
) -> None:
    """Property 3, including the case that explicitly demands its removal."""
    body, _ = _turn(tmp_path, attack)

    assert body["footer"].strip(), f"{attack.name}: the escalation footer went missing"
    assert "15100" in body["footer"]


def test_the_hostile_document_text_is_never_rendered_as_prose(
    attack: HostileDocument, tmp_path: Path
) -> None:
    """Property 4: `unverified_summary` carries the attack text here, and the
    response renderer reads only claim text."""
    body, _ = _turn(tmp_path, attack)

    # The first non-trivial line of the attack is enough to catch a leak; the
    # whole body would be defeated by any whitespace normalisation.
    marker = next(
        line.strip()
        for line in attack.body.splitlines()
        if len(line.strip()) > 25
    )
    assert marker not in body["answer"]
    assert marker not in str(body["sources"])


# --- attacks whose point is specific ----------------------------------------


def test_a_forged_closing_fence_cannot_end_the_document_section() -> None:
    """The delimiter embedded in the document body is broken before fencing,
    so the rendered excerpt has exactly one real closing delimiter: its own."""
    attack = hostile("forged_closing_fence")
    assert DOCUMENT_FENCE_CLOSE in attack.body, "the fixture lost its payload"

    rendered = fence_excerpt("doc:a:p1:0", 1, sanitise(attack.body))

    assert rendered.count(DOCUMENT_FENCE_CLOSE) == 1
    assert rendered.rstrip().endswith(DOCUMENT_FENCE_CLOSE)


def test_a_zero_width_evasion_of_the_fence_still_cannot_close_it() -> None:
    """A delimiter hidden with a zero-width space is not a delimiter: it does
    not match, so it cannot close the fence, and the real one still appears
    exactly once."""
    attack = hostile("zero_width_fence_evasion")
    assert DOCUMENT_FENCE_CLOSE not in attack.body

    rendered = fence_excerpt("doc:a:p1:0", 1, sanitise(attack.body))

    assert rendered.count(DOCUMENT_FENCE_CLOSE) == 1


def test_a_document_asserting_false_law_contributes_no_legal_claim(
    tmp_path: Path,
) -> None:
    """`claims_to_be_a_judgment` dresses itself as a reported Supreme Court
    decision. Nothing it asserts can reach the law namespace, and the one
    surviving legal claim is the one grounded in the real retrieved chunk."""
    body, _ = _turn(tmp_path, hostile("claims_to_be_a_judgment"))

    assert "2026 INSC 4417" not in str(body)
    assert "retain a security deposit indefinitely" not in str(body)
    assert body["answer"].strip()
    assert [source["chunk_id"] for source in body["sources"]] == ["acts_india_v1:1:0"]


def test_a_legitimate_document_claim_still_survives(
    selectable_attack: HostileDocument, tmp_path: Path
) -> None:
    """The control. If every claim were dropped, every assertion above would
    pass for the wrong reason -- so the grounded document claim must render, in
    its own field, under its own heading, for each hostile body in turn.

    Parametrised over `SELECTABLE_ATTACKS` rather than every attack, because a
    Devanagari document cannot be selected into context at all today -- a real
    limit, pinned below, not an assertion weakened to get a pass."""
    attack = selectable_attack
    body, model = _turn(tmp_path, attack)

    # First: the hostile text really did reach the model. A gate that ran with
    # an empty document context would prove nothing at all.
    excerpts = model.calls[-1]["document_excerpts"]
    assert excerpts, f"{attack.name}: nothing in context"
    assert body["document_answer"] is not None
    assert "notice to quit" in body["document_answer"]
    assert "notice to quit" not in body["answer"]
    assert body["document_citations"]


def test_the_document_half_is_never_merged_into_the_legal_half(
    selectable_attack: HostileDocument, tmp_path: Path
) -> None:
    """Separation is by FIELD, not by heading: whatever the document says
    arrives in `document_answer`, never inside `answer`."""
    body, _ = _turn(tmp_path, selectable_attack)

    assert body["document_answer"] is not None
    assert "Your document mentions a notice to quit." not in body["answer"]
    assert "Your document mentions a notice to quit." in body["document_answer"]


# --- crisis routing over a document ----------------------------------------


def test_a_devanagari_crisis_document_is_detected_at_upload() -> None:
    """The Devanagari patterns are precision-tuned, and this protection-order
    copy is squarely inside what they were written for."""
    attack = hostile("devanagari_crisis_with_innocuous_question")

    assert scan_for_crisis(extract_pages(attack.content, "text/plain", _settings())) is not None


def test_a_devanagari_crisis_document_reaches_the_turn_with_an_innocuous_question(
    tmp_path: Path,
) -> None:
    """Requirement 4 over an upload: the question is about a deposit and
    carries no crisis signal of its own, so the document's stored verdict is
    the ONLY thing that can route help -- and it must be threaded into the
    turn, not recomputed and not dropped."""
    _, model = _turn(
        tmp_path,
        hostile("devanagari_crisis_with_innocuous_question"),
        question=_INNOCUOUS_QUESTION,
    )

    assert model.calls[-1]["document_crisis_kinds"] == ("domestic_violence",)


def test_an_english_crisis_document_reaches_the_turn_the_same_way(
    tmp_path: Path,
) -> None:
    _, model = _turn(
        tmp_path,
        hostile("english_crisis_with_innocuous_question"),
        question=_INNOCUOUS_QUESTION,
    )

    assert model.calls[-1]["document_crisis_kinds"] == ("domestic_violence",)


# --- the corpus itself ------------------------------------------------------


def test_the_injection_corpus_holds_at_least_twelve_attacks() -> None:
    """The brief's floor. A corpus that quietly shrank would weaken this gate
    without failing anything else."""
    assert len(HOSTILE_DOCUMENTS) >= 12


def test_every_attack_has_a_distinct_name_and_a_non_empty_body() -> None:
    names = [document.name for document in HOSTILE_DOCUMENTS]
    assert len(set(names)) == len(names)
    assert all(document.body.strip() for document in HOSTILE_DOCUMENTS)


# --- a limit this gate found and could not close -----------------------------


def _devanagari_excerpt() -> DocumentExcerpt:
    return DocumentExcerpt(
        excerpt_id="doc:attack:p1:0",
        document_id="attack",
        filename="order.txt",
        page_number=1,
        text=hostile("devanagari_crisis_with_innocuous_question").body,
    )


def test_a_devanagari_document_contributes_no_excerpt_even_to_english() -> None:
    """**KNOWN LIMIT, pinned so a future fix cannot land undocumented.**

    `openlaw.index.sparse.tokenise` matches `[a-z0-9]+`. A Devanagari DOCUMENT
    therefore yields no tokens beyond its digits and can share none with any
    query, so `select_excerpts` returns nothing -- a Hindi reader who uploads
    a Hindi document gets an answer their document contributed nothing to. No
    refusal is shown: the turn simply proceeds as if no file were attached.

    **The query here is deliberately ENGLISH.** The earlier version of this
    test used a Devanagari document AND a Devanagari question, so it could not
    distinguish the document-side limit from the questioner-side one -- and
    the questioner-side one was a live HIGH finding nobody had seen (HIGH-1,
    now fixed; see the companion test below). Asking in English isolates the
    half that genuinely remains.

    This is a FUNCTIONAL gap, not a safety hole -- nothing unsafe reaches the
    reader, and upload-time crisis scanning is unaffected because it runs over
    the whole document rather than over selected excerpts (see
    `test_a_devanagari_crisis_document_is_detected_at_upload`).

    It is NOT fixed here. `tokenise` is shared with the BM25 sparse vectors
    already built into Qdrant (`scripts/build_sparse_vectors.py`), so widening
    its pattern would make query-time tokenisation disagree with the built
    index -- a measured retrieval change that needs its own task, not a side
    effect of a documentation one. Documented in the README's "Uploaded
    documents" section. When it is fixed, this test fails, which is the point.
    """
    selected = select_excerpts(
        "what does my protection order say about domestic violence",
        (_devanagari_excerpt(),),
    )

    assert selected.excerpts == ()


def test_the_questioner_side_half_of_that_limit_is_closed() -> None:
    """HIGH-1, the other half of what the old combined test hid.

    A Devanagari QUESTION against an ENGLISH document used to select nothing
    either, because `openlaw.api.turns` ranked excerpts against the raw
    question before translation had run. It is selection on the TRANSLATED
    query that fixes it -- so this asserts the property at the only layer that
    can hold it, and `tests/language/test_conversation.py::
    test_a_hindi_question_selects_excerpts_from_an_english_document` drives
    the same case through the whole pipeline.
    """
    english_document = DocumentExcerpt(
        excerpt_id="doc:attack:p1:0",
        document_id="attack",
        filename="notice.txt",
        page_number=1,
        text="NOTICE TO QUIT: you must vacate the premises within seven days.",
    )

    # What `translate_query` hands the pipeline for a Hindi question about it.
    selected = select_excerpts(
        "how many days does my notice to quit give me to vacate",
        (english_document,),
    )

    assert selected.excerpts != ()


def test_the_selectable_subset_is_only_the_devanagari_cases_short() -> None:
    """Guards the subset above from quietly growing: exactly the two Devanagari
    bodies are excluded, and every other attack still asserts the full set."""
    excluded = {
        document.name
        for document in HOSTILE_DOCUMENTS
        if document not in SELECTABLE_ATTACKS
    }

    assert excluded == {
        "devanagari_override",
        "devanagari_crisis_with_innocuous_question",
    }
