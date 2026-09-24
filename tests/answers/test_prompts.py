from openlaw.answers.prompts import SYSTEM_PROMPT, build_context_message
from openlaw.retrieval.context import ChunkKind, RetrievalContext, RetrievedChunk


def _context() -> RetrievalContext:
    return RetrievalContext(
        query="my landlord kept my deposit",
        chunks=(
            RetrievedChunk(
                chunk_id="IND_central_1_12",
                text="A landlord shall refund the deposit within one month.",
                source_url="https://www.indiacode.nic.in/handle/123456789/1",
                score=0.9,
                kind=ChunkKind.PROVISION,
                title="Model Tenancy Act",
            ),
        ),
    )


def test_system_prompt_requires_chunk_ids_and_a_sufficiency_flag() -> None:
    assert "chunk_id" in SYSTEM_PROMPT
    assert "sufficient" in SYSTEM_PROMPT


def test_system_prompt_states_it_is_information_not_advice() -> None:
    assert "not legal advice" in SYSTEM_PROMPT.lower()


def test_system_prompt_instructs_plain_language() -> None:
    assert "plain language" in SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_soliciting_identifying_details() -> None:
    assert "aadhaar" in SYSTEM_PROMPT.lower()


def test_system_prompt_contains_no_per_turn_content() -> None:
    """It sits before the cache breakpoint, so it must be byte-identical each turn."""
    assert "landlord" not in SYSTEM_PROMPT.lower()


def test_context_message_is_a_user_turn() -> None:
    assert build_context_message(_context())["role"] == "user"


def test_context_message_labels_each_chunk_with_its_id() -> None:
    """The model can only cite ids it can see."""
    text = build_context_message(_context())["content"][0]["text"]
    assert "IND_central_1_12" in text


def test_context_message_includes_the_chunk_text() -> None:
    text = build_context_message(_context())["content"][0]["text"]
    assert "refund the deposit within one month" in text


def test_context_message_includes_the_question() -> None:
    text = build_context_message(_context())["content"][0]["text"]
    assert "my landlord kept my deposit" in text


def test_context_message_carries_no_cache_control() -> None:
    """Retrieved chunks change every turn; caching them yields a 0% hit rate."""
    block = build_context_message(_context())["content"][0]
    assert "cache_control" not in block


def test_context_message_handles_an_empty_context() -> None:
    message = build_context_message(RetrievalContext(query="q", chunks=()))
    assert message["role"] == "user"
    assert "no source excerpts" in message["content"][0]["text"]
