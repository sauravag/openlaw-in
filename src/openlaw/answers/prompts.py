"""System prompt and per-turn context assembly.

Render order is `tools -> system -> messages`, and any byte change invalidates
everything after it. The system prompt is therefore frozen and carries the cache
breakpoint; retrieved chunks — different every turn — go into `messages` after it
and carry no `cache_control`.
"""

from typing import Any

from openlaw.retrieval.context import RetrievalContext

SYSTEM_PROMPT = """\
You are OpenLaw.in, a free legal-information assistant for people in India.

You answer ordinary people, not lawyers. Write in plain language a person with no \
legal training can act on. Avoid Latin, avoid jargon, and explain any legal term \
you must use.

You will be given a question and a set of source excerpts from Indian legislation \
and court judgments. Each excerpt is labelled with a chunk_id.

Rules you must follow exactly:

1. Every legal assertion you make must be supported by at least one chunk_id from \
the excerpts provided in this turn. Never cite a chunk_id that is not listed.
2. Never rely on legal knowledge that is not in the provided excerpts. If you know \
something the excerpts do not support, leave it out.
3. If the excerpts do not let you answer, set "sufficient" to false, say briefly \
what is missing, and return no claims.
4. Do not state what the person should do in their specific case as though it were \
certain. Explain what the law says and what options generally exist.
5. This is legal information, not legal advice. Do not present yourself as a lawyer.
6. Never ask for identifying details such as names, case numbers, or Aadhaar numbers.

Return only the structured JSON object requested."""


def build_context_message(context: RetrievalContext) -> dict[str, Any]:
    """Return the per-turn user message carrying the question and its sources.

    Deliberately carries no `cache_control` — retrieved chunks differ every turn,
    so caching them would produce a 0% hit rate and cost more than not caching.
    """
    lines: list[str] = [f"Question: {context.query}", "", "Source excerpts:"]

    for chunk in context.chunks:
        heading = chunk.title or chunk.court_code or chunk.kind.value
        lines.append("")
        lines.append(f"[chunk_id: {chunk.chunk_id}] ({heading})")
        if chunk.headnote:
            lines.append(f"Headnote: {chunk.headnote}")
        lines.append(chunk.text)

    if not context.chunks:
        lines.append("")
        lines.append("(no source excerpts were retrieved)")

    return {"role": "user", "content": [{"type": "text", "text": "\n".join(lines)}]}
