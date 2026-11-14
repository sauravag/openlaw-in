"""Refusing to overwrite a Qdrant corpus the operator already has.

Split out of `scripts/fetch_corpus.py`; behaviour is unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from openlaw.index.publish import ExportTarget, court_code_filter

COLLECTION_NOT_FOUND = 404


@dataclass(frozen=True)
class Existing:
    """What a Qdrant collection already holds, before we write anything.

    `matching` is whether any of those points belong to the slice being
    fetched. `hc_judgments_v1` is shared across courts (see
    `openlaw.corpus.high_courts`), so Karnataka's 1.4M points must not block
    a later Delhi fetch -- but Delhi's own points must.
    """

    points: int
    matching: bool


def inspect_collection(
    client: httpx.Client, qdrant_url: str, target: ExportTarget
) -> Existing | None:
    """Return what `target`'s collection already holds, or None if absent.

    Read-only: a GET for the collection and, for a court-scoped target, one
    single-point scroll under the `court_code` filter. A 404 means the
    collection does not exist yet, which is the normal, safe case.
    """
    response = client.get(f"{qdrant_url}/collections/{target.collection}")
    if response.status_code == COLLECTION_NOT_FOUND:
        return None
    response.raise_for_status()
    points = int(response.json()["result"].get("points_count") or 0)
    if target.court_code is None:
        return Existing(points=points, matching=points > 0)
    probe = client.post(
        f"{qdrant_url}/collections/{target.collection}/points/scroll",
        json={
            "limit": 1,
            "with_payload": False,
            "with_vector": False,
            "filter": court_code_filter(target.court_code),
        },
    )
    probe.raise_for_status()
    return Existing(points=points, matching=bool(probe.json()["result"]["points"]))


def _court_scoped_hint(target: ExportTarget) -> str:
    """Return the extra line a shared-collection refusal needs."""
    if not target.court_code:
        return ""
    return (
        f"      (that removes EVERY court in {target.collection}; to drop only "
        f"court_code {target.court_code!r}, POST points/delete with a "
        "court_code filter -- see "
        "openlaw.index.hc_upsert.delete_hc_court_points)\n"
    )


def clobber_refusal(
    target: ExportTarget,
    existing: Existing | None,
    resumable: bool,
    qdrant_url: str = "$OPENLAW_QDRANT_URL",
) -> str | None:
    """Return why `target` must not be written, or None when it is safe.

    Safe when the collection is absent, when it is empty, when the points in
    it belong to a different court, or when this run's own checkpoint proves
    those points came from an earlier, interrupted run of this same fetch.
    Anything else is somebody's corpus and is not ours to overwrite.
    """
    if existing is None or not existing.matching or resumable:
        return None
    scope = f" (including court_code {target.court_code!r})" if target.court_code else ""
    return (
        f"{target.collection} already holds {existing.points:,} points{scope} "
        f"and no checkpoint from this fetch explains them -- nothing was "
        f"written.\n"
        f"      To keep that corpus, drop {target.key} from --collections/"
        f"--courts and re-run.\n"
        f"      To replace it, delete it deliberately first:\n"
        f"        curl -X DELETE "
        f"'{qdrant_url}/collections/{target.collection}'\n"
        + _court_scoped_hint(target)
        + "      then re-run this command."
    )


def refuse_to_clobber(
    client: httpx.Client,
    qdrant_url: str,
    targets: Sequence[ExportTarget],
    resumable: frozenset[str],
) -> None:
    """Check every target before writing any of them, or exit explaining why.

    Checked up front, as one gate, so a six-target fetch cannot write three
    collections and then stop on the fourth -- which would leave a corpus
    `/readyz` calls degraded for reasons nothing records.
    """
    refusals: list[str] = []
    for target in targets:
        existing = inspect_collection(client, qdrant_url, target)
        refusal = clobber_refusal(
            target, existing, target.key in resumable, qdrant_url
        )
        if refusal:
            refusals.append(refusal)
    if refusals:
        raise SystemExit(
            "[Fetch] REFUSING to overwrite an existing corpus:\n  - "
            + "\n  - ".join(refusals)
        )


