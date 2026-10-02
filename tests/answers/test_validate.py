import pytest
from pydantic import ValidationError

from openlaw.answers.escalation import build_hallucination_events
from openlaw.answers.schema import Claim, DocumentClaim, DraftAnswer
from openlaw.answers.validate import (
    DocumentCitation,
    DroppedClaim,
    RejectedClaim,
    Source,
    ValidatedAnswer,
    rejected_claims,
    rejected_document_claims,
    validate_answer,
)
from openlaw.documents.context import DocumentContext, DocumentExcerpt
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="text",
        source_url=f"https://www.indiacode.nic.in/{chunk_id}",
        score=0.9,
        kind=ChunkKind.PROVISION,
        title="An Act",
    )


def _context(*chunk_ids: str) -> RetrievalContext:
    return RetrievalContext(query="q", chunks=tuple(_chunk(cid) for cid in chunk_ids))


def test_keeps_a_claim_whose_citations_were_all_retrieved() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a", "b")),), sufficient=True
    )
    result = validate_answer(draft, _context("a", "b"))

    assert len(result.claims) == 1
    assert result.dropped == ()


def test_drops_a_claim_citing_a_chunk_that_was_not_retrieved() -> None:
    """This is the hallucination case the whole design exists to catch."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="invented", chunk_ids=("fabricated",)),),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == ("fabricated",)


def test_drops_a_claim_that_mixes_real_and_invented_citations() -> None:
    """Partial grounding is not grounding — the whole claim goes."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="mixed", chunk_ids=("a", "fabricated")),),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == ("fabricated",)


def test_drops_only_the_offending_claim_and_keeps_the_rest() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(
            Claim(text="good", chunk_ids=("a",)),
            Claim(text="bad", chunk_ids=("nope",)),
        ),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert [claim.text for claim in result.claims] == ["good"]
    assert [d.unresolvable_chunk_ids for d in result.dropped] == [("nope",)]


def test_sources_are_built_only_from_surviving_claims() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="good", chunk_ids=("a",)),), sufficient=True
    )
    result = validate_answer(draft, _context("a", "b"))
    assert [source.chunk_id for source in result.sources] == ["a"]


def test_every_source_carries_a_government_url() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )
    for source in validate_answer(draft, _context("a")).sources:
        assert source.source_url.startswith("https://")


def test_sources_are_deduplicated_across_claims() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="one", chunk_ids=("a",)), Claim(text="two", chunk_ids=("a",))),
        sufficient=True,
    )
    assert len(validate_answer(draft, _context("a")).sources) == 1


def test_answer_is_unanswerable_when_every_claim_is_dropped() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="bad", chunk_ids=("nope",)),), sufficient=True
    )
    assert validate_answer(draft, _context("a")).is_answerable is False


def test_answer_is_unanswerable_when_the_model_reports_insufficiency() -> None:
    draft = DraftAnswer(summary="cannot answer", claims=(), sufficient=False)
    assert validate_answer(draft, _context("a")).is_answerable is False


def test_answer_is_unanswerable_when_insufficient_even_with_fully_valid_claims() -> (
    None
):
    """Pins the `and draft.sufficient` term specifically: without a claim that
    is itself fully grounded, a regression to `is_answerable = bool(kept)`
    (dropping the `sufficient` check entirely) would still pass every other
    test in this file, since the only prior insufficiency test used
    claims=(). A model saying "I cannot answer" while still emitting
    well-cited claims must not ship as answerable.
    """
    draft = DraftAnswer(
        summary="cannot answer", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=False
    )
    result = validate_answer(draft, _context("a"))

    assert len(result.claims) == 1
    assert result.is_answerable is False
    assert result.unverified_summary is None


def test_validation_does_not_mutate_the_draft() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )
    validate_answer(draft, _context())
    assert len(draft.claims) == 1


def test_empty_context_drops_everything() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )
    result = validate_answer(draft, _context())

    assert result.claims == ()
    assert result.is_answerable is False


# --- Additional edge cases beyond the brief: duplicates, case/whitespace,
# fully-invalidated summary, and logging of dropped claims. ---


def test_duplicate_chunk_ids_within_a_claim_are_all_valid_when_the_id_is_retrieved() -> (
    None
):
    """A claim citing the same real id twice is not penalized for the duplicate."""
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a", "a")),), sufficient=True
    )
    result = validate_answer(draft, _context("a"))

    assert len(result.claims) == 1
    assert result.dropped == ()


def test_case_variation_in_chunk_id_is_not_treated_as_a_match() -> None:
    """A hallucinated id that differs only in case must NOT be accepted."""
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("A",)),), sufficient=True
    )
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == ("A",)


def test_whitespace_variation_in_chunk_id_is_not_treated_as_a_match() -> None:
    """A hallucinated id with stray whitespace must NOT be accepted."""
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=(" a",)),), sufficient=True
    )
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == (" a",)


def test_prefix_of_a_real_chunk_id_is_not_treated_as_a_match() -> None:
    """A hallucinated id that is a prefix of a real one must NOT be accepted."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="c", chunk_ids=("IND_central_00042_s012",)),),
        sufficient=True,
    )
    result = validate_answer(
        draft, _context("IND_central_00042_s012_p1")
    )

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == ("IND_central_00042_s012",)


def test_homoglyph_chunk_id_is_not_treated_as_a_match() -> None:
    """A hallucinated id substituting a visually-identical Cyrillic letter for
    a Latin one must NOT be accepted. `IND_central_...` becomes
    `IND_сentral_...` with a Cyrillic 'с' (U+0441) in place of Latin 'c'
    (U+0063) — indistinguishable to a human reader, distinct to `==`."""
    real_id = "IND_central_00042_s012_p1"
    homoglyph_id = "IND_сentral_00042_s012_p1"  # Cyrillic 'с' (U+0441)
    assert real_id != homoglyph_id  # sanity: they really are different strings

    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=(homoglyph_id,)),), sufficient=True
    )
    result = validate_answer(draft, _context(real_id))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == (homoglyph_id,)


def test_nfd_normalized_chunk_id_is_not_treated_as_a_match_for_its_nfc_form() -> None:
    """Unicode allows the same visible character to be encoded two ways: NFC
    ('e' + acute accent as one codepoint, U+00E9) vs NFD ('e', U+0065, plus a
    combining acute accent, U+0301). They render identically but compare
    unequal — and must stay unequal here, since normalizing them would be
    exactly the kind of "close enough" matching this validator must never
    do."""
    import unicodedata

    base = "IND_r\u00e9sum\u00e9_00042"
    nfc_id = unicodedata.normalize("NFC", base)
    nfd_id = unicodedata.normalize("NFD", base)
    assert nfc_id != nfd_id  # sanity: distinct code point sequences
    assert unicodedata.normalize("NFC", nfd_id) == nfc_id  # same rendered text

    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=(nfd_id,)),), sufficient=True
    )
    result = validate_answer(draft, _context(nfc_id))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == (nfd_id,)


def test_zero_width_character_in_chunk_id_is_not_treated_as_a_match() -> None:
    """A hallucinated id with an invisible zero-width space (U+200B) spliced
    in looks identical when printed but must NOT be accepted as the real
    id."""
    real_id = "IND_central_00042_s012_p1"
    zero_width_id = real_id + "\u200b"
    assert zero_width_id != real_id  # sanity: distinct code point sequences

    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=(zero_width_id,)),), sufficient=True
    )
    result = validate_answer(draft, _context(real_id))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == (zero_width_id,)


def test_fully_invalidated_answer_clears_unverified_summary() -> None:
    """A fully-dropped answer is not silently emptied — every dropped claim is
    recorded rather than discarded — but `unverified_summary` is set to None,
    not preserved. The model's raw prose is exactly the unchecked channel a
    hallucination could hide in (it carries no chunk_ids and this validator
    cannot check it), so once the answer is unanswerable nothing renderable
    survives: a careless `{{ answer.unverified_summary }}` template shows
    nothing rather than the hallucinated text.
    """
    draft = DraftAnswer(
        summary="You are entitled to X under section Y.",
        claims=(Claim(text="bad", chunk_ids=("nope",)),),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.unverified_summary is None
    assert result.is_answerable is False
    assert result.claims == ()
    assert result.sources == ()
    assert len(result.dropped) == 1


def test_answerable_result_carries_the_summary_through() -> None:
    """When the answer IS answerable, `unverified_summary` does hold the
    model's prose — it is only cleared on the unanswerable path. It remains
    unchecked (see module docstring): callers must build the user-visible
    answer from `claims`/`sources`, never from this field.
    """
    draft = DraftAnswer(
        summary="Some prose.",
        claims=(Claim(text="c", chunk_ids=("a",)),),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.unverified_summary == "Some prose."
    assert result.is_answerable is True


def test_multiple_claims_each_citing_multiple_invalid_ids_are_all_dropped() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(
            Claim(text="bad1", chunk_ids=("x", "y")),
            Claim(text="bad2", chunk_ids=("z",)),
        ),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert len(result.dropped) == 2
    assert result.dropped[0].unresolvable_chunk_ids == ("x", "y")
    assert result.dropped[1].unresolvable_chunk_ids == ("z",)


def test_logs_a_warning_when_a_claim_is_dropped(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="invented", chunk_ids=("fabricated",)),),
        sufficient=True,
    )
    with caplog.at_level(logging.WARNING, logger="openlaw.answers.validate"):
        validate_answer(draft, _context("a"))

    assert any("fabricated" in record.message for record in caplog.records)


def test_does_not_log_when_no_claim_is_dropped(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    draft = DraftAnswer(
        summary="s", claims=(Claim(text="good", chunk_ids=("a",)),), sufficient=True
    )
    with caplog.at_level(logging.WARNING, logger="openlaw.answers.validate"):
        validate_answer(draft, _context("a"))

    assert caplog.records == []


def test_answer_with_no_claims_at_all_is_unanswerable() -> None:
    draft = DraftAnswer(summary="s", claims=(), sufficient=True)
    result = validate_answer(draft, _context("a"))

    assert result.claims == ()
    assert result.dropped == ()
    assert result.is_answerable is False


def test_sources_preserve_first_seen_order_across_claims() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(
            Claim(text="one", chunk_ids=("b",)),
            Claim(text="two", chunk_ids=("a", "b")),
        ),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a", "b"))

    assert [source.chunk_id for source in result.sources] == ["b", "a"]


def test_source_title_is_carried_through_from_the_chunk() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )
    result = validate_answer(draft, _context("a"))

    assert result.sources[0].title == "An Act"


class _DriftedContext:
    """A stand-in for `RetrievalContext` whose `by_id` disagrees with
    `chunk_ids` — a violation of the confirmed invariant that both derive
    from the same underlying chunks. Used only to exercise the defensive
    guard in `_build_sources`, never expected in production."""

    def __init__(self, chunk_ids: frozenset[str]) -> None:
        self._chunk_ids = chunk_ids

    @property
    def chunk_ids(self) -> frozenset[str]:
        return self._chunk_ids

    def by_id(self, chunk_id: str) -> None:  # noqa: ARG002
        return None


def test_source_is_skipped_without_crashing_if_the_membership_invariant_is_ever_violated() -> (
    None
):
    """Defensive guard: `_build_sources` never crashes even if a chunk_id
    that passed membership somehow cannot be resolved back to a chunk. The
    claim itself is still kept (it was validly grounded), but no source is
    emitted for the unresolvable chunk — this is a defense against a broken
    invariant elsewhere, not the primary hallucination check itself.
    """
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )
    result = validate_answer(draft, _DriftedContext(frozenset({"a"})))  # type: ignore[arg-type]

    assert len(result.claims) == 1
    assert result.sources == ()


# --- rejected prose never rides along on the renderable answer --------------
#
# `ValidatedAnswer.dropped[].text` used to hold the verbatim text of a claim
# that FAILED validation, preserved unconditionally -- a third route for
# unverified model prose to reach a user, one level down from the `summary`
# bypass already closed. The rejected prose now lives only in
# `RejectedClaim`, which goes straight to the log-only, 0600-hardened
# `HallucinationEvent` and is never attached to `ValidatedAnswer`.


def test_dropped_claims_on_the_answer_carry_no_model_prose() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="invented prose", chunk_ids=("ghost",)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"))

    assert result.dropped[0].unresolvable_chunk_ids == ("ghost",)
    assert not hasattr(result.dropped[0], "text")
    assert "invented prose" not in result.model_dump_json()


def test_rejected_claims_carry_the_verbatim_text_for_the_log() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(
            Claim(text="good", chunk_ids=("a",)),
            Claim(text="invented prose", chunk_ids=("ghost", "a")),
        ),
        sufficient=True,
    )

    rejected = rejected_claims(draft, _context("a"))

    assert len(rejected) == 1
    assert rejected[0].text == "invented prose"
    assert rejected[0].unresolvable_chunk_ids == ("ghost",)


def test_rejected_claims_is_empty_when_every_claim_is_grounded() -> None:
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="good", chunk_ids=("a",)),), sufficient=True
    )

    assert rejected_claims(draft, _context("a")) == ()


# --- the second citation class: the user's own uploaded document -----------
#
# Plan 6 Task 7. A `DocumentClaim` is a statement about the person's own
# paperwork. It is validated by exactly the same standard as a legal claim --
# exact set membership against the ids actually shown to the model this turn
# -- but against a DIFFERENT set (`DocumentContext.excerpt_ids`), and it
# produces a `DocumentCitation`, which has no `source_url` and can never be
# rendered as a source of law.


def _excerpt(excerpt_id: str, text: str = "The rent is Rs 12,000 per month.") -> DocumentExcerpt:
    return DocumentExcerpt(
        excerpt_id=excerpt_id,
        document_id="abc123",
        filename="lease.pdf",
        page_number=2,
        text=text,
    )


def _document_context(*excerpt_ids: str) -> DocumentContext:
    return DocumentContext(excerpts=tuple(_excerpt(eid) for eid in excerpt_ids))


_SHOWN = "doc:abc123:p2:0"
_UNSHOWN = "doc:abc123:p9:4"


def _document_draft(text: str, *excerpt_ids: str, sufficient: bool = True) -> DraftAnswer:
    return DraftAnswer(
        summary="s",
        document_claims=(DocumentClaim(text=text, excerpt_ids=excerpt_ids),),
        sufficient=sufficient,
    )


# -- rule 1 restated for the new namespace: membership, exactly --------------


def test_keeps_a_document_claim_whose_excerpts_were_all_shown() -> None:
    draft = _document_draft("Your lease sets the rent at Rs 12,000.", _SHOWN)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert [claim.text for claim in result.document_claims] == [
        "Your lease sets the rent at Rs 12,000."
    ]
    assert result.dropped == ()


def test_drops_a_document_claim_citing_an_excerpt_that_was_not_shown() -> None:
    """The injection containment this task owns. A fenced document can talk
    the model into a false statement about its own contents; it cannot make
    that statement point at an excerpt nobody put in front of the model.
    This is the `chunk_id` check, in the document namespace."""
    draft = _document_draft("Your lease waives your right to notice.", _UNSHOWN)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert result.document_claims == ()
    assert result.dropped[0].unresolvable_excerpt_ids == (_UNSHOWN,)


def test_drops_a_document_claim_citing_an_excerpt_selected_for_another_turn() -> None:
    """Excerpt ids are stable across turns (`select_excerpts` is
    deterministic), so a real id from an earlier turn's selection is exactly
    the plausible-looking id a model could carry forward. Membership is
    against THIS turn's selection, nothing wider."""
    last_turn = "doc:abc123:p7:1"
    draft = _document_draft("Your lease says you must vacate in 7 days.", last_turn)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert result.document_claims == ()
    assert result.dropped[0].unresolvable_excerpt_ids == (last_turn,)


def test_document_excerpt_membership_is_exact_not_prefix() -> None:
    draft = _document_draft("Your lease says something.", _SHOWN[:-1])

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert result.document_claims == ()


def test_empty_document_context_drops_every_document_claim() -> None:
    """No document was shown this turn, so there is nothing a document claim
    could be checked against -- including on the default-argument path every
    existing caller takes."""
    draft = _document_draft("Your notice is dated 4 March.", _SHOWN)

    result = validate_answer(draft, _context("a"))

    assert result.document_claims == ()
    assert result.dropped[0].unresolvable_excerpt_ids == (_SHOWN,)


# -- rules 2 and 4: a claim that mixes the namespaces is dropped whole -------


def test_drops_a_legal_claim_citing_a_document_excerpt_id() -> None:
    """Rule 2. A `doc:` id in the law namespace is a contamination event, not
    a typo -- the model is trying to source a legal statement to the person's
    own paperwork."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="The law says you may stay.", chunk_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.claims == ()
    assert result.dropped[0].unresolvable_chunk_ids == (_SHOWN,)


def test_drops_a_legal_claim_citing_a_document_id_even_if_it_is_in_the_corpus_set() -> (
    None
):
    """Rule 2 does not lean on the two id spaces happening not to collide. A
    `RetrievalContext` built (wrongly, or maliciously) with a `doc:`-prefixed
    chunk id still cannot launder a document excerpt into the law namespace:
    the prefix check runs before, and independently of, membership."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="The law says you may stay.", chunk_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context(_SHOWN))

    assert result.claims == ()
    assert result.sources == ()
    assert result.is_answerable is False
    assert result.dropped[0].unresolvable_chunk_ids == (_SHOWN,)


def test_drops_a_document_claim_citing_a_corpus_chunk_id() -> None:
    """Rule 4, the mirror image: a statement about the person's document may
    not be sourced to a statute."""
    draft = _document_draft("Your lease says notice is 30 days.", "a")

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.document_claims == ()
    assert result.dropped[0].unresolvable_excerpt_ids == ("a",)


def test_drops_a_legal_claim_that_mixes_a_real_chunk_and_a_real_excerpt() -> None:
    """Both ids exist and both were shown this turn -- and the claim is still
    dropped whole, not trimmed down to its corpus citation. A claim that
    sources one assertion to a statute and the person's lease at once is a
    model confusion, and the half a reader cannot check is precisely the half
    a repair would keep."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="mixed", chunk_ids=("a", _SHOWN)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.claims == ()
    assert result.sources == ()
    assert result.dropped[0].unresolvable_chunk_ids == (_SHOWN,)


def test_drops_a_document_claim_that_mixes_a_real_excerpt_and_a_real_chunk() -> None:
    draft = _document_draft("mixed", _SHOWN, "a")

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.document_claims == ()
    assert result.document_citations == ()
    assert result.dropped[0].unresolvable_excerpt_ids == ("a",)


# -- rule 5: a document claim is never, by itself, a legal answer ------------


def test_document_claims_alone_do_not_make_an_answer_answerable() -> None:
    """"Your notice is dated 4 March" establishes nothing about the law. The
    turn renders under the fixed "we could not find a verifiable answer"
    text, with the document reading as an addendum to it."""
    draft = _document_draft("Your notice is dated 4 March.", _SHOWN)

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert len(result.document_claims) == 1
    assert result.is_answerable is False
    assert result.unverified_summary is None
    assert result.sources == ()


def test_document_claims_ride_along_with_an_answerable_legal_answer() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="Notice is required.", chunk_ids=("a",)),),
        document_claims=(DocumentClaim(text="Your notice is dated 4 March.", excerpt_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.is_answerable is True
    assert len(result.claims) == 1
    assert len(result.document_claims) == 1


def test_a_dropped_legal_claim_is_not_rescued_by_a_surviving_document_claim() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="The law says X.", chunk_ids=("ghost",)),),
        document_claims=(DocumentClaim(text="Your lease says Y.", excerpt_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.claims == ()
    assert result.is_answerable is False
    assert len(result.document_claims) == 1


# -- the citation a document claim produces is not a source of law -----------


def test_document_citation_has_no_source_url_field_at_all() -> None:
    """Not optional, not None -- absent. A document excerpt has no government
    URL, so a `source_url` on this type could only ever be empty, invented,
    or borrowed from a statute. The type system is the mechanism; a
    convention is not."""
    assert "source_url" not in DocumentCitation.model_fields


def test_document_citation_cannot_grow_a_source_url() -> None:
    """`extra="forbid"`, the precedent `DocumentExcerpt` set in Task 6."""
    with pytest.raises(ValidationError):
        DocumentCitation(
            excerpt_id=_SHOWN,
            document_id="abc123",
            filename="lease.pdf",
            page_number=2,
            source_url="https://www.indiacode.nic.in/handle/1",
        )


def test_document_citations_carry_where_the_reader_can_check_the_quote() -> None:
    draft = _document_draft("Your lease sets the rent at Rs 12,000.", _SHOWN)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    citation = result.document_citations[0]
    assert citation.excerpt_id == _SHOWN
    assert citation.filename == "lease.pdf"
    assert citation.page_number == 2
    assert citation.document_id == "abc123"


def test_document_citations_are_built_only_from_surviving_document_claims() -> None:
    draft = _document_draft("Your lease waives notice.", _UNSHOWN)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert result.document_citations == ()


def test_document_citations_are_deduplicated_in_first_seen_order() -> None:
    second = "doc:abc123:p3:0"
    draft = DraftAnswer(
        summary="s",
        document_claims=(
            DocumentClaim(text="one", excerpt_ids=(second,)),
            DocumentClaim(text="two", excerpt_ids=(second, _SHOWN)),
        ),
        sufficient=True,
    )

    result = validate_answer(draft, _context(), _document_context(_SHOWN, second))

    assert [c.excerpt_id for c in result.document_citations] == [second, _SHOWN]


def test_no_document_citation_ever_reaches_the_source_list() -> None:
    """The one question this whole task turns on: can a document excerpt be
    rendered as a source of law? `sources` is the only list a renderer may
    treat as legal provenance."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="Notice is required.", chunk_ids=("a",)),),
        document_claims=(DocumentClaim(text="Your lease says Y.", excerpt_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert [source.chunk_id for source in result.sources] == ["a"]
    assert all(not isinstance(source, DocumentCitation) for source in result.sources)


def test_a_document_claim_contradicting_its_excerpt_still_yields_no_legal_source() -> (
    None
):
    """A crafted document -- or a model that misreads an honest one -- can
    produce a document claim whose text the excerpt does not support. No
    deterministic check can catch that (there is no id to disagree with), so
    the containment is structural instead: the claim is bound to the exact
    excerpt shown, that excerpt is named with its filename and page so the
    reader can check the quote against their own paperwork, it contributes no
    `Source`, no `source_url` appears anywhere on the answer, and it cannot
    make the turn answerable. The assertion is about that structure, never
    about what the model wrote."""
    lease = DocumentContext(
        excerpts=(_excerpt(_SHOWN, text="The tenant shall give one month's notice."),)
    )
    draft = _document_draft("Your lease says you owe six months' rent.", _SHOWN)

    result = validate_answer(draft, _context("a"), lease)

    assert len(result.document_claims) == 1
    assert result.sources == ()
    assert result.is_answerable is False
    assert "source_url" not in result.model_dump_json()
    assert result.document_citations[0].excerpt_id == _SHOWN


# -- the unverified-prose route stays closed --------------------------------


def test_validated_answer_grows_no_document_flavoured_summary() -> None:
    """Plan 3 closed the unverified-prose route and Plan 5 found it half
    reopened. A "document summary" field would be the same hole with a new
    name: the only prose field on this model stays `unverified_summary`, and
    every renderable field is a validated claim or a citation."""
    summary_fields = [
        name for name in ValidatedAnswer.model_fields if "summary" in name
    ]
    assert summary_fields == ["unverified_summary"]


def test_a_document_only_answer_carries_no_renderable_prose_at_all() -> None:
    draft = DraftAnswer(
        summary="You are entitled to stay under section 9.",
        document_claims=(DocumentClaim(text="Your notice is dated 4 March.", excerpt_ids=(_SHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.unverified_summary is None
    assert "section 9" not in result.model_dump_json()


# -- the refusal record: ids only, in the namespace they came from ----------


def test_dropped_document_claims_carry_no_model_prose() -> None:
    draft = _document_draft("invented document prose", _UNSHOWN)

    result = validate_answer(draft, _context(), _document_context(_SHOWN))

    assert not hasattr(result.dropped[0], "text")
    assert "invented document prose" not in result.model_dump_json()


def test_a_dropped_document_claim_is_recorded_in_the_excerpt_namespace() -> None:
    """Excerpt ids never land in a field named for chunk ids. Keeping the two
    apart in the refusal record is the same rule as keeping them apart in the
    answer."""
    draft = _document_draft("t", _UNSHOWN)

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert result.dropped[0].unresolvable_chunk_ids == ()
    assert result.dropped[0].unresolvable_excerpt_ids == (_UNSHOWN,)


def test_both_kinds_of_refusal_are_recorded_on_one_answer() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="bad law", chunk_ids=("ghost",)),),
        document_claims=(DocumentClaim(text="bad doc", excerpt_ids=(_UNSHOWN,)),),
        sufficient=True,
    )

    result = validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert [d.unresolvable_chunk_ids for d in result.dropped] == [("ghost",), ()]
    assert [d.unresolvable_excerpt_ids for d in result.dropped] == [(), (_UNSHOWN,)]


def test_rejected_document_claims_carry_the_verbatim_text_for_the_log() -> None:
    draft = DraftAnswer(
        summary="s",
        document_claims=(
            DocumentClaim(text="good", excerpt_ids=(_SHOWN,)),
            DocumentClaim(text="invented document prose", excerpt_ids=(_UNSHOWN,)),
        ),
        sufficient=True,
    )

    rejected = rejected_document_claims(draft, _document_context(_SHOWN))

    assert len(rejected) == 1
    assert rejected[0].text == "invented document prose"
    assert rejected[0].unresolvable_excerpt_ids == (_UNSHOWN,)
    assert rejected[0].unresolvable_chunk_ids == ()


def test_rejected_claims_is_unchanged_by_the_presence_of_document_claims() -> None:
    """The corpus log path keeps exactly its old meaning: it reports refused
    LEGAL claims and nothing else, so no excerpt id can ever reach
    `HallucinationEvent.dropped_chunk_ids`."""
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="bad law", chunk_ids=("ghost",)),),
        document_claims=(DocumentClaim(text="bad doc", excerpt_ids=(_UNSHOWN,)),),
        sufficient=True,
    )

    rejected = rejected_claims(draft, _context("a"))

    assert [claim.text for claim in rejected] == ["bad law"]


def test_logs_a_warning_when_a_document_claim_is_dropped(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    draft = _document_draft("t", _UNSHOWN)

    with caplog.at_level(logging.WARNING, logger="openlaw.answers.validate"):
        validate_answer(draft, _context("a"), _document_context(_SHOWN))

    assert any(_UNSHOWN in record.message for record in caplog.records)


def test_validation_does_not_mutate_the_document_context_or_draft() -> None:
    draft = _document_draft("t", _SHOWN)
    document_context = _document_context(_SHOWN)

    validate_answer(draft, _context("a"), document_context)

    assert len(draft.document_claims) == 1
    assert document_context.excerpt_ids == frozenset({_SHOWN})


# --- `Source` carries what the server already knows -------------------------
#
# `Source` used to expose `chunk_id`/`source_url`/`title` only, so every
# judgment citation reached the browser as a null title above a raw tarball
# URL and the frontend had to guess the citation's kind from its hostname.
# The payload has always carried the rest (`chunk_from_point`); it was simply
# discarded here.


def _judgment_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="1963_INSC_244_c3",
        text="text",
        source_url="https://example.s3.amazonaws.com/data/tar/1963_INSC_244.tar",
        score=0.9,
        kind=ChunkKind.JUDGMENT,
        case_id="1963_INSC_244",
        court_code="SC",
        decision_date="1963-04-05",
        judges=("K Subba Rao", "J C Shah"),
        headnote="Whether notice was required.",
    )


def test_a_judgment_source_carries_everything_needed_to_cite_it_readably() -> None:
    context = RetrievalContext(query="q", chunks=(_judgment_chunk(),))
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="c", chunk_ids=("1963_INSC_244_c3",)),),
        sufficient=True,
    )

    source = validate_answer(draft, context).sources[0]

    assert source.kind is ChunkKind.JUDGMENT
    assert source.case_id == "1963_INSC_244"
    assert source.court_code == "SC"
    assert source.decision_date == "1963-04-05"
    assert source.judges == ("K Subba Rao", "J C Shah")
    assert source.headnote == "Whether notice was required."


def test_a_provision_source_leaves_judgment_fields_absent_rather_than_invented() -> (
    None
):
    draft = DraftAnswer(
        summary="s", claims=(Claim(text="c", chunk_ids=("a",)),), sufficient=True
    )

    source = validate_answer(draft, _context("a")).sources[0]

    assert source.kind is ChunkKind.PROVISION
    assert source.case_id is None
    assert source.decision_date is None
    assert source.judges == ()
    assert source.headnote is None


def test_source_still_requires_a_government_url() -> None:
    """Widening `Source` must not make it constructible without the one field
    that makes a citation checkable."""
    with pytest.raises(ValidationError):
        Source(chunk_id="a", kind=ChunkKind.PROVISION)  # type: ignore[call-arg]


# --- the refusal record's namespace invariant is structural ----------------
#
# Security review MEDIUM-3. "Exactly one of the two id tuples is populated"
# was documented and left to the one function that builds these records. Every
# other invariant in this module is enforced by the type (`extra="forbid"` on
# `DocumentCitation`, the absent `source_url`, whole-claim rejection), so this
# one is too: a refusal record that claimed both namespaces, or neither, would
# be a refusal nobody could file.


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="neither-namespace"),
        pytest.param(
            {"unresolvable_chunk_ids": ("a",), "unresolvable_excerpt_ids": (_SHOWN,)},
            id="both-namespaces",
        ),
    ],
)
def test_a_dropped_claim_must_name_exactly_one_namespace(
    kwargs: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValidationError):
        DroppedClaim(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="neither-namespace"),
        pytest.param(
            {"unresolvable_chunk_ids": ("a",), "unresolvable_excerpt_ids": (_SHOWN,)},
            id="both-namespaces",
        ),
    ],
)
def test_a_rejected_claim_must_name_exactly_one_namespace(
    kwargs: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValidationError):
        RejectedClaim(text="t", **kwargs)


def test_a_refusal_record_in_one_namespace_is_accepted() -> None:
    assert DroppedClaim(unresolvable_chunk_ids=("a",)).unresolvable_excerpt_ids == ()
    assert DroppedClaim(unresolvable_excerpt_ids=(_SHOWN,)).unresolvable_chunk_ids == ()
    assert RejectedClaim(text="t", unresolvable_chunk_ids=("a",)).text == "t"


# --- a refused document claim leaves an auditable trace --------------------
#
# Security review MEDIUM-1. A document inducing claims that cite excerpts
# nobody showed the model is the clearest signal of an injection attempt, and
# a `logger.warning` is not a retained, auditable event.


def test_refused_document_claims_become_hallucination_events() -> None:
    draft = DraftAnswer(
        summary="s",
        claims=(Claim(text="bad law", chunk_ids=("ghost",)),),
        document_claims=(DocumentClaim(text="bad doc", excerpt_ids=(_UNSHOWN,)),),
        sufficient=True,
    )
    context = _context("a")

    events = build_hallucination_events(
        "q",
        rejected_claims(draft, context) + rejected_document_claims(draft),
        context,
    )

    assert [event.claim_text for event in events] == ["bad law", "bad doc"]
    assert events[0].dropped_chunk_ids == ("ghost",)
    assert events[0].dropped_excerpt_ids == ()
    # The namespaces stay apart all the way into the log: an excerpt id never
    # lands in a field named for chunk ids, which is also the column name on
    # `openlaw.db.models.HallucinationLog`.
    assert events[1].dropped_chunk_ids == ()
    assert events[1].dropped_excerpt_ids == (_UNSHOWN,)


def test_a_rejected_claim_cannot_be_attached_to_a_validated_answer() -> None:
    """The two refusal records share a base class now, which would be a
    quiet way to reopen the third unverified-prose route: if `RejectedClaim`
    were a *subclass* of `DroppedClaim` rather than a sibling, its verbatim
    text would validate straight onto `ValidatedAnswer.dropped`. They are
    siblings, so it does not."""
    rejected = RejectedClaim(text="invented prose", unresolvable_chunk_ids=("ghost",))

    with pytest.raises(ValidationError):
        ValidatedAnswer(unverified_summary=None, dropped=(rejected,))  # type: ignore[arg-type]
