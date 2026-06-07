"""Concurrent multi-provider isolation test.

Alice with an OpenAI key and Bob with a Gemini key run concurrently;
each must receive output only from their own provider.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.anthropic import AnthropicProvider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider


def _make_gemini_provider(canned: str) -> GeminiProvider:
    mock_client = MagicMock()
    resp = MagicMock()
    resp.text = canned
    mock_client.models.generate_content.return_value = resp
    with patch("llm.gemini.genai.Client", return_value=mock_client):
        return GeminiProvider(api_key="sk-gemini")


def _make_openai_provider(canned: str) -> OpenAIProvider:
    mock_client = AsyncMock()
    resp = MagicMock()
    resp.choices[0].message.content = canned
    mock_client.chat.completions.create.return_value = resp
    with patch("llm.openai.AsyncOpenAI", return_value=mock_client):
        return OpenAIProvider(api_key="sk-openai")


def _make_anthropic_provider(canned: str) -> AnthropicProvider:
    from anthropic.types import TextBlock

    mock_client = AsyncMock()
    text_block = MagicMock(spec=TextBlock)
    text_block.text = canned
    resp = MagicMock()
    resp.content = [text_block]
    mock_client.messages.create.return_value = resp
    with patch("llm.anthropic.AsyncAnthropic", return_value=mock_client):
        return AnthropicProvider(api_key="sk-anthropic")


@pytest.mark.asyncio
async def test_openai_gemini_no_crosstalk():
    """Two users on different providers must see only their own provider's output."""
    alice_provider = _make_openai_provider("from-openai")
    bob_provider = _make_gemini_provider("from-gemini")

    async def alice_task():
        return await alice_provider.generate("Q?", "ctx", "DIRECT")

    async def bob_task():
        return await bob_provider.generate("Q?", "ctx", "DIRECT")

    a_answer, b_answer = await asyncio.gather(alice_task(), bob_task())
    assert a_answer == "from-openai"
    assert b_answer == "from-gemini"


@pytest.mark.asyncio
async def test_three_providers_concurrent():
    """Three concurrent users on Gemini / OpenAI / Anthropic must not cross-talk."""
    p_gemini = _make_gemini_provider("gemini-answer")
    p_openai = _make_openai_provider("openai-answer")
    p_anthropic = _make_anthropic_provider("anthropic-answer")

    g, o, a = await asyncio.gather(
        p_gemini.generate("Q?", "ctx", "DIRECT"),
        p_openai.generate("Q?", "ctx", "DIRECT"),
        p_anthropic.generate("Q?", "ctx", "DIRECT"),
    )
    assert g == "gemini-answer"
    assert o == "openai-answer"
    assert a == "anthropic-answer"


def test_each_provider_has_own_client():
    """Providers built with different keys hold distinct client instances."""
    with patch("llm.gemini.genai.Client") as mock_cls:
        mock_cls.side_effect = lambda api_key=None, **kw: MagicMock()
        p1 = GeminiProvider(api_key="key1")
        p2 = GeminiProvider(api_key="key2")
    assert p1._client is not p2._client, "providers must not share client state"
