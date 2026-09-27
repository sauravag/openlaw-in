"""Corpus validation gate: every indexed chunk must resolve to a government URL.

Scrolls every collection in the corpus and validates every payload.

`acts_india` is a third-party publisher snapshot (restored from a Qdrant
snapshot mirror, not built by this project). A measured fraction of its points
are regulator instruments — MCA notifications, Law Commission reports — with no
usable government `source_url`. That gap is inherent to the publisher's data,
not a defect this project introduced, and deleting those points is not this
gate's call to make. So for `acts_india`, a missing URL is REPORTED, not FAILED.
`acts_india` payloads also predate this project's `corpus_version` convention
entirely (no such field exists in the publisher's schema), so a missing
corpus_version there is expected and likewise never fails the gate.

`sc_judgments_v1` is built by this project (scripts/build_sc_index.py), which
already drops any chunk it cannot resolve a URL for before indexing. So for
`sc_judgments_v1`, a missing URL or corpus_version is a real defect and FAILS
the gate (exit 1).

`hc_judgments_v1` (High Court judgments, scripts/build_hc_index.py) is held to
the same strict standard as `sc_judgments_v1`: it drops any chunk it cannot
resolve a government URL for before indexing, so a missing URL or
corpus_version there is likewise a real defect, not an accepted gap.

A collection that has not been built yet is reported as "not built yet" and
skipped, rather than crashing the gate.

Run: python scripts/validate_corpus.py
"""

import sys
from dataclasses import dataclass

import httpx

from openlaw.config import get_settings
from openlaw.index.hc_upsert import HC_COLLECTION
from openlaw.index.upsert import SC_COLLECTION
from openlaw.index.validation import ValidationReport, validate_payloads

PAGE_SIZE = 1_000
ACTS_INDIA_COLLECTION = "acts_india"

#: The filtered, citable copy of `acts_india` that retrieval actually queries
#: (built by `scripts/build_sparse_vectors.py`, which excludes the 137,441
#: provisions carrying no government source URL).
ACTS_INDIA_V1_COLLECTION = "acts_india_v1"


@dataclass(frozen=True)
class CollectionPolicy:
    """How strictly one collection's validation violations are treated."""

    name: str
    strict: bool  # True: a missing URL/version fails the gate.


POLICIES: tuple[CollectionPolicy, ...] = (
    CollectionPolicy(name=ACTS_INDIA_COLLECTION, strict=False),
    # Strict, unlike its source snapshot above. The gate previously named only
    # `acts_india` -- the raw publisher snapshot, where un-citable points are
    # expected and tolerated -- while `acts_india_v1`, the collection retrieval
    # ACTUALLY queries, was never checked at all. It passed on inspection
    # (0 of 961,136 missing either field), but by construction rather than by
    # enforcement: a future rebuild that dropped `has_source_url` would have
    # reported GATE PASS while shipping un-citable provisions to users.
    CollectionPolicy(name=ACTS_INDIA_V1_COLLECTION, strict=True),
    CollectionPolicy(name=SC_COLLECTION, strict=True),
    CollectionPolicy(name=HC_COLLECTION, strict=True),
)


def _collection_exists(name: str, qdrant_url: str, client: httpx.Client) -> bool:
    """Return True when `name` exists as a Qdrant collection."""
    response = client.get(f"{qdrant_url}/collections/{name}")
    return response.status_code == 200


def _scroll_and_validate(
    name: str, qdrant_url: str, client: httpx.Client
) -> ValidationReport:
    """Scroll every point in `name` and return the aggregate validation report."""
    checked = 0
    missing_url: list[str] = []
    missing_version: list[str] = []

    offset: object | None = None
    while True:
        body: dict[str, object] = {
            "limit": PAGE_SIZE,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset

        response = client.post(
            f"{qdrant_url}/collections/{name}/points/scroll", json=body
        )
        response.raise_for_status()
        result = response.json()["result"]

        page_report = validate_payloads(result["points"])
        checked += page_report.checked
        missing_url.extend(page_report.missing_url)
        missing_version.extend(page_report.missing_version)

        offset = result.get("next_page_offset")
        if offset is None:
            break

    return ValidationReport(
        checked=checked, missing_url=missing_url, missing_version=missing_version
    )


def _report_strict(name: str, report: ValidationReport) -> bool:
    """Print `report` for a self-built collection. Return True when it PASSES."""
    print(f"[Validate] {name}: checked {report.checked:,} points")
    if report.is_clean:
        print(
            f"[Validate] {name}: PASS — every indexed chunk resolves to a "
            "government URL"
        )
        return True

    violations = report.missing_url + report.missing_version
    print(f"[Validate] {name}: FAIL — {len(violations):,} violations")
    for identifier in violations[:20]:
        print(f"  - {identifier}")
    return False


def _report_advisory(name: str, report: ValidationReport) -> None:
    """Print `report` for a third-party snapshot collection. Never fails the gate."""
    print(f"[Validate] {name}: checked {report.checked:,} points")
    if report.checked == 0:
        return

    url_fraction = len(report.missing_url) / report.checked
    print(
        f"[Validate] {name}: {len(report.missing_url):,} points "
        f"({url_fraction:.1%}) carry no usable government source_url"
    )
    print(
        f"[Validate] {name}: these are inherent to the publisher's snapshot "
        "(regulator instruments — MCA notifications, Law Commission reports) "
        "and MUST be excluded at retrieval time"
    )
    if report.missing_version:
        version_fraction = len(report.missing_version) / report.checked
        print(
            f"[Validate] {name}: {len(report.missing_version):,} points "
            f"({version_fraction:.1%}) carry no corpus_version field — expected, "
            "this collection predates our corpus_version convention; not a gate "
            "failure"
        )


def main() -> int:
    settings = get_settings()
    gate_failed = False

    with httpx.Client(timeout=120) as client:
        for policy in POLICIES:
            if not _collection_exists(policy.name, settings.qdrant_url, client):
                print(f"[Validate] {policy.name}: not built yet, skipping")
                continue

            report = _scroll_and_validate(policy.name, settings.qdrant_url, client)
            if policy.strict:
                if not _report_strict(policy.name, report):
                    gate_failed = True
            else:
                _report_advisory(policy.name, report)

    print()
    print("[Validate] GATE FAIL" if gate_failed else "[Validate] GATE PASS")
    return 1 if gate_failed else 0


if __name__ == "__main__":
    sys.exit(main())
