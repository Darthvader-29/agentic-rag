"""Provider contract tests — same assertions run against all three adapters."""

import pytest

from exceptions import LLMAuthError, LLMRateLimitError, LLMUnavailableError
from llm.base import LLMProvider


@pytest.mark.asyncio
async def test_implements_protocol(provider_case):
    provider, _ = provider_case
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_route_returns_known_label(provider_case):
    provider, _ = provider_case
    decision = await provider.route("hi", has_documents=False, web_allowed=True)
    assert decision in ("RAG", "WEB", "DIRECT")


@pytest.mark.asyncio
async def test_generate_returns_text(provider_case):
    provider, _ = provider_case
    out = await provider.generate("Q?", "ctx", "DIRECT")
    assert isinstance(out, str) and out


@pytest.mark.asyncio
async def test_stream_yields_str_chunks(provider_case):
    provider, _ = provider_case
    chunks = [c async for c in provider.stream("Q?", "ctx", "DIRECT")]
    assert chunks and all(isinstance(c, str) for c in chunks)


@pytest.mark.parametrize(
    "kind, neutral",
    [
        ("auth", LLMAuthError),
        ("rate", LLMRateLimitError),
        ("unavailable", LLMUnavailableError),
    ],
)
@pytest.mark.asyncio
async def test_error_mapping(provider_case, kind, neutral):
    provider, mock_sdk = provider_case
    mock_sdk.raise_next(kind)
    with pytest.raises(neutral):
        await provider.generate("Q?", "ctx", "DIRECT")


def test_repr_never_contains_api_key(provider_case):
    provider, _ = provider_case
    assert "fake-key" not in repr(provider)
