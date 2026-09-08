"""Validation of indexed point payloads.

Enforces the product's core guarantee against the live index: every chunk carries
a government source URL and the corpus snapshot version.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationReport:
    """The outcome of validating a set of indexed points."""

    checked: int
    missing_url: list[str] = field(default_factory=list)
    missing_version: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when no point is missing a URL or a corpus version."""
        return not self.missing_url and not self.missing_version


def validate_payloads(points: Iterable[dict[str, Any]]) -> ValidationReport:
    """Validate that every point in `points` carries a URL and corpus version."""
    checked = 0
    missing_url: list[str] = []
    missing_version: list[str] = []

    for point in points:
        checked += 1
        payload = point.get("payload") or {}
        identifier = str(payload.get("chunk_id") or point.get("id") or "<unknown>")

        url = payload.get("source_url")
        if not url or not str(url).strip():
            missing_url.append(identifier)

        version = payload.get("corpus_version")
        if not version or not str(version).strip():
            missing_version.append(identifier)

    return ValidationReport(
        checked=checked, missing_url=missing_url, missing_version=missing_version
    )
