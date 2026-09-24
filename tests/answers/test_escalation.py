import json

import pytest

from openlaw.answers.escalation import (
    CRISIS_PATTERNS,
    NALSA_HELPLINE,
    CrisisKind,
    append_events,
    build_hallucination_events,
    crisis_response,
    detect_crisis,
    escalation_footer,
)
from openlaw.answers.schema import Claim
from openlaw.answers.validate import DroppedClaim, ValidatedAnswer
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk


def _context(*chunk_ids: str) -> RetrievalContext:
    return RetrievalContext(
        query="q",
        chunks=tuple(
            RetrievedChunk(
                chunk_id=cid,
                text="t",
                source_url=f"https://x.gov.in/{cid}",
                score=0.5,
                kind=ChunkKind.PROVISION,
            )
            for cid in chunk_ids
        ),
    )


# --- crisis detection ---------------------------------------------------


def test_detects_domestic_violence() -> None:
    assert detect_crisis("my husband beats me every night") is (
        CrisisKind.DOMESTIC_VIOLENCE
    )


def test_detects_custodial_abuse() -> None:
    assert detect_crisis("the police beat my son in the lockup") is (
        CrisisKind.CUSTODIAL_ABUSE
    )


def test_detects_trafficking() -> None:
    assert detect_crisis("my daughter was trafficked and is missing") is (
        CrisisKind.TRAFFICKING
    )


def test_detects_self_harm() -> None:
    assert detect_crisis("i want to end my life over this debt") is CrisisKind.SELF_HARM


def test_ordinary_legal_question_is_not_a_crisis() -> None:
    assert detect_crisis("my landlord will not return my deposit") is None


def test_crisis_detection_is_case_insensitive() -> None:
    assert detect_crisis("MY HUSBAND BEATS ME") is CrisisKind.DOMESTIC_VIOLENCE


def test_empty_text_is_not_a_crisis() -> None:
    assert detect_crisis("") is None


@pytest.mark.parametrize("kind", list(CrisisKind))
def test_every_crisis_kind_has_at_least_one_pattern(kind: CrisisKind) -> None:
    assert CRISIS_PATTERNS[kind]


# --- fail-safe bias: softer / uncertain phrasing must still route to help --


@pytest.mark.parametrize(
    "text",
    [
        "he hurts me all the time and I don't know what to do",
        "I'm scared of my husband, he hit me again",
        "my in-laws hit me for dowry",
    ],
)
def test_softer_domestic_violence_phrasing_still_routes_to_help(text: str) -> None:
    """Recall matters more than precision here: a missed signal leaves a
    person in danger, a false positive only shows them a helpline they did
    not strictly need."""
    assert detect_crisis(text) is CrisisKind.DOMESTIC_VIOLENCE


@pytest.mark.parametrize(
    "text",
    [
        "i don't want to live anymore",
        "there's no point in living after this",
        "i keep thinking about suicide because of this case",
    ],
)
def test_softer_self_harm_phrasing_still_routes_to_help(text: str) -> None:
    assert detect_crisis(text) is CrisisKind.SELF_HARM


def test_when_multiple_crises_are_signalled_the_most_urgent_wins() -> None:
    """A message can plausibly touch more than one category. The safe
    direction is to resolve to whichever response gets help fastest, not to
    silently pick one at random or refuse to classify."""
    text = "my husband beats me and i want to end my life"
    assert detect_crisis(text) is CrisisKind.SELF_HARM


# --- crisis responses -----------------------------------------------------


def test_crisis_response_leads_with_a_helpline() -> None:
    """Helpline numbers must come before any legal analysis."""
    response = crisis_response(CrisisKind.DOMESTIC_VIOLENCE)
    assert "181" in response or "1091" in response


def test_every_crisis_kind_has_a_response() -> None:
    for kind in CrisisKind:
        assert crisis_response(kind).strip()


def test_self_harm_response_carries_tele_manas() -> None:
    assert "14416" in crisis_response(CrisisKind.SELF_HARM)


def test_trafficking_response_carries_emergency_and_child_helplines() -> None:
    response = crisis_response(CrisisKind.TRAFFICKING)
    assert "112" in response
    assert "1098" in response


def test_custodial_abuse_response_carries_nhrc_helpline() -> None:
    assert "14433" in crisis_response(CrisisKind.CUSTODIAL_ABUSE)


# --- escalation footer -----------------------------------------------------


def test_escalation_footer_carries_the_nalsa_helpline() -> None:
    assert NALSA_HELPLINE in escalation_footer()


def test_escalation_footer_names_the_state_authority_when_known() -> None:
    assert "Kerala" in escalation_footer(state="Kerala")


def test_escalation_footer_states_this_is_not_legal_advice() -> None:
    assert "not legal advice" in escalation_footer().lower()


def test_escalation_footer_without_state_still_routes_somewhere() -> None:
    assert "District Legal Services Authority" in escalation_footer()


# --- hallucination logging --------------------------------------------------


def test_hallucination_events_record_dropped_chunk_ids() -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    events = build_hallucination_events("my question", answer, _context("real"))

    assert events[0].dropped_chunk_ids == ("ghost",)
    assert events[0].retrieved_chunk_ids == ("real",)
    assert events[0].question == "my question"
    assert events[0].claim_text == "bad"
    assert events[0].timestamp


def test_no_events_when_nothing_was_dropped() -> None:
    answer = ValidatedAnswer(
        unverified_summary="s",
        claims=(Claim(text="c", chunk_ids=("real",)),),
        is_answerable=True,
    )
    assert build_hallucination_events("q", answer, _context("real")) == ()


def test_one_event_per_dropped_claim() -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(
            DroppedClaim(text="bad one", unresolvable_chunk_ids=("ghost1",)),
            DroppedClaim(text="bad two", unresolvable_chunk_ids=("ghost2", "ghost3")),
        ),
        is_answerable=False,
    )
    events = build_hallucination_events("q", answer, _context("real"))
    assert len(events) == 2
    assert events[1].dropped_chunk_ids == ("ghost2", "ghost3")


def test_hallucination_event_is_frozen() -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    event = build_hallucination_events("q", answer, _context("real"))[0]
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen model raises
        event.question = "changed"  # type: ignore[misc]


def test_events_append_as_jsonl(tmp_path) -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    events = build_hallucination_events("q", answer, _context("real"))
    path = tmp_path / "hallucinations.jsonl"

    assert append_events(events, path) == 1
    assert append_events(events, path) == 1

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["dropped_chunk_ids"] == ["ghost"]


def test_append_events_with_empty_tuple_writes_nothing_and_creates_no_file(
    tmp_path,
) -> None:
    path = tmp_path / "nested" / "hallucinations.jsonl"
    assert append_events((), path) == 0
    assert not path.exists()


def test_append_events_creates_parent_directories(tmp_path) -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    events = build_hallucination_events("q", answer, _context("real"))
    path = tmp_path / "nested" / "deep" / "hallucinations.jsonl"

    assert append_events(events, path) == 1
    assert path.exists()
