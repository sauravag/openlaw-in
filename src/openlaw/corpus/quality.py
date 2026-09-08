"""Quality filters for measured defects in the vaquill judgment corpus.

One defect is handled here:

1. Judge extraction emits parser noise. Observed real values include
   ``['which']`` and ``['SH. ANIL KUMAR SISODIA', 'vide']``.

A second defect — stray Supreme Court Reports margin letters (``A``-``H``)
inline in English Supreme Court text — was deliberately NOT handled, after
measurement showed the obvious regex-based fix corrupts far more text than it
cleans. Do not re-add margin-letter scrubbing without solving this problem:

A single capital letter A-H sitting between two lowercase words is NOT reliably
a margin marker. It is, overwhelmingly, a party placeholder — the dominant
idiom in Indian statutory illustrations and judgments ("if A owes B a sum of
money", "A agrees to sell to B a horse", "as between A and B the contract").
An earlier implementation tried to distinguish the two with a regex and
failed: measured against the real corpus, it fired on 60.5% of English Supreme
Court chunks and 1.5% of central legislation provisions, silently deleting
party letters out of legal quotations. This product's entire value
proposition is quoting Indian law accurately, so mass-mutating quoted text is
worse than leaving OCR/typesetting noise in place. Margin letters are left in
the text on purpose.
"""

# Lowercase function words that indicate a mis-parsed judge field.
_JUDGE_NOISE: frozenset[str] = frozenset(
    {
        "which", "vide", "and", "the", "of", "for", "with", "that", "this",
        "hon", "honble", "before", "coram", "per", "dated", "order", "judgment",
    }
)
_MIN_JUDGE_LENGTH = 3


def is_plausible_judge_name(name: str | None) -> bool:
    """Return True when `name` looks like a judge's name rather than parser noise."""
    if not name:
        return False
    candidate = name.strip()
    if len(candidate) < _MIN_JUDGE_LENGTH:
        return False
    if candidate.lower().strip(".") in _JUDGE_NOISE:
        return False
    # A real name carries at least one uppercase letter; noise is lowercase prose.
    return any(character.isupper() for character in candidate)


def clean_judge_names(names: list[str] | None) -> tuple[str, ...]:
    """Return only the plausible judge names in `names`, order preserved."""
    if not names:
        return ()
    return tuple(name.strip() for name in names if is_plausible_judge_name(name))
