"""Shared fixtures for the LLM provider contract tests.

Each ``provider_case`` entry is ``(provider_instance, MockSDK)``. ``MockSDK.raise_next(kind)`` queues
the SDK-specific exception for the next call, exercising each adapter's ``_map_error`` path;
``raise_next_times(kind, n)`` queues it for the next ``n`` calls, exercising the bounded-retry path
(R12). ``MockSDK.calls`` counts how many times the underlying SDK call was actually invoked.

Two provider fixtures, differing only in retry policy (R12):

* ``provider_case`` — built with ``max_retry_attempts=1`` so a single queued transient error maps
  to its neutral type immediately (the *mapping* contract; no retry noise).
* ``retrying_provider_case`` — built with ``max_retry_attempts=3`` and zero backoff so the retry/
  exhaustion behavior is exercised without real sleeps.
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
        self._queue: list[Exception] = []
        self.calls = 0

        def _generate(*args: Any, **kwargs: Any) -> MagicMock:
            self.calls += 1
            if self._queue:
                raise self._queue.pop(0)
            resp = MagicMock()
            resp.text = "DIRECT"
            return resp

        mock_chunk = MagicMock()
        mock_chunk.text = "hello"
        mock_client.models.generate_content.side_effect = _generate
        mock_client.models.generate_content_stream.return_value = [mock_chunk]

    @staticmethod
    def _exc(kind: str) -> Exception:
        from google.api_core import exceptions as gexc

        return {
            "auth": gexc.PermissionDenied("auth"),
            "rate": gexc.ResourceExhausted("rate"),
            "unavailable": gexc.ServiceUnavailable("unavailable"),
        }[kind]

    def raise_next(self, kind: str) -> None:
        self._queue = [self._exc(kind)]

    def raise_next_times(self, kind: str, n: int) -> None:
        self._queue = [self._exc(kind) for _ in range(n)]


class OpenAIMockSDK:
    """Intercepts OpenAIProvider's async chat.completions.create calls."""

    def __init__(self, mock_client: AsyncMock) -> None:
        self._queue: list[Exception] = []
        self.calls = 0

        async def _chat_create(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            if self._queue:
                raise self._queue.pop(0)
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

    @staticmethod
    def _exc(kind: str) -> Exception:
        import openai

        r = _make_httpx_response({"auth": 401, "rate": 429, "unavailable": 503}[kind])
        if kind == "auth":
            return openai.AuthenticationError("auth", response=r, body=None)
        if kind == "rate":
            return openai.RateLimitError("rate", response=r, body=None)
        return openai.APIStatusError("unavailable", response=r, body=None)

    def raise_next(self, kind: str) -> None:
        self._queue = [self._exc(kind)]

    def raise_next_times(self, kind: str, n: int) -> None:
        self._queue = [self._exc(kind) for _ in range(n)]


class AnthropicMockSDK:
    """Intercepts AnthropicProvider's async messages calls."""

    def __init__(self, mock_client: AsyncMock) -> None:
        self._queue: list[Exception] = []
        self.calls = 0

        async def _messages_create(*args: Any, **kwargs: Any) -> MagicMock:
            from anthropic.types import TextBlock

            self.calls += 1
            if self._queue:
                raise self._queue.pop(0)
            text_block = MagicMock(spec=TextBlock)
            text_block.text = "DIRECT"
            resp = MagicMock()
            resp.content = [text_block]
            resp.stop_reason = "end_turn"
            return resp

        mock_client.messages.create.side_effect = _messages_create

        # messages.stream() must be a sync callable returning an async context manager
        # (not AsyncMock, which would return a coroutine when called)
        mock_stream_inner = MagicMock()
        mock_stream_inner.text_stream = _async_text_gen(["hello"])
        final_msg = MagicMock()
        final_msg.stop_reason = "end_turn"
        mock_stream_inner.get_final_message = AsyncMock(return_value=final_msg)
        stream_cm = AsyncMock()
        stream_cm.__aenter__.return_value = mock_stream_inner
        stream_cm.__aexit__.return_value = False
        mock_client.messages.stream = MagicMock(return_value=stream_cm)

    @staticmethod
    def _exc(kind: str) -> Exception:
        import anthropic

        r = _make_httpx_response({"auth": 401, "rate": 429, "unavailable": 503}[kind])
        if kind == "auth":
            return anthropic.AuthenticationError("auth", response=r, body=None)
        if kind == "rate":
            return anthropic.RateLimitError("rate", response=r, body=None)
        return anthropic.APIStatusError("unavailable", response=r, body=None)

    def raise_next(self, kind: str) -> None:
        self._queue = [self._exc(kind)]

    def raise_next_times(self, kind: str, n: int) -> None:
        self._queue = [self._exc(kind) for _ in range(n)]


async def _async_text_gen(items: list[str]):
    for item in items:
        yield item


# ── provider builders + fixtures ──────────────────────────────────────────────


def _build_case(kind: str, **policy: Any):
    """Build (provider, mock_sdk) for ``kind`` with the given resilience ``policy`` kwargs."""
    if kind == "gemini":
        mock_client = MagicMock()
        with patch("llm.gemini.genai.Client", return_value=mock_client):
            from llm.gemini import GeminiProvider

            provider = GeminiProvider(api_key="fake-key", **policy)
        return provider, GeminiMockSDK(mock_client)

    if kind == "openai":
        mock_client = AsyncMock()
        with patch("llm.openai.AsyncOpenAI", return_value=mock_client):
            from llm.openai import OpenAIProvider

            provider = OpenAIProvider(api_key="fake-key", **policy)
        return provider, OpenAIMockSDK(mock_client)

    # anthropic
    mock_client = AsyncMock()
    with patch("llm.anthropic.AsyncAnthropic", return_value=mock_client):
        from llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="fake-key", **policy)
    return provider, AnthropicMockSDK(mock_client)


@pytest.fixture(params=["gemini", "openai", "anthropic"])
def provider_case(request):
    # max_retry_attempts=1 ⇒ no retry, so a single queued error maps immediately (mapping contract).
    return _build_case(request.param, max_retry_attempts=1, retry_backoff_seconds=0.0)


@pytest.fixture(params=["gemini", "openai", "anthropic"])
def retrying_provider_case(request):
    # 3 attempts, zero backoff ⇒ exercises retry/exhaustion deterministically without real sleeps.
    return _build_case(request.param, max_retry_attempts=3, retry_backoff_seconds=0.0)
