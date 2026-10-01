"""Static checks on `scripts/verify_selfhost.sh` (final whole-plan review, M2).

This script cannot be executed by this test suite -- it starts with
`docker compose up -d` against the real stack, which this project's test
environment must never touch. What CAN be verified without running it is
that the text of the script no longer makes the specific unverified claim
the review found: `corpus_version` is present on EVERY `AskResponse`
(`openlaw.api.schemas.AskResponse.corpus_version` has no default and is
always set -- see `tests/api/test_routes.py`), including an
`is_answerable: false` response with an empty `sources` list, so grepping
for it alone proves nothing about whether a cited answer came back. The
script printed "PASS -- the stack is up and answering with citations" on
that check alone; these tests guard against regressing to that.
"""

from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "verify_selfhost.sh"
)


def _source() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def test_verify_selfhost_script_is_where_this_test_expects_it() -> None:
    assert _SCRIPT_PATH.is_file()


def test_citation_claim_is_not_gated_on_corpus_version_alone() -> None:
    """The bare, insufficient check the finding was about must be gone."""
    assert "grep -q '\"corpus_version\"'" not in _source()


def test_citation_claim_requires_is_answerable_and_a_non_empty_sources_list() -> None:
    """A genuinely cited answer requires BOTH `is_answerable: true` and a
    non-empty `sources` array -- `"sources":[{` matches only when at least
    one source card is present, never an empty `[]`."""
    source = _source()
    assert '"is_answerable":true' in source
    assert '"sources":\\[{' in source


def test_citation_claim_line_is_reached_only_after_the_verifying_check() -> None:
    """"answering with citations" must be textually downstream of the
    is_answerable/sources check, not printed unconditionally after asking
    the question -- ordering is what makes the check actually gate the
    claim rather than merely appear somewhere in the file."""
    source = _source()
    claim_index = source.index("answering with citations")
    check_index = source.index('"is_answerable":true')
    assert check_index < claim_index


def test_unverified_branch_admits_it_did_not_check_a_citation() -> None:
    """When the check fails, the script must say plainly that it did not
    verify a citation -- not stay silent and not repeat the citation claim."""
    source = _source()
    unverified_branch = source.split('"is_answerable":true', 1)[1]
    assert "did NOT verify a cited answer" in unverified_branch


# --- the upload round trip and the key check (Plan 6 Task 18) ----------------


def test_the_script_never_echoes_the_document_encryption_key() -> None:
    """**The property that matters most in this file.** The script reads the key
    into `document_key` to measure it; no line may ever put that variable into
    output, and it must be unset immediately after measuring."""
    source = _source()

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "document_key" not in stripped:
            continue
        assert not stripped.startswith("echo"), f"the key could be printed: {line}"
        assert not stripped.startswith("printf '[Selfhost]"), line
    assert "unset document_key" in source
    # And nothing interpolates it into a message or a URL.
    assert "${document_key}" not in source


def test_the_key_check_reports_only_presence_and_length() -> None:
    source = _source()

    assert "OPENLAW_DOCUMENT_ENCRYPTION_KEY is empty" in source
    assert "decodes to 32 bytes" in source
    assert "The key itself is deliberately not printed." in source


def test_the_key_check_only_runs_when_uploads_are_enabled() -> None:
    """A deployment with uploads switched off has no key to check, and failing
    it there would be a false alarm, not a finding."""
    source = _source()

    kill_switch = source.index("uploads_enabled=")
    key_check = source.index("OPENLAW_DOCUMENT_ENCRYPTION_KEY is set")
    assert kill_switch < key_check
    assert "skipping the document checks, which is correct, not a gap." in source


def test_the_script_uploads_and_then_deletes_a_document() -> None:
    source = _source()

    assert "-X POST http://localhost:8080/api/openlaw/documents" in source
    assert "-X DELETE" in source
    assert '"http://localhost:8080/api/openlaw/documents/${document_id}"' in source


def test_a_delete_that_does_not_return_204_fails_the_script() -> None:
    """An upload that cannot be withdrawn is the one failure this feature must
    not have, so it is a hard exit rather than a warning."""
    source = _source()

    assert 'if [ "$delete_status" != "204" ]; then' in source
    delete_branch = source.split('if [ "$delete_status" != "204" ]; then', 1)[1]
    assert "exit 1" in delete_branch.split("fi", 1)[0]


def test_a_second_delete_must_be_a_404_not_a_403() -> None:
    """Idempotent and unenumerable: the script pins the behaviour that makes
    `DELETE /documents/{id}` useless as an enumeration oracle."""
    source = _source()

    assert 'if [ "$repeat_status" != "404" ]; then' in source


def test_the_upload_round_trip_reuses_one_cookie_jar() -> None:
    """An anonymous upload is owned by a cookie. Without `-b`, the DELETE
    arrives as a different identity and is correctly refused -- which would
    make this check fail for a reason that is not a defect."""
    source = _source()

    assert 'cookie_jar="$(mktemp)"' in source
    assert source.count('-c "$cookie_jar" -b "$cookie_jar"') >= 3


def test_env_is_parsed_rather_than_sourced() -> None:
    """A malformed or hostile `.env` line must not be able to execute."""
    source = _source()

    assert "source .env" not in source
    assert ". .env" not in source
    assert 'sed -n "s/^${name}=//p" .env' in source
