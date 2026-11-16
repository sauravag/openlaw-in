"""Tests for the final retrieval pool: statute floor, dedupe, and caps.

The pool is what reaches the generator. Before this module existed the
cross-collection merge kept 10 chunks, interleaved roughly 5 statutes and 5
judgments, and the statute half was routinely spent on near-identical copies
of one section (central and state copies of the Parsi Marriage and Divorce
Act s.32). See `openlaw.retrieval.pool`'s module docstring.
"""

from openlaw.retrieval.context import ChunkKind, RetrievedChunk
from openlaw.retrieval.pool import provision_dedupe_key, select_pool


def _provision(
    chunk_id: str, score: float, body: str | None = None, header: str = "Act: Some Act | India | Central | In Force"
) -> RetrievedChunk:
    text = f"{header}\nSection 1: Heading\n\n{body if body is not None else f'body of {chunk_id}'}"
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        source_url=f"https://indiacode.nic.in/{chunk_id}",
        score=score,
        kind=ChunkKind.PROVISION,
    )


def _judgment(chunk_id: str, score: float, text: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text if text is not None else f"judgment text {chunk_id}",
        source_url=f"https://s3.amazonaws.com/{chunk_id}",
        score=score,
        kind=ChunkKind.JUDGMENT,
        case_id=f"case-{chunk_id}",
    )


def _ranked(*chunks: RetrievedChunk) -> tuple[RetrievedChunk, ...]:
    return tuple(sorted(chunks, key=lambda chunk: chunk.score, reverse=True))


def _kinds(pool: tuple[RetrievedChunk, ...]) -> tuple[int, int]:
    statutes = sum(1 for chunk in pool if chunk.kind == ChunkKind.PROVISION)
    return statutes, len(pool) - statutes


def test_statute_floor_is_honoured_when_every_judgment_outranks_every_statute() -> None:
    # Arrange
    judgments = [_judgment(f"j{i}", 1.0 - i * 0.01) for i in range(20)]
    statutes = [_provision(f"s{i}", 0.5 - i * 0.01) for i in range(10)]

    # Act
    pool = select_pool(_ranked(*judgments, *statutes), limit=16, statute_floor=8)

    # Assert
    assert _kinds(pool) == (8, 8)
    assert [c.chunk_id for c in pool if c.kind == ChunkKind.PROVISION] == [
        f"s{i}" for i in range(8)
    ]


def test_judgments_fill_the_pool_when_statutes_run_short() -> None:
    ranked = _ranked(
        *(_provision(f"s{i}", 0.9 - i * 0.01) for i in range(3)),
        *(_judgment(f"j{i}", 0.8 - i * 0.01) for i in range(20)),
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert _kinds(pool) == (3, 13)


def test_statutes_fill_the_pool_when_judgments_run_short() -> None:
    ranked = _ranked(
        *(_provision(f"s{i}", 0.9 - i * 0.01) for i in range(20)),
        *(_judgment(f"j{i}", 0.95 - i * 0.01) for i in range(2)),
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert _kinds(pool) == (14, 2)


def test_the_pool_never_exceeds_the_total_cap() -> None:
    ranked = _ranked(
        *(_provision(f"s{i}", 0.9 - i * 0.001) for i in range(40)),
        *(_judgment(f"j{i}", 0.9 - i * 0.001) for i in range(40)),
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert len(pool) == 16


def test_duplicate_sections_collapse_to_the_best_ranked_copy() -> None:
    """Central and state copies of one section differ only in the header
    line ("| State |" vs "| Central |", a trailing full stop on the title)
    and in whitespace -- the live Parsi s.32 triple looks exactly like this."""
    body = "Provided that in every suit for divorce on this ground it shall be..."
    ranked = _ranked(
        _provision("IND_state_14003_s032_p2", 0.9, body, "Act: The Parsi Act, 1936 | India | State | In Force"),
        _provision("IND_central_2476_s032_p2", 0.8, body.replace(" ", "  "), "Act: The Parsi Act, 1936. | India | Central | In Force"),
        _provision("IND_state_17290_s032_p2", 0.7, body, "Act: The Parsi Act, 1936 | India | State | In Force"),
        _provision("IND_central_1560_s21B", 0.6, "trial to be concluded within six months"),
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert [c.chunk_id for c in pool] == ["IND_state_14003_s032_p2", "IND_central_1560_s21B"]


def test_dedupe_happens_before_the_floor_so_duplicates_do_not_eat_statute_slots() -> None:
    ranked = _ranked(
        *(_provision(f"dup{i}", 0.99 - i * 0.001, "same body") for i in range(8)),
        *(_provision(f"s{i}", 0.5 - i * 0.01) for i in range(8)),
        *(_judgment(f"j{i}", 0.9 - i * 0.01) for i in range(20)),
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    statute_ids = [c.chunk_id for c in pool if c.kind == ChunkKind.PROVISION]
    assert statute_ids == ["dup0", *(f"s{i}" for i in range(7))]


def test_different_parts_of_one_long_section_are_not_duplicates() -> None:
    first = _provision("act_s005_p0", 0.9, "first part of the section")
    second = _provision("act_s005_p1", 0.8, "second part of the section")

    assert provision_dedupe_key(first) != provision_dedupe_key(second)


def test_judgments_are_never_deduplicated() -> None:
    ranked = _ranked(_judgment("j1", 0.9, "same"), _judgment("j2", 0.8, "same"))

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert [c.chunk_id for c in pool] == ["j1", "j2"]


def test_the_pool_keeps_fused_rank_order() -> None:
    ranked = _ranked(
        _judgment("j1", 0.9), _provision("s1", 0.8), _judgment("j2", 0.7), _provision("s2", 0.6)
    )

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert [c.chunk_id for c in pool] == ["j1", "s1", "j2", "s2"]


def test_a_character_budget_trims_judgments_first_and_never_below_the_floor() -> None:
    big = "x" * 1_000
    ranked = _ranked(
        *(_judgment(f"j{i}", 0.99 - i * 0.01, big) for i in range(8)),
        *(_provision(f"s{i}", 0.5 - i * 0.01, f"{big}{i}") for i in range(10)),
    )

    # Room for roughly 9 chunks of ~1k chars: every judgment has to go
    # before any statute does, and the 8-statute floor is never breached.
    pool = select_pool(ranked, limit=16, statute_floor=8, max_chars=9_500)

    statutes, judgments = _kinds(pool)
    assert statutes == 8
    assert judgments <= 1
    assert sum(len(c.text) for c in pool) <= 9_500


def test_the_character_budget_trims_statutes_above_the_floor_once_judgments_are_gone() -> None:
    big = "x" * 1_000
    ranked = _ranked(*(_provision(f"s{i}", 0.9 - i * 0.01, f"{big}{i}") for i in range(16)))

    pool = select_pool(ranked, limit=16, statute_floor=8, max_chars=10_700)

    assert len(pool) == 10
    assert [c.chunk_id for c in pool] == [f"s{i}" for i in range(10)]


def test_no_budget_means_no_character_trimming() -> None:
    big = "x" * 50_000
    ranked = _ranked(*(_judgment(f"j{i}", 0.9 - i * 0.01, big) for i in range(4)))

    pool = select_pool(ranked, limit=16, statute_floor=8)

    assert len(pool) == 4
