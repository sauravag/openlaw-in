from openlaw.answers.schema import Claim, DraftAnswer
from openlaw.answers.validate import rejected_claims, validate_answer
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
