"""Query embedding.

The corpus was embedded with voyage-4 using `input_type="document"`. Queries must
use the same model but `input_type="query"` -- Voyage applies different prefixes to
each side, and mixing them measurably degrades retrieval (0.9886 vs 0.9825 cosine
on documents, measured).

**Decided (measured): queries are embedded LOCALLY with `voyage-4-nano`, not
through the Voyage API.** `voyage-4-nano` is Apache-2.0 open weights that share the
voyage-4 embedding space -- the same text embedded by nano and by voyage-4 gives
0.951 mean cosine (p05 0.92) against 0.362 for unrelated text, and nano queries hit
85% top-10 against the stored voyage-4 document vectors. That removes the API key,
the per-query cost, and the vendor dependency from the request path.

`LocalNanoQueryEmbedder` (see `openlaw.retrieval.local_nano`) is therefore the
default query embedder; `VoyageQueryEmbedder` below stays available as a
configurable alternative (self-hosters without local GPU/CPU headroom, or anyone
who wants the vendor's own inference). Both must emit 1024-dim vectors and use the
query side of the model (nano's query prompt / `input_type="query"`), never the
document side.
"""

import hashlib
from typing import Protocol

from openlaw.index.embed import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL

QUERY_INPUT_TYPE = "query"


class QueryEmbedder(Protocol):
    """Anything that can turn a user query into a vector."""

    def embed_query(self, text: str) -> list[float]:
        """Return the query vector for `text`."""
        ...


class VoyageQueryEmbedder:
    """A `QueryEmbedder` backed by the Voyage AI API.

    The configurable alternative to the default `LocalNanoQueryEmbedder` -- costs
    an API call and a key per query, but needs no local model weights or compute.
    """

    def __init__(self, api_key: str, model: str = EMBEDDING_MODEL) -> None:
        import voyageai

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed_query(self, text: str) -> list[float]:
        """Embed `text` as a query, returning a single vector."""
        if not text or not text.strip():
            raise ValueError("cannot embed an empty query")
        result = self._client.embed(
            [text], model=self._model, input_type=QUERY_INPUT_TYPE
        )
        return result.embeddings[0]


class StubQueryEmbedder:
    """A deterministic `QueryEmbedder` for tests -- no network, no API key, no model.

    Derives a stable pseudo-vector from the SHA-256 of the text, so equal inputs
    give equal vectors and different inputs differ.
    """

    def __init__(self, vector: list[float] | None = None) -> None:
        self._fixed = vector

    def embed_query(self, text: str) -> list[float]:
        """Return a deterministic vector of the corpus dimensionality."""
        if not text or not text.strip():
            raise ValueError("cannot embed an empty query")
        if self._fixed is not None:
            return list(self._fixed)

        digest = hashlib.sha256(text.strip().encode("utf-8")).digest()
        return [
            digest[index % len(digest)] / 255.0
            for index in range(EMBEDDING_DIMENSIONS)
        ]
