"""Shared fixtures for the LLM provider contract tests.

Each provider_case fixture entry is (provider_instance, MockSDK).
MockSDK.raise_next(kind) queues the SDK-specific exception for the next call,
exercising each adapter's _map_error path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_httpx_response(status_code: int) -> httpx.Response:
    """Create an httpx.Response with a dummy request attached (required by openai/anthropic)."""
    request = httpx.Request("POST", "https://api.example.com/v1/endpoint")
    return httpx.Response(status_code=status_code, request=request)


# ── Mock SDK helpers ──────────────────────────────────────────────────────────


class GeminiMockSDK:
    """Intercepts GeminiProvider's sync generate calls via the mock client."""

    def __init__(self, mock_client: MagicMock) -> None:
        self._queued: Exception | None = None

        def _generate(*args: Any, **kwargs: Any) -> MagicMock:
            if self._queued is not None:
                err, self._queued = self._queued, None
                raise err
            resp = MagicMock()
            resp.text = "DIRECT"
            return resp

        mock_chunk = MagicMock()
        mock_chunk.text = "hello"
        mock_client.models.generate_content.side_effect = _generate
        mock_client.models.generate_content_stream.return_value = [mock_chunk]

    def raise_next(self, kind: str) -> None:
        from google.api_core import exceptions as gexc

        self._queued = {
            "auth": gexc.PermissionDenied("auth"),
            "rate": gexc.ResourceExhausted("rate"),
            "unavailable": gexc.ServiceUnavailable("unavailable"),
        }[kind]


class OpenAIMockSDK:
    """Intercepts OpenAIProvider's async chat.completions.create calls."""

    def __init__(self, mock_client: AsyncMock) -> None:
        self._queued: Exception | None = None

        async def _chat_create(*args: Any, **kwargs: Any) -> Any:
            if self._queued is not None:
                err, self._queued = self._queued, None
                raise err
            if kwargs.get("stream"):

                async def _async_chunks():
                    event = MagicMock()
                    event.choices[0].delta.content = "hello"
                    yield event

                return _async_chunks()
            resp = MagicMock()
            resp.choices[0].message.content = "DIRECT"
            return resp

        mock_client.chat.completions.create.side_effect = _chat_create

    def raise_next(self, kind: str) -> None:
        import openai

        codes = {"auth": 401, "rate": 429, "unavailable": 503}
        r = _make_httpx_response(codes[kind])
        if kind == "auth":
            self._queued = openai.AuthenticationError("auth", response=r, body=None)
        elif kind == "rate":
            self._queued = openai.RateLimitError("rate", response=r, body=None)
        else:
            self._queued = openai.APIStatusError("unavailable", response=r, body=None)


class AnthropicMockSDK:
    """Intercepts AnthropicProvider's async messages calls."""

    def __init__(self, mock_client: AsyncMock) -> None:
        self._queued: Exception | None = None

        async def _messages_create(*args: Any, **kwargs: Any) -> MagicMock:
            from anthropic.types import TextBlock

            if self._queued is not None:
                err, self._queued = self._queued, None
                raise err
            text_block = MagicMock(spec=TextBlock)
            text_block.text = "DIRECT"
            resp = MagicMock()
            resp.content = [text_block]
            return resp

        mock_client.messages.create.side_effect = _messages_create

        # messages.stream() must be a sync callable returning an async context manager
        # (not AsyncMock, which would return a coroutine when called)
        mock_stream_inner = MagicMock()
        mock_stream_inner.text_stream = _async_text_gen(["hello"])
        stream_cm = AsyncMock()
        stream_cm.__aenter__.return_value = mock_stream_inner
        stream_cm.__aexit__.return_value = False
        mock_client.messages.stream = MagicMock(return_value=stream_cm)

    def raise_next(self, kind: str) -> None:
        import anthropic

        codes = {"auth": 401, "rate": 429, "unavailable": 503}
        r = _make_httpx_response(codes[kind])
        if kind == "auth":
            self._queued = anthropic.AuthenticationError("auth", response=r, body=None)
        elif kind == "rate":
            self._queued = anthropic.RateLimitError("rate", response=r, body=None)
        else:
            self._queued = anthropic.APIStatusError("unavailable", response=r, body=None)


async def _async_text_gen(items: list[str]):
    for item in items:
        yield item


# ── provider_case fixture ─────────────────────────────────────────────────────


@pytest.fixture(params=["gemini", "openai", "anthropic"])
def provider_case(request):
    kind = request.param

    if kind == "gemini":
        mock_client = MagicMock()
        with patch("llm.gemini.genai.Client", return_value=mock_client):
            from llm.gemini import GeminiProvider

            provider = GeminiProvider(api_key="fake-key")
        sdk = GeminiMockSDK(mock_client)
        return provider, sdk

    if kind == "openai":
        mock_client = AsyncMock()
        with patch("llm.openai.AsyncOpenAI", return_value=mock_client):
            from llm.openai import OpenAIProvider

            provider = OpenAIProvider(api_key="fake-key")
        sdk = OpenAIMockSDK(mock_client)
        return provider, sdk

    # anthropic
    mock_client = AsyncMock()
    with patch("llm.anthropic.AsyncAnthropic", return_value=mock_client):
        from llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="fake-key")
    sdk = AnthropicMockSDK(mock_client)
    return provider, sdk
