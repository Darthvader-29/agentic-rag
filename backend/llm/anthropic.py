"""AnthropicProvider: async Anthropic client, one instance per request.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``.

Prompt caching (Decision 9): the stable instruction — the routing rubric for ``route()`` and the
role+format contract for ``generate()``/``stream()`` — goes in a ``system`` block carrying
``cache_control={"type": "ephemeral"}`` (~90% off cached input tokens, up to 2x faster). The
variable query+context stays in the ``user`` message and is therefore NEVER cached. The system
block contains no per-request data, so it is byte-identical across requests and reliably hits.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import APIStatusError as AnthropicStatusError
from anthropic import AsyncAnthropic, AuthenticationError, PermissionDeniedError, RateLimitError
from anthropic.types import TextBlock, TextBlockParam

from exceptions import LLMResponseError
from llm.base import BaseLLMProvider

_ANTHROPIC_OVERLOADED = 529  # Anthropic-specific "overloaded" status


def _cached_system(text: str) -> list[TextBlockParam]:
    """Wrap a stable instruction as a cache-eligible Anthropic system block."""
    return [
        TextBlockParam(type="text", text=text, cache_control={"type": "ephemeral"}),
    ]


class AnthropicProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "claude-3-5-haiku-latest"

    _AUTH_EXCS = (AuthenticationError, PermissionDeniedError)
    _RATELIMIT_EXCS = (RateLimitError,)
    _STATUS_EXC = AnthropicStatusError
    _UNAVAILABLE_STATUSES = frozenset({500, 503, _ANTHROPIC_OVERLOADED})

    _ROUTE_MAX_TOKENS = 8
    _GENERATE_MAX_TOKENS = 1024

    def _build_client(self, api_key: str) -> None:
        # R12: explicit per-request timeout so a hung upstream can't pin the request forever. The
        # SDK's own retry loop is disabled (max_retries=0) — the base class owns retry/backoff via
        # tenacity so exhaustion maps to the neutral taxonomy (not a raw SDK error).
        self._client = AsyncAnthropic(api_key=api_key, timeout=self._timeout_seconds, max_retries=0)

    async def _call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> str:
        with self._guard():
            msg = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens or self._GENERATE_MAX_TOKENS,
                system=_cached_system(system),  # stable prefix → ephemeral cache
                messages=[{"role": "user", "content": user}],  # variable suffix
            )
            text_block = next((b for b in msg.content if isinstance(b, TextBlock)), None)
            if text_block is None:
                raise LLMResponseError("Anthropic response contained no text block")
            return text_block.text.strip()

    async def _stream_call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        with self._guard():
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens or self._GENERATE_MAX_TOKENS,
                system=_cached_system(system),  # stable prefix → ephemeral cache
                messages=[{"role": "user", "content": user}],  # variable suffix
            ) as stream_mgr:
                async for text in stream_mgr.text_stream:
                    yield text
