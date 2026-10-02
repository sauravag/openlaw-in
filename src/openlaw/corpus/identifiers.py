"""Normalisation of Indian court case identifiers.

Two identifier families join the datasets:

- Neutral citation, e.g. vaquill ``2016_INSC_1151`` and KanoonGPT ``2016INSC1151``.
  Normalising both to ``2016INSC1151`` yields a 100% Supreme Court join.
- CNR (Case Number Record), e.g. ``DLHC010517432014``. A 16-character code whose
  first four characters identify the court. Yields a 98.6% Delhi High Court join.

Both families are normalised identically here because the join only needs
strings to compare equal, not to know which family a given value belongs to.
"""

import re

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]")


def normalise_identifier(raw: str | None) -> str:
    """Return `raw` uppercased with every non-alphanumeric character removed.

    Returns an empty string when `raw` is None, blank, or has no alphanumerics.
    """
    if not raw:
        return ""
    return _NON_ALPHANUMERIC.sub("", raw.strip().upper())
