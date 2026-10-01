import json
import re
import stat
from datetime import UTC, datetime, timedelta

import pytest

from openlaw.answers.escalation import (
    CRISIS_PATTERNS,
    DEVANAGARI_CRISIS_PATTERNS,
    DEVANAGARI_PATTERN_COUNT,
    DEFAULT_HALLUCINATION_LOG_RETENTION_DAYS,
    NALSA_HELPLINE,
    CrisisKind,
    HallucinationEvent,
    append_events,
    build_hallucination_events,
    crisis_helplines,
    crisis_kind_from_value,
    crisis_response,
    dedupe_helplines,
    detect_crisis,
    escalation_footer,
    most_urgent_crisis,
    nalsa_helpline,
    purge_stale_events,
)
from openlaw.answers.escalation import _COMPILED_DEVANAGARI, _DEVANAGARI_CHAR
from openlaw.language.detect import SCRIPT_RANGES
from openlaw.answers.schema import Claim
from openlaw.answers.validate import RejectedClaim
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
    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    events = build_hallucination_events("my question", rejected, _context("real"))

    assert events[0].dropped_chunk_ids == ("ghost",)
    assert events[0].retrieved_chunk_ids == ("real",)
    assert events[0].question == "my question"
    assert events[0].claim_text == "bad"
    assert events[0].timestamp


def test_no_events_when_nothing_was_dropped() -> None:
    assert build_hallucination_events("q", (), _context("real")) == ()


def test_one_event_per_dropped_claim() -> None:
    rejected = (
        RejectedClaim(text="bad one", unresolvable_chunk_ids=("ghost1",)),
        RejectedClaim(text="bad two", unresolvable_chunk_ids=("ghost2", "ghost3")),
    )
    events = build_hallucination_events("q", rejected, _context("real"))
    assert len(events) == 2
    assert events[1].dropped_chunk_ids == ("ghost2", "ghost3")


def test_hallucination_event_is_frozen() -> None:
    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    event = build_hallucination_events("q", rejected, _context("real"))[0]
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen model raises
        event.question = "changed"  # type: ignore[misc]


def test_events_append_as_jsonl(tmp_path) -> None:
    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    events = build_hallucination_events("q", rejected, _context("real"))
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
    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    events = build_hallucination_events("q", rejected, _context("real"))
    path = tmp_path / "nested" / "deep" / "hallucinations.jsonl"

    assert append_events(events, path) == 1
    assert path.exists()


# --- fix round 1: log file permissions and retention -----------------------


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_append_events_creates_file_with_owner_only_permissions(tmp_path) -> None:
    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    events = build_hallucination_events("q", rejected, _context("real"))
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

    rejected = (RejectedClaim(text="bad", unresolvable_chunk_ids=("ghost",)),)
    events = build_hallucination_events("q", rejected, _context("real"))
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


# --- the footer must not assert an unsourced statistic ----------------------
#
# The footer is the one piece of prose this system always emits, to every
# citizen. It carried "Around 80% of people in India qualify for free legal
# aid" -- an unsourced quantitative claim, in a product whose whole thesis is
# that unsourced legal assertions must not reach users.


def test_escalation_footer_asserts_no_unsourced_statistic() -> None:
    footer = escalation_footer()

    assert "80%" not in footer
    assert "%" not in footer


def test_escalation_footer_cites_the_statutory_basis_for_eligibility() -> None:
    footer = escalation_footer()

    assert "Legal Services Authorities Act, 1987" in footer
    assert "section 12" in footer


def test_escalation_footer_tells_people_how_to_check_eligibility() -> None:
    assert "whether you qualify" in escalation_footer()


# --- shared crisis-verdict helpers (Plan 6 Task 10) ------------------------
#
# Crisis routing now combines more than one verdict per turn: the question's
# own (regex, then classifier) and the stored, regex-only verdict of each
# attached document. Both the severity resolution and the stored-value
# parsing live here so neither is reimplemented per call site.


def test_most_urgent_crisis_returns_none_for_no_verdicts() -> None:
    assert most_urgent_crisis(()) is None
    assert most_urgent_crisis((None, None)) is None


def test_most_urgent_crisis_prefers_the_more_urgent_verdict() -> None:
    """Severity order, not argument order: a document revealing self-harm
    must not be answered with the domestic-violence helpline just because
    the question matched first."""
    assert (
        most_urgent_crisis((CrisisKind.DOMESTIC_VIOLENCE, CrisisKind.SELF_HARM))
        is CrisisKind.SELF_HARM
    )
    assert (
        most_urgent_crisis((CrisisKind.SELF_HARM, CrisisKind.DOMESTIC_VIOLENCE))
        is CrisisKind.SELF_HARM
    )


def test_most_urgent_crisis_agrees_with_detect_crisis_on_severity() -> None:
    """The two must not carry independent severity tables -- a question
    matching several categories and a turn carrying several verdicts have
    to resolve the same way."""
    multi = "my husband beats me and I want to kill myself"
    assert detect_crisis(multi) is most_urgent_crisis(
        (CrisisKind.DOMESTIC_VIOLENCE, CrisisKind.SELF_HARM)
    )


def test_crisis_kind_from_value_round_trips_every_kind() -> None:
    for kind in CrisisKind:
        assert crisis_kind_from_value(kind.value) is kind


def test_crisis_kind_from_value_treats_absent_and_blank_as_no_verdict() -> None:
    """`Document.crisis_kind` is NULL for a document the scan cleared, and a
    blank string is the same statement -- neither is an error."""
    assert crisis_kind_from_value(None) is None
    assert crisis_kind_from_value("") is None


def test_crisis_kind_from_value_logs_loudly_on_an_unrecognised_value(caplog) -> None:
    """An unrecognised stored verdict is a bug or a corrupted row on a
    SAFETY field. It must never crash the turn, and must never pass
    silently either: routing proceeds without it (no category can be
    fabricated from an unknown string) and an operator watching ordinary
    logs sees it."""
    with caplog.at_level("ERROR"):
        assert crisis_kind_from_value("not_a_crisis_kind") is None

    assert "not_a_crisis_kind" in caplog.text


def test_every_crisis_kind_value_fits_the_stored_column() -> None:
    """`openlaw.db.models.Document.crisis_kind` is `String(32)`; a kind whose
    value did not fit would be truncated into an unrecognised verdict."""
    for kind in CrisisKind:
        assert len(kind.value) <= 32


# --- Devanagari crisis patterns (Plan 6 Task 10, ruling 2) ----------------
#
# UNVERIFIED pending native-speaker review, and tuned for PRECISION rather
# than recall -- unlike the Latin-script set. The routing is why: a
# Latin-script match can short-circuit a turn, while a document-only match
# appends a helpline card to an otherwise normal legal answer, and a card
# that fires on every Hindi lease is one people learn to ignore.


@pytest.mark.parametrize(
    "text,kind",
    [
        ("मैं मरना चाहता हूँ", CrisisKind.SELF_HARM),
        ("अब जीना नहीं चाहती", CrisisKind.SELF_HARM),
        ("मैंने आत्महत्या करने की कोशिश की", CrisisKind.SELF_HARM),
        ("मेरा पति मुझे रोज मारता है", CrisisKind.DOMESTIC_VIOLENCE),
        ("मुझे मारता है", CrisisKind.DOMESTIC_VIOLENCE),
        ("घरेलू हिंसा अधिनियम के तहत आदेश", CrisisKind.DOMESTIC_VIOLENCE),
        ("ससुराल वाले मुझे मारते हैं", CrisisKind.DOMESTIC_VIOLENCE),
        ("पुलिस ने मुझे थाने में पीटा", CrisisKind.CUSTODIAL_ABUSE),
        ("हिरासत में मौत का मामला", CrisisKind.CUSTODIAL_ABUSE),
        ("मानव तस्करी का शिकार", CrisisKind.TRAFFICKING),
        ("बंधुआ मजदूर बनाकर रखा", CrisisKind.TRAFFICKING),
        ("जबरन शादी करवाई गई", CrisisKind.TRAFFICKING),
    ],
)
def test_devanagari_crisis_phrasings_are_detected(text: str, kind: CrisisKind) -> None:
    assert detect_crisis(text) is kind


@pytest.mark.parametrize(
    "text",
    [
        "किराया बकाया के बारे में क्या नियम है",
        "मेरा मकान मालिक जमा राशि वापस नहीं कर रहा",
        "मैंने पुलिस स्टेशन में शिकायत दर्ज कराई है",
        "तलाक की प्रक्रिया क्या है",
        "संपत्ति का बंटवारा कैसे होगा",
        "श्रम कानून के तहत मजदूरी का भुगतान",
        "उपभोक्ता फोरम में शिकायत कैसे करें",
        "कंपनी ने मेरा वेतन नहीं दिया, क्या करूं",
    ],
)
def test_ordinary_hindi_legal_text_is_not_a_crisis(text: str) -> None:
    """Precision is the constraint on this set, so it is tested as one."""
    assert detect_crisis(text) is None


def test_the_devanagari_gate_matches_the_projects_script_range() -> None:
    """`_DEVANAGARI_CHAR` is a local copy of
    `openlaw.language.detect.SCRIPT_RANGES`'s `"hi"` block, kept local so
    this module does not depend on the language package. Pinned here so the
    two cannot drift -- a narrowed gate would silently stop consulting the
    Devanagari patterns."""
    start, end = next(
        (start, end) for language, start, end in SCRIPT_RANGES if language == "hi"
    )

    assert _DEVANAGARI_CHAR.pattern == f"[{chr(start)}-{chr(end)}]"
    assert _DEVANAGARI_CHAR.search("मुझे") is not None
    assert _DEVANAGARI_CHAR.search("my husband beats me") is None


def test_latin_only_text_never_pays_for_the_devanagari_patterns() -> None:
    """The gate is a cost control for the one input whose size a user
    chooses -- a 10 MiB uploaded document. If it ever stopped gating, an
    English document would start paying for a pattern set that cannot
    match it."""
    calls: list[str] = []

    class _Counting:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def search(self, text: str) -> object:
            calls.append(text)
            return self._inner.search(text)  # type: ignore[attr-defined]

    patched = {
        kind: tuple(_Counting(pattern) for pattern in patterns)
        for kind, patterns in _COMPILED_DEVANAGARI.items()
    }
    original = dict(_COMPILED_DEVANAGARI)
    _COMPILED_DEVANAGARI.update(patched)  # type: ignore[arg-type]
    try:
        assert detect_crisis("an ordinary question about rent arrears") is None
        assert calls == []
        assert detect_crisis("किराया बकाया") is None
        assert calls  # the gate opened for Devanagari text
    finally:
        _COMPILED_DEVANAGARI.update(original)


def test_every_devanagari_pattern_compiles_and_is_counted() -> None:
    for patterns in DEVANAGARI_CRISIS_PATTERNS.values():
        for pattern in patterns:
            re.compile(pattern)
    assert DEVANAGARI_PATTERN_COUNT == sum(
        len(patterns) for patterns in DEVANAGARI_CRISIS_PATTERNS.values()
    )
    assert DEVANAGARI_PATTERN_COUNT > 0


# --- helpline deduplication (Plan 6 Task 10, ruling 1 revised) -------------


def test_dedupe_helplines_keeps_the_first_occurrence_and_the_order() -> None:
    """Order matters: the primary category's numbers must stay first (see
    `openlaw.language.pipeline._crisis_answer`)."""
    dv = crisis_helplines(CrisisKind.DOMESTIC_VIOLENCE)
    self_harm = crisis_helplines(CrisisKind.SELF_HARM)

    deduped = dedupe_helplines(dv + self_harm + (nalsa_helpline(),))

    numbers = [helpline.number for helpline in deduped]
    assert numbers == ["112", "181", "1091", "14416", NALSA_HELPLINE]


def test_dedupe_helplines_removes_the_overlapping_emergency_number() -> None:
    """112 is in three of the four category sets, so any two-category turn
    would otherwise show it twice."""
    custodial = crisis_helplines(CrisisKind.CUSTODIAL_ABUSE)
    trafficking = crisis_helplines(CrisisKind.TRAFFICKING)

    numbers = [h.number for h in dedupe_helplines(custodial + trafficking)]

    assert numbers.count("112") == 1
    assert len(numbers) == len(set(numbers))


def test_dedupe_helplines_fixes_the_custodial_nalsa_duplicate() -> None:
    """`_CRISIS_HELPLINE_NUMBERS[CUSTODIAL_ABUSE]` contains `NALSA_HELPLINE`
    itself, so appending the footer's NALSA number -- which every crisis turn
    does -- produced 15100 twice before this helper existed, with no
    document or second category involved."""
    with_footer_number = crisis_helplines(CrisisKind.CUSTODIAL_ABUSE) + (
        nalsa_helpline(),
    )
    assert [h.number for h in with_footer_number].count(NALSA_HELPLINE) == 2

    numbers = [h.number for h in dedupe_helplines(with_footer_number)]
    assert numbers.count(NALSA_HELPLINE) == 1


def test_dedupe_helplines_preserves_the_label_language_of_the_first() -> None:
    hindi_first = dedupe_helplines(
        crisis_helplines(CrisisKind.DOMESTIC_VIOLENCE, "hi")
        + crisis_helplines(CrisisKind.DOMESTIC_VIOLENCE, "en")
    )

    assert {helpline.label for helpline in hindi_first} == {
        helpline.label for helpline in crisis_helplines(CrisisKind.DOMESTIC_VIOLENCE, "hi")
    }


def test_dedupe_helplines_of_nothing_is_nothing() -> None:
    assert dedupe_helplines(()) == ()
