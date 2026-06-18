"""LLM resilience tests — timeouts + bounded retry mapped to the neutral taxonomy (R12).

* every adapter builds its SDK client with an explicit request timeout and the SDK's own retry loop
  disabled;
* the base templates retry *transient* failures (429/503/529 → neutral ``LLMRateLimitError`` /
  ``LLMUnavailableError``) with a bounded attempt budget and map exhaustion to the neutral taxonomy;
* *non-transient* failures (auth) are NOT retried.

Mock SDKs come from ``conftest.py`` (``provider_case`` maps-without-retry; ``retrying_provider_case``
retries with zero backoff so nothing sleeps).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from exceptions import (
    LLMAuthError,
    LLMRateLimitError,
    LLMResponseError,
    LLMUnavailableError,
)

# ── timeout wired into the client + SDK retry disabled ─────────────────────────


def test_openai_client_built_with_timeout_and_no_sdk_retry():
    mock_client = AsyncMock()
    with patch("llm.openai.AsyncOpenAI", return_value=mock_client) as ctor:
        from llm.openai import OpenAIProvider

        OpenAIProvider(api_key="fake-key", timeout_seconds=12.5)
    kwargs = ctor.call_args.kwargs
    assert kwargs["timeout"] == 12.5  # explicit per-request deadline (R12)
    assert kwargs["max_retries"] == 0  # SDK retry disabled — base owns retry/backoff


def test_anthropic_client_built_with_timeout_and_no_sdk_retry():
    mock_client = AsyncMock()
    with patch("llm.anthropic.AsyncAnthropic", return_value=mock_client) as ctor:
        from llm.anthropic import AnthropicProvider

        AnthropicProvider(api_key="fake-key", timeout_seconds=33.0)
    kwargs = ctor.call_args.kwargs
    assert kwargs["timeout"] == 33.0
    assert kwargs["max_retries"] == 0


def test_gemini_client_built_with_timeout_in_milliseconds():
    mock_client = MagicMock()
    with patch("llm.gemini.genai.Client", return_value=mock_client) as ctor:
        from llm.gemini import GeminiProvider

        GeminiProvider(api_key="fake-key", timeout_seconds=20.0)
    http_options = ctor.call_args.kwargs["http_options"]
    # Gemini's HttpOptions.timeout is in MILLISECONDS, so 20s → 20000ms (R12).
    assert http_options.timeout == 20000


def test_default_timeout_and_retry_come_from_settings():
    """Construction without explicit policy reads the additive Settings defaults (R12)."""
    mock_client = AsyncMock()
    with patch("llm.openai.AsyncOpenAI", return_value=mock_client) as ctor:
        from llm.openai import OpenAIProvider

        provider = OpenAIProvider(api_key="fake-key")
    assert ctor.call_args.kwargs["timeout"] == settings.LLM_TIMEOUT_SECONDS
    assert provider._max_retry_attempts == settings.LLM_MAX_RETRY_ATTEMPTS


# ── bounded retry on transient errors → neutral taxonomy ───────────────────────


@pytest.mark.parametrize("kind", ["rate", "unavailable"])
async def test_transient_retries_then_succeeds(retrying_provider_case, kind):
    """A transient failure that clears within the attempt budget recovers (no error raised)."""
    provider, sdk = retrying_provider_case
    sdk.raise_next_times(kind, 1)  # fail once, succeed on the retry
    out = await provider.generate("Q?", "ctx", "DIRECT")
    assert isinstance(out, str) and out
    assert sdk.calls == 2  # one failed attempt + one successful retry


@pytest.mark.parametrize(
    "kind, neutral",
    [("rate", LLMRateLimitError), ("unavailable", LLMUnavailableError)],
)
async def test_transient_exhaustion_maps_to_neutral_error(retrying_provider_case, kind, neutral):
    """When every attempt fails, exhaustion surfaces the neutral error — never a RetryError."""
    provider, sdk = retrying_provider_case
    sdk.raise_next_times(kind, 99)  # never clears within the attempt budget
    with pytest.raises(neutral):
        await provider.generate("Q?", "ctx", "DIRECT")
    assert sdk.calls == 3  # bounded to max_retry_attempts (the retrying fixture sets 3)


async def test_auth_error_is_not_retried(retrying_provider_case):
    """Auth failures are deterministic → mapped immediately, with NO retry (R12)."""
    provider, sdk = retrying_provider_case
    sdk.raise_next_times("auth", 99)
    with pytest.raises(LLMAuthError):
        await provider.route("hi", has_documents=False, web_allowed=True)
    assert sdk.calls == 1  # single attempt — auth is not in the transient set


async def test_stream_retries_before_first_chunk_then_succeeds(retrying_provider_case):
    """A transient failure before the first delta is safely retried (consumer saw nothing)."""
    provider, sdk = retrying_provider_case
    sdk.raise_next_times("unavailable", 1)
    chunks = [c async for c in provider.stream("Q?", "ctx", "DIRECT")]
    assert chunks and all(isinstance(c, str) for c in chunks)


def test_only_transient_errors_are_retryable():
    """The retry set is exactly the throttle/unavailable pair — auth/response are NOT retryable."""
    from llm.base import _TRANSIENT_LLM_ERRORS

    assert set(_TRANSIENT_LLM_ERRORS) == {LLMRateLimitError, LLMUnavailableError}
    assert LLMResponseError not in _TRANSIENT_LLM_ERRORS
    assert LLMAuthError not in _TRANSIENT_LLM_ERRORS
