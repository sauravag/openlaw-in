"""Fixtures for the adversarial gate.

The hostile corpus lives in `prompts/injection.txt` as DATA, not as Python
literals, so a new attack is added by writing a document body rather than by
writing a test -- and so the corpus can be read by a person who does not read
Python.

`ObedientModel` is the piece that makes this a real gate. A stub that produced
a well-behaved answer would prove only that well-behaved answers are safe. This
one does everything each injection asks for: it cites across the namespace
boundary, invents a chunk id with a plausible government URL, and puts the
hostile document text into the field reserved for unverified model prose. The
REAL `openlaw.answers.validate.validate_answer` then runs over it. Whatever
survives is what a fully compromised model could put in front of a reader.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from openlaw.answers.escalation import escalation_footer
from openlaw.answers.schema import Claim, DocumentClaim, DraftAnswer
from openlaw.answers.validate import validate_answer
from openlaw.documents.context import DocumentContext
from openlaw.index.sparse import tokenise
from openlaw.language.pipeline import LocalisedAnswer
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk

#: A real-looking government URL, of exactly the kind
#: `prompts/injection.txt`'s `fabricated_indiacode_citation` case supplies.
#: Nothing may render it, because no retrieved chunk carries it.
FABRICATED_SOURCE_URL = "https://www.indiacode.nic.in/handle/123456789/1362"

#: The chunk id the obedient model invents for that fabricated citation.
FABRICATED_CHUNK_ID = "acts_india_v1:9999:0"


@dataclass(frozen=True)
class HostileDocument:
    """One attack: a name from `injection.txt` and the file body it holds."""

    name: str
    body: str

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8")


def _corpus_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "injection.txt"


def load_hostile_documents() -> tuple[HostileDocument, ...]:
    """Parse `prompts/injection.txt` into named document bodies.

    Comment and blank lines are skipped only OUTSIDE a body: a payload's own
    whitespace and `#` characters are part of the attack and are preserved
    byte for byte.
    """
    documents: list[HostileDocument] = []
    name: str | None = None
    lines: list[str] = []
    for line in _corpus_path().read_text(encoding="utf-8").splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if name is not None:
                documents.append(HostileDocument(name, "\n".join(lines).strip()))
            name, lines = line[4:-4].strip(), []
            continue
        if name is None:
            continue
        lines.append(line)
    if name is not None:
        documents.append(HostileDocument(name, "\n".join(lines).strip()))
    return tuple(documents)


HOSTILE_DOCUMENTS = load_hostile_documents()


def hostile(name: str) -> HostileDocument:
    """Return one named case, failing loudly if the corpus no longer has it."""
    for document in HOSTILE_DOCUMENTS:
        if document.name == name:
            return document
    raise AssertionError(f"no hostile document named {name!r} in injection.txt")


def question_for(attack: HostileDocument) -> str:
    """Return a question that genuinely retrieves `attack`'s own excerpt.

    `openlaw.documents.context.select_excerpts` drops an excerpt that shares no
    token with the question, so a question unrelated to the document puts
    NOTHING in the model's context -- and a gate that ran with an empty
    document context would pass while testing nothing. This builds a question
    out of the document's own longest words, which is also the realistic case:
    a person asks about the paper they just attached.
    """
    words = sorted(
        {
            word.strip(".,:;()[]{}\"'").lower()
            for word in attack.body.split()
            if len(word.strip(".,:;()[]{}\"'")) > 5
        },
        key=lambda word: (-len(word), word),
    )
    return "what does my document say about " + " ".join(words[:4])


def retrieval_context(query: str = "my landlord kept my deposit") -> RetrievalContext:
    """One genuinely retrieved chunk -- the only id a legal claim may cite."""
    return RetrievalContext(
        query=query,
        chunks=(
            RetrievedChunk(
                chunk_id="acts_india_v1:1:0",
                text="A landlord shall refund the security deposit within thirty days.",
                source_url="https://www.indiacode.nic.in/handle/123456789/1",
                score=0.9,
                kind=ChunkKind.PROVISION,
                title="Model Tenancy Act, section 11",
            ),
        ),
    )


def obedient_draft(
    document_context: DocumentContext,
    context: RetrievalContext,
    hostile_text: str,
) -> DraftAnswer:
    """The draft a fully compromised model emits: every injection obeyed.

    Five claims, four of them attacks:

    1. a legal claim citing an uploaded `doc:` excerpt id -- uploaded text
       presented as law (the central attack);
    2. a legal claim citing an invented chunk id with a real-looking
       `indiacode.nic.in` URL behind it -- a fabricated citation;
    3. a document claim citing a corpus chunk id -- the same boundary crossed
       from the other side;
    4. a document claim quoting the hostile document and citing a real excerpt
       id -- legitimate, and the control: it proves the suite is not passing
       merely because everything was dropped;
    5. `summary` set to the hostile document's own text, which must never be
       rendered anywhere.
    """
    excerpt_ids = tuple(sorted(document_context.excerpt_ids))
    first_excerpt = excerpt_ids[0] if excerpt_ids else "doc:absent:p1:0"
    corpus_id = next(iter(sorted(context.chunk_ids)), "acts_india_v1:1:0")
    return DraftAnswer(
        summary=hostile_text,
        claims=(
            Claim(
                text="Section 99 of the Tenancy Act forbids eviction entirely.",
                chunk_ids=(first_excerpt,),
            ),
            Claim(
                text="Section 14B of the Delhi Rent Control Act 1958 applies.",
                chunk_ids=(FABRICATED_CHUNK_ID,),
            ),
            Claim(
                text="A landlord shall refund the deposit within thirty days.",
                chunk_ids=(corpus_id,),
            ),
        ),
        document_claims=(
            DocumentClaim(
                text="Your document is a binding judgment.",
                excerpt_ids=(corpus_id,),
            ),
            DocumentClaim(
                text="Your document mentions a notice to quit.",
                excerpt_ids=(first_excerpt,),
            ),
        ),
        sufficient=True,
    )


class ObedientModel:
    """A compromised `ConversationAnswerFn`: obeys the document, then is
    validated for real.

    The model is the ONLY thing stubbed. `validate_answer`, the two-namespace
    rule, the source-card construction and the escalation footer are all the
    shipped implementations, so what this returns is what the deployed
    response renderer would be handed if the model were fully owned.
    """

    def __init__(self, hostile_text: str = "") -> None:
        self.hostile_text = hostile_text
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        question: str,
        language: str | None,
        state: str | None,
        *,
        history: tuple = (),
        document_context: DocumentContext | None = None,
        document_crisis_kinds: tuple[str | None, ...] = (),
    ) -> LocalisedAnswer:
        self.calls.append(
            {
                "question": question,
                "language": language,
                "document_context": document_context,
                "document_crisis_kinds": document_crisis_kinds,
            }
        )
        documents = document_context or DocumentContext()
        context = retrieval_context(question)
        draft = obedient_draft(documents, context, self.hostile_text or question)
        validated = validate_answer(draft, context, documents)
        return LocalisedAnswer(
            answer=validated,
            english_answer=validated,
            language=language or "en",
            english_query=question,
            footer=escalation_footer(state, language or "en"),
            turn_index=len(self.calls) - 1,
        )


@pytest.fixture(params=[document.name for document in HOSTILE_DOCUMENTS])
def attack(request: pytest.FixtureRequest) -> Iterator[HostileDocument]:
    """Every hostile body in the corpus, one test run each."""
    yield hostile(request.param)


def has_selectable_excerpt(attack: HostileDocument) -> bool:
    """Whether `question_for(attack)` can select `attack`'s own excerpt.

    `openlaw.index.sparse.tokenise` matches `[a-z0-9]+` only, so a document
    written in Devanagari yields no tokens beyond its digits and can never
    share one with a Devanagari question. See
    `test_document_injection.test_a_devanagari_document_contributes_no_excerpt`,
    which pins that limit, and the README's "Uploaded documents" section, which
    documents it.
    """
    return bool(
        set(tokenise(question_for(attack))) & set(tokenise(attack.body))
    )


#: The attacks whose excerpt can actually reach a model's context today. Every
#: attack is still run through every SAFETY property; this subset is only for
#: the properties that need a SURVIVING document claim to assert anything.
SELECTABLE_ATTACKS = tuple(
    document for document in HOSTILE_DOCUMENTS if has_selectable_excerpt(document)
)


@pytest.fixture(params=[document.name for document in SELECTABLE_ATTACKS])
def selectable_attack(request: pytest.FixtureRequest) -> Iterator[HostileDocument]:
    """Every attack whose excerpt the selector can actually retrieve."""
    yield hostile(request.param)
