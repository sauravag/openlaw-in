"""voyage-4 embedding of judgment chunks.

The published collections were embedded with the voyage-4 series at 1024
dimensions. Vectors from any other model live in a different space, so similarity
scores against `acts_india` would be meaningless. This constant is load-bearing.
"""

from collections.abc import Callable, Iterable, Iterator
from typing import Protocol, TypeVar

EMBEDDING_MODEL = "voyage-4"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_BATCH_SIZE = 128
DEFAULT_MAX_RETRIES = 5

# Voyage batches are sized in rows by `batched`, but the per-request limit that
# actually matters is tokens. A measured mean of ~908 tokens/chunk (p99 ~135K per
# 128-row batch) puts naive row-count batching well past most providers' per-request
# caps. `MAX_BATCH_TOKENS` is conservative headroom under that cap; `CHARS_PER_TOKEN`
# is a deliberately conservative (i.e. token-inflating) chars-per-token estimate, so
# actual token counts should run at or below the estimate, not above it.
MAX_BATCH_TOKENS = 60_000
CHARS_PER_TOKEN = 4

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


def _estimated_tokens(text: str) -> float:
    """Return a conservative token estimate for `text`."""
    return len(text) / CHARS_PER_TOKEN


def batched_by_tokens(
    items: Iterable[T],
    max_items: int,
    max_tokens: float,
    text_of: Callable[[T], str],
) -> Iterator[list[T]]:
    """Yield `items` in lists bounded by BOTH `max_items` and `max_tokens`.

    A batch closes as soon as it holds `max_items`, or before adding an item
    would push the estimated token total past `max_tokens` — whichever comes
    first. A single item whose own estimated token count already exceeds
    `max_tokens` is still yielded alone, one item per batch, rather than being
    dropped or blocking the stream forever.
    """
    if max_items <= 0:
        raise ValueError(f"max_items must be positive, got {max_items}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")

    batch: list[T] = []
    batch_tokens = 0.0
    for item in items:
        item_tokens = _estimated_tokens(text_of(item))
        would_overflow = batch and (batch_tokens + item_tokens > max_tokens)
        if would_overflow or len(batch) == max_items:
            yield batch
            batch = []
            batch_tokens = 0.0
        batch.append(item)
        batch_tokens += item_tokens
    if batch:
        yield batch


class VoyageEmbedder:
    """An `Embedder` backed by the Voyage AI API."""

    def __init__(
        self,
        api_key: str,
        model: str = EMBEDDING_MODEL,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        import voyageai

        self._client = voyageai.Client(api_key=api_key, max_retries=max_retries)
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts` as documents, returning one vector each."""
        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings
