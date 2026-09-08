"""voyage-4 embedding of judgment chunks.

The published collections were embedded with the voyage-4 series at 1024
dimensions. Vectors from any other model live in a different space, so similarity
scores against `acts_india` would be meaningless. This constant is load-bearing.
"""

from collections.abc import Iterable, Iterator
from typing import Protocol, TypeVar

EMBEDDING_MODEL = "voyage-4"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_BATCH_SIZE = 128

T = TypeVar("T")


class Embedder(Protocol):
    """Anything that can turn document texts into vectors."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in the same order."""
        ...


def batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Yield `items` in lists of at most `size`."""
    if size <= 0:
        raise ValueError(f"batch size must be positive, got {size}")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class VoyageEmbedder:
    """An `Embedder` backed by the Voyage AI API."""

    def __init__(self, api_key: str, model: str = EMBEDDING_MODEL) -> None:
        import voyageai

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts` as documents, returning one vector each."""
        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings
