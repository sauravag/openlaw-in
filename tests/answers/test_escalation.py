import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from openlaw.answers.escalation import (
    CRISIS_PATTERNS,
    DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
    NALSA_HELPLINE,
    CrisisKind,
    HallucinationEvent,
    append_events,
    build_hallucination_events,
    crisis_response,
    detect_crisis,
    escalation_footer,
    purge_stale_events,
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


# --- fix round 1: adjacency, reversed constructions, non-obvious wording ---


@pytest.mark.parametrize(
    "text",
    [
        "my husband keeps beating me",
        "he keeps hurting me",
        "my in-laws are constantly hitting me",
    ],
)
def test_intervening_words_between_subject_and_verb_do_not_defeat_a_match(
    text: str,
) -> None:
    """A strict-adjacency pattern ("husband beats") misses "husband keeps
    beating" -- the gap must not defeat detection."""
    assert detect_crisis(text) is CrisisKind.DOMESTIC_VIOLENCE


@pytest.mark.parametrize(
    "text",
    [
        "tortured by police",
        "assaulted by cops at the station",
        "he was beaten by the police last night",
    ],
)
def test_reversed_victim_first_custodial_phrasing_is_detected(text: str) -> None:
    """"police beat him" is easy; "beaten by the police" (victim first, no
    "lockup"/"custody" keyword at all) must be detected too."""
    assert detect_crisis(text) is CrisisKind.CUSTODIAL_ABUSE


@pytest.mark.parametrize(
    "text",
    [
        "she was kidnapped for marriage",
        "he was sold to a family for work",
    ],
)
def test_trafficking_phrased_without_the_word_trafficking_is_detected(
    text: str,
) -> None:
    assert detect_crisis(text) is CrisisKind.TRAFFICKING


# --- fix round 1: transliterated Hindi/Hinglish coverage -------------------


@pytest.mark.parametrize(
    "text",
    [
        "mera pati mujhe maarta hai",
        "meri saas mujhe maarti hai",
        "mera pati mujhe roz peeta hai",
    ],
)
def test_hindi_hinglish_domestic_violence_phrasing_is_detected(text: str) -> None:
    """This product's users routinely code-mix; English-only patterns would
    silently exclude a large share of the people this feature exists for."""
    assert detect_crisis(text) is CrisisKind.DOMESTIC_VIOLENCE


@pytest.mark.parametrize(
    "text",
    [
        "marna chahta hoon",
        "mar jaana chahti hoon",
        "ab jeena nahi chahta",
        "meri zindagi khatam karna chahti hoon",
    ],
)
def test_hindi_hinglish_self_harm_phrasing_is_detected(text: str) -> None:
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


# --- fix round 1: log file permissions and retention -----------------------


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_append_events_creates_file_with_owner_only_permissions(tmp_path) -> None:
    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    events = build_hallucination_events("q", answer, _context("real"))
    path = tmp_path / "hallucinations.jsonl"

    append_events(events, path)

    assert _mode(path) == 0o600


def test_append_events_fixes_looser_permissions_on_an_existing_file(tmp_path) -> None:
    """The log can carry verbatim, sensitive user text, so a pre-existing
    file with looser permissions (e.g. created by some other process) must
    be corrected, not left as-is."""
    path = tmp_path / "hallucinations.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)
    assert _mode(path) == 0o644

    answer = ValidatedAnswer(
        unverified_summary=None,
        claims=(),
        dropped=(DroppedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),),
        is_answerable=False,
    )
    events = build_hallucination_events("q", answer, _context("real"))
    append_events(events, path)

    assert _mode(path) == 0o600


def test_default_retention_is_ninety_days() -> None:
    assert DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS == 90


def _event_at(timestamp: datetime) -> HallucinationEvent:
    return HallucinationEvent(
        question="q",
        dropped_chunk_ids=("ghost",),
        retrieved_chunk_ids=("real",),
        claim_text="bad",
        timestamp=timestamp.isoformat(),
    )


def test_purge_stale_events_removes_events_older_than_max_age(tmp_path) -> None:
    path = tmp_path / "hallucinations.jsonl"
    old = _event_at(datetime.now(UTC) - timedelta(days=100))
    fresh = _event_at(datetime.now(UTC) - timedelta(days=1))
    append_events((old, fresh), path)

    removed = purge_stale_events(path, max_age_days=90)

    assert removed == 1
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["claim_text"] == "bad"
    assert json.loads(lines[0])["retrieved_chunk_ids"] == ["real"]


def test_purge_stale_events_keeps_everything_within_retention(tmp_path) -> None:
    path = tmp_path / "hallucinations.jsonl"
    events = (
        _event_at(datetime.now(UTC) - timedelta(days=1)),
        _event_at(datetime.now(UTC) - timedelta(days=2)),
    )
    append_events(events, path)

    assert purge_stale_events(path, max_age_days=90) == 0
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_purge_stale_events_on_missing_file_returns_zero(tmp_path) -> None:
    path = tmp_path / "does-not-exist.jsonl"
    assert purge_stale_events(path) == 0


def test_purge_stale_events_drops_unparsable_lines(tmp_path) -> None:
    """A line whose timestamp cannot be read must not survive retention
    indefinitely just because its age is unknown."""
    path = tmp_path / "hallucinations.jsonl"
    fresh = _event_at(datetime.now(UTC) - timedelta(days=1))
    append_events((fresh,), path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not valid json\n")
        handle.write(json.dumps({"claim_text": "no timestamp field"}) + "\n")

    removed = purge_stale_events(path, max_age_days=90)

    assert removed == 2
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_purge_stale_events_preserves_owner_only_permissions(tmp_path) -> None:
    path = tmp_path / "hallucinations.jsonl"
    old = _event_at(datetime.now(UTC) - timedelta(days=100))
    fresh = _event_at(datetime.now(UTC) - timedelta(days=1))
    append_events((old, fresh), path)

    purge_stale_events(path, max_age_days=90)

    assert _mode(path) == 0o600


def test_purge_stale_events_uses_the_documented_default_when_unspecified(
    tmp_path,
) -> None:
    path = tmp_path / "hallucinations.jsonl"
    just_over_default = _event_at(
        datetime.now(UTC) - timedelta(days=DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS + 1)
    )
    append_events((just_over_default,), path)

    assert purge_stale_events(path) == 1
