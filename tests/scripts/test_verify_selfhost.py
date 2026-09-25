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
