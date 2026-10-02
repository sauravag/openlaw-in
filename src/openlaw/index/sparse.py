"""BM25 sparse vectors, built from chunk text under one consistent scheme.

The published sparse vectors cannot be used as they stand. Sampling 5,000 random
`acts_india` points found two incompatible weighting schemes mixed together:
51.7% BM25-saturated (values capped near 2.2) and 47.6% raw term counts (values
to 16+), with no pattern by jurisdiction or document type. Hybrid search sums
these weights, so a raw-count chunk outscores a BM25 chunk by roughly 7x for
reasons unrelated to relevance.

Stored values carry only BM25's **term-frequency** component. The inverse
document frequency is applied by Qdrant at query time via the collection's
`modifier: "idf"`, which derives it from real collection statistics and stays
correct as the collection grows. That split is what keeps every stored value
under the `k1 + 1` ceiling, which is the invariant the published data broke.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Iterable

SPARSE_VECTOR_NAME = "sparse"

# Standard BM25 constants. k1 sets how fast term frequency saturates; b sets how
# strongly length is normalised. These are the usual defaults and are not tuned
# against the corpus — tuning belongs with the Plan 3 evaluation harness, where
# the effect on recall can actually be measured.
BM25_K1 = 1.2
BM25_B = 0.75

# Alphanumeric runs. Keeping digits attached to letters preserves legal terms of
# art such as "498A" and "377", which are how citizens and lawyers alike refer to
# sections; splitting them would make section lookup impossible.
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

_HASH_BYTES = 4


def tokenise(text: str) -> list[str]:
    """Return the lowercase alphanumeric tokens of `text`, in order."""
    return _TOKEN_PATTERN.findall(text.lower())


def token_index(token: str) -> int:
    """Return the sparse-vector slot for `token`.

    Hashed rather than looked up in a vocabulary, so indexing needs no shared
    dictionary and a query process can compute the same slot years later. The
    digest is truncated to 32 bits, matching the index range the published
    vectors use.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=_HASH_BYTES).digest()
    return int.from_bytes(digest, "big")


def bm25_term_weight(
    frequency: int,
    doc_len: float,
    avg_len: float,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """Return BM25's term-frequency weight, bounded above by `k1 + 1`.

    `avg_len <= 0` disables length normalisation rather than dividing by zero,
    so a build cannot fail on an empty or unmeasured sample.
    """
    if avg_len <= 0:
        normalised_length = 1.0
    else:
        normalised_length = doc_len / avg_len
    denominator = frequency + k1 * (1 - b + b * normalised_length)
    if denominator == 0:
        return 0.0
    return frequency * (k1 + 1) / denominator


def bm25_vector(
    text: str,
    avg_len: float,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> dict[str, list]:
    """Return the Qdrant sparse vector for `text`.

    Shaped `{"indices": [...], "values": [...]}`, indices ascending. Text with no
    alphanumeric content yields an empty vector, which Qdrant accepts.
    """
    tokens = tokenise(text)
    if not tokens:
        return {"indices": [], "values": []}

    doc_len = float(len(tokens))
    weights: dict[int, float] = {}
    for token, frequency in Counter(tokens).items():
        weights[token_index(token)] = bm25_term_weight(
            frequency, doc_len=doc_len, avg_len=avg_len, k1=k1, b=b
        )

    indices = sorted(weights)
    return {"indices": indices, "values": [weights[index] for index in indices]}


def average_token_count(texts: Iterable[str]) -> float:
    """Return the mean token count over `texts`, or 0.0 when there are none."""
    total = 0
    count = 0
    for text in texts:
        total += len(tokenise(text))
        count += 1
    return total / count if count else 0.0
