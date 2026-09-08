"""Government source URL resolution.

The product's core guarantee is that every indexed chunk resolves to a government
source document. Judgments get their URL from the KanoonGPT join, because vaquill's
judgment `source_url` is empty. Provisions carry their own India Code URL.
"""

from openlaw.corpus.join import MetadataIndex
from openlaw.corpus.schema import JudgmentChunk, Provision


class CitationUnresolvable(Exception):
    """Raised when a chunk has no government source URL and must not be indexed."""


def resolve_judgment_url(chunk: JudgmentChunk, index: MetadataIndex) -> str | None:
    """Return the government PDF URL for `chunk`, or None when unresolvable."""
    metadata = index.lookup(chunk.join_key)
    if metadata is None:
        return None
    return metadata.source_pdf_url or None


def resolve_provision_url(provision: Provision) -> str | None:
    """Return the India Code URL for `provision`, or None when absent."""
    return provision.source_url.strip() or None


def require_url(url: str | None, chunk_id: str) -> str:
    """Return `url`, raising CitationUnresolvable when it is missing.

    This is the strict enforcement point for the core guarantee. Batch drivers
    that prefer to count and skip unresolvable chunks call the `resolve_*`
    functions directly instead.
    """
    if not url or not url.strip():
        raise CitationUnresolvable(
            f"chunk {chunk_id} has no government source URL; refusing to index"
        )
    return url.strip()
