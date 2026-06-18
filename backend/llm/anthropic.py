"""AnthropicProvider: async Anthropic client, one instance per request.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``.

Prompt caching (Decision 9): the stable instruction — the routing rubric for ``route()`` and the
role+format contract for ``generate()``/``stream()`` — goes in a ``system`` block carrying
``cache_control={"type": "ephemeral"}`` (~90% off cached input tokens, up to 2x faster). The
variable query+context stays in the ``user`` message and is therefore NEVER cached. The system
block contains no per-request data, so it is byte-identical across requests and reliably hits.

Output budget (R14): synthesis allows up to ``_GENERATE_MAX_TOKENS`` output tokens. The previous
1024 cap silently truncated long answers — a rich markdown answer plus a trailing ```json component
block routinely exceeds it, and a half-emitted block fails ``parse_components`` and is dropped. We
raise the cap and inspect ``stop_reason``: a ``max_tokens`` stop is logged as a warning (so a
truncation is observable, never silent) rather than passed off as a complete answer.

Model ids (R14): the defaults are pinned to current, non-deprecated Claude 4.x ids (the deprecated
``claude-3-5-*-latest`` aliases risk silent drift / removal). The route classifier uses Haiku 4.5
and synthesis uses Sonnet 4.6 (see ``config.TIER_*_ANTHROPIC``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import AsyncAnthropic, AuthenticationError, PermissionDeniedError, RateLimitError
from anthropic.types import TextBlock, TextBlockParam

from exceptions import LLMResponseError
from llm.base import BaseLLMProvider

logger = structlog.get_logger(__name__)

_ANTHROPIC_OVERLOADED = 529  # Anthropic-specific "overloaded" status


def _cached_system(text: str) -> list[TextBlockParam]:
    """Wrap a stable instruction as a cache-eligible Anthropic system block."""
    return [
        TextBlockParam(type="text", text=text, cache_control={"type": "ephemeral"}),
    ]


class AnthropicProvider(BaseLLMProvider):
    # Pinned to a current, non-deprecated Claude 4.x id (R14): the default/route classifier is
    # Haiku 4.5; synthesis upgrades to Sonnet 4.6 via the per-provider synth tier (config).
    _DEFAULT_MODEL = "claude-haiku-4-5-20251001"

    _AUTH_EXCS = (AuthenticationError, PermissionDeniedError)
    _RATELIMIT_EXCS = (RateLimitError,)
    _STATUS_EXC = AnthropicStatusError
    _UNAVAILABLE_STATUSES = frozenset({500, 503, _ANTHROPIC_OVERLOADED})

    _ROUTE_MAX_TOKENS = 8
    # R14: raised from 1024 → 4096 so rich-markdown answers + a trailing ```json component block
    # aren't truncated mid-block (which fails parse_components and silently drops the component).
    _GENERATE_MAX_TOKENS = 4096

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
            if msg.stop_reason == "max_tokens":
                # R14: don't silently truncate — surface that the answer hit the output cap.
                logger.warning(
                    "anthropic_response_truncated",
                    model=model,
                    stop_reason=msg.stop_reason,
                    max_tokens=max_tokens or self._GENERATE_MAX_TOKENS,
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
                # R14: after the stream drains, inspect the assembled message's stop_reason so a
                # truncated long answer is observable rather than passed off as complete.
                final = await stream_mgr.get_final_message()
                if final.stop_reason == "max_tokens":
                    logger.warning(
                        "anthropic_stream_truncated",
                        model=model,
                        stop_reason=final.stop_reason,
                        max_tokens=max_tokens or self._GENERATE_MAX_TOKENS,
                    )
