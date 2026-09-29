"""The retrieval context — the set of chunks a turn is allowed to cite.

`RetrievalContext.chunk_ids` is the authority the citation validator checks
against. A chunk cannot enter the context without a government source URL, so
anything the model is permitted to cite is provably attributable.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ChunkKind(Enum):
    """What kind of legal material a chunk holds."""

    PROVISION = "provision"
    JUDGMENT = "judgment"


class RetrievedChunk(BaseModel):
    """One retrieved chunk, with everything needed to render a citation.

    The optional fields are the ones only some payloads carry: `title` on a
    provision, `case_id`/`court_code`/`decision_date`/`judges`/`headnote` on
    a judgment. An absent value stays absent (`None`, or an empty tuple for
    `judges`) and is never filled in with a placeholder — a renderer must be
    able to tell "the corpus does not record this" from a real value.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    score: float
    kind: ChunkKind
    title: str | None = None
    case_id: str | None = None
    court_code: str | None = None
    decision_date: str | None = None
    judges: tuple[str, ...] = ()
    headnote: str | None = None


class RetrievalContext(BaseModel):
    """Everything retrieved for one turn."""

    model_config = ConfigDict(frozen=True)

    query: str
    chunks: tuple[RetrievedChunk, ...] = ()

    @property
    def chunk_ids(self) -> frozenset[str]:
        """The set of chunk ids a claim may legitimately cite this turn."""
        return frozenset(chunk.chunk_id for chunk in self.chunks)

    def by_id(self, chunk_id: str) -> RetrievedChunk | None:
        """Return the chunk with `chunk_id`, or None when it was not retrieved."""
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        return None
