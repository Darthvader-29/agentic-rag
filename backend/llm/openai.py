"""OpenAIProvider: async OpenAI client, one instance per request.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``.

Prompt caching: OpenAI does **automatic** prefix caching (no API flag) for stable leading
content. We keep the stable instruction (the routing rubric / the role+format contract) in the
``system`` message — always the prefix — and the variable query+context in the trailing ``user``
message. Never inject per-request data into the system message or the cache prefix breaks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai import APIStatusError as OpenAIStatusError
from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError, RateLimitError

from llm.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "gpt-4o-mini"

    _AUTH_EXCS = (AuthenticationError, PermissionDeniedError)
    _RATELIMIT_EXCS = (RateLimitError,)
    _STATUS_EXC = OpenAIStatusError
    _UNAVAILABLE_STATUSES = frozenset({500, 502, 503})

    def _build_client(self, api_key: str) -> None:
        # R12: explicit per-request timeout so a hung upstream can't pin the request forever.
        # The SDK's own retry loop is disabled (max_retries=0) — the base class owns retry/backoff
        # via tenacity so exhaustion maps to the neutral taxonomy (not a raw SDK error).
        self._client = AsyncOpenAI(api_key=api_key, timeout=self._timeout_seconds, max_retries=0)

    async def _call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> str:
        with self._guard():
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},  # stable prefix → auto-cached
                    {"role": "user", "content": user},  # variable suffix
                ],
            )
            return (resp.choices[0].message.content or "").strip()

    async def _stream_call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        with self._guard():
            stream_resp = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},  # stable prefix → auto-cached
                    {"role": "user", "content": user},  # variable suffix
                ],
                stream=True,
            )
            async for event in stream_resp:
                delta = event.choices[0].delta.content
                if delta:
                    yield delta
