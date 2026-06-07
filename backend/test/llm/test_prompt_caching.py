"""Prompt-caching structure tests.

Verify each adapter places the STABLE instruction (routing rubric / synthesis format contract) in
its cache-eligible position and keeps VARIABLE content (the query + retrieved context) out of it:

* Anthropic — stable text in a `system` block carrying `cache_control={"type": "ephemeral"}`.
* OpenAI    — stable text in the leading `system` message (automatic prefix caching).
* Gemini    — stable text as the leading prefix of the single combined prompt (implicit caching).

The decisive invariant (caching skill "prefix changes" sharp edge): the cached prefix must contain
NO per-request data, or the hit rate collapses. Each test asserts the unique query + context
sentinels are absent from the cached prefix and present in the variable position.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm._prompts import ROUTING_SYSTEM, generation_system

_QUERY = "UNIQUE_QUERY_SENTINEL_zzz"
_CONTEXT = "UNIQUE_CONTEXT_SENTINEL_qqq"


# ── Anthropic: cache_control on the system block ──────────────────────────────


def _anthropic_provider_and_client():
    mock_client = AsyncMock()
    with patch("llm.anthropic.AsyncAnthropic", return_value=mock_client):
        from llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="fake-key")
    return provider, mock_client


@pytest.mark.asyncio
async def test_anthropic_route_caches_rubric_system_block():
    provider, client = _anthropic_provider_and_client()

    text_block = MagicMock()
    text_block.text = "DIRECT"
    from anthropic.types import TextBlock

    text_block.__class__ = TextBlock  # satisfy isinstance check in adapter
    resp = MagicMock()
    resp.content = [text_block]
    client.messages.create.return_value = resp

    await provider.route(_QUERY, has_documents=True, web_allowed=True)

    kwargs = client.messages.create.call_args.kwargs
    system = kwargs["system"]
    # System is a list of cache-eligible blocks; the (only) block carries ephemeral cache_control.
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == ROUTING_SYSTEM
    # Stable prefix has NO per-request data; the query lives in the user message.
    assert _QUERY not in system[0]["text"]
    user_content = kwargs["messages"][0]["content"]
    assert _QUERY in user_content


@pytest.mark.asyncio
async def test_anthropic_generate_caches_format_contract_not_context():
    provider, client = _anthropic_provider_and_client()

    text_block = MagicMock()
    text_block.text = "answer"
    from anthropic.types import TextBlock

    text_block.__class__ = TextBlock
    resp = MagicMock()
    resp.content = [text_block]
    client.messages.create.return_value = resp

    await provider.generate(_QUERY, _CONTEXT, "RAG")

    kwargs = client.messages.create.call_args.kwargs
    system = kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == generation_system("RAG")
    # Neither the query nor the retrieved context may sit in the cached prefix.
    assert _QUERY not in system[0]["text"]
    assert _CONTEXT not in system[0]["text"]
    user_content = kwargs["messages"][0]["content"]
    assert _QUERY in user_content
    assert _CONTEXT in user_content


@pytest.mark.asyncio
async def test_anthropic_stream_caches_system_block():
    provider, client = _anthropic_provider_and_client()

    async def _text_gen():
        yield "hello"

    inner = MagicMock()
    inner.text_stream = _text_gen()
    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = inner
    stream_cm.__aexit__.return_value = False
    client.messages.stream = MagicMock(return_value=stream_cm)

    _ = [c async for c in provider.stream(_QUERY, _CONTEXT, "WEB")]

    kwargs = client.messages.stream.call_args.kwargs
    system = kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert _QUERY not in system[0]["text"]
    assert _CONTEXT not in system[0]["text"]


# ── OpenAI: stable system message is the prefix ───────────────────────────────


def _openai_provider_and_client():
    mock_client = AsyncMock()
    with patch("llm.openai.AsyncOpenAI", return_value=mock_client):
        from llm.openai import OpenAIProvider

        provider = OpenAIProvider(api_key="fake-key")
    return provider, mock_client


@pytest.mark.asyncio
async def test_openai_route_puts_rubric_first_in_system():
    provider, client = _openai_provider_and_client()
    resp = MagicMock()
    resp.choices[0].message.content = "DIRECT"
    client.chat.completions.create.return_value = resp

    await provider.route(_QUERY, has_documents=False, web_allowed=True)

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"  # stable prefix is first
    assert messages[0]["content"] == ROUTING_SYSTEM
    assert _QUERY not in messages[0]["content"]  # no per-request data in the cached prefix
    assert messages[1]["role"] == "user"
    assert _QUERY in messages[1]["content"]


@pytest.mark.asyncio
async def test_openai_generate_keeps_context_out_of_system():
    provider, client = _openai_provider_and_client()
    resp = MagicMock()
    resp.choices[0].message.content = "answer"
    client.chat.completions.create.return_value = resp

    await provider.generate(_QUERY, _CONTEXT, "RAG")

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == generation_system("RAG")
    assert _QUERY not in messages[0]["content"]
    assert _CONTEXT not in messages[0]["content"]
    assert _QUERY in messages[1]["content"]
    assert _CONTEXT in messages[1]["content"]


# ── Gemini: stable prefix leads the single combined prompt ────────────────────


def _gemini_provider_and_client():
    mock_client = MagicMock()
    with patch("llm.gemini.genai.Client", return_value=mock_client):
        from llm.gemini import GeminiProvider

        provider = GeminiProvider(api_key="fake-key")
    return provider, mock_client


@pytest.mark.asyncio
async def test_gemini_route_prompt_leads_with_stable_rubric():
    provider, client = _gemini_provider_and_client()
    resp = MagicMock()
    resp.text = "DIRECT"
    client.models.generate_content.return_value = resp

    await provider.route(_QUERY, has_documents=False, web_allowed=True)

    prompt = client.models.generate_content.call_args.kwargs["contents"]
    # Stable rubric is the literal prefix; the variable query comes strictly after it.
    assert prompt.startswith(ROUTING_SYSTEM)
    assert prompt.index(ROUTING_SYSTEM) < prompt.index(_QUERY)


@pytest.mark.asyncio
async def test_gemini_generate_prompt_leads_with_stable_system():
    provider, client = _gemini_provider_and_client()
    resp = MagicMock()
    resp.text = "answer"
    client.models.generate_content.return_value = resp

    await provider.generate(_QUERY, _CONTEXT, "RAG")

    prompt = client.models.generate_content.call_args.kwargs["contents"]
    stable = generation_system("RAG")
    assert prompt.startswith(stable)
    # Variable context + query both follow the stable prefix.
    assert prompt.index(stable) < prompt.index(_CONTEXT)
    assert prompt.index(stable) < prompt.index(_QUERY)
