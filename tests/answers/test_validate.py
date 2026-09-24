from openlaw.answers.schema import Claim, DraftAnswer
from openlaw.answers.validate import validate_answer
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
    assert [dropped.text for dropped in result.dropped] == ["bad"]


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


def test_fully_invalidated_answer_keeps_the_original_summary_text() -> None:
    """A fully-dropped answer is not silently emptied; the summary is preserved
    so the caller can decide how to present the "could not verify" state, and
    every dropped claim is recorded rather than discarded."""
    draft = DraftAnswer(
        summary="You are entitled to X under section Y.",
        claims=(Claim(text="bad", chunk_ids=("nope",)),),
        sufficient=True,
    )
    result = validate_answer(draft, _context("a"))

    assert result.summary == "You are entitled to X under section Y."
    assert result.is_answerable is False
    assert result.claims == ()
    assert result.sources == ()
    assert len(result.dropped) == 1


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
