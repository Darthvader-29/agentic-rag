"""GeminiProvider: instance-scoped Gemini client (no process-global configure).

Uses google-genai (google.genai.Client) — instance-scoped, not the deprecated
google.generativeai.configure() global that races under concurrent requests.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``; one client, two model ids.

Prompt caching: Gemini 2.5 does **implicit** caching keyed on a stable leading prefix. ``_call``
concatenates the (stable system, variable user) pair the base passes into ``f"{system}\n\n{user}"``,
so the byte-identical rubric / format contract leads and the variable query+context trails — no API
flag is needed; the structure is the cache key. (Explicit ``CachedContent`` is skipped: it adds
storage TTL management for prefixes well under the implicit-cache threshold.)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anyio
from google import genai
from google.api_core import exceptions as gexc

from llm.base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "gemini-2.5-flash"

    _AUTH_EXCS = (gexc.PermissionDenied, gexc.Unauthenticated)
    _RATELIMIT_EXCS = (gexc.ResourceExhausted,)
    _UNAVAILABLE_EXCS = (gexc.ServiceUnavailable, gexc.DeadlineExceeded)

    def _build_client(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def _call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> str:
        prompt = f"{system}\n\n{user}"
        with self._guard():
            resp = await anyio.to_thread.run_sync(
                lambda: self._client.models.generate_content(model=model, contents=prompt)
            )
            return resp.text.strip()

    async def _stream_call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        prompt = f"{system}\n\n{user}"
        with self._guard():
            chunks = await anyio.to_thread.run_sync(
                lambda: self._client.models.generate_content_stream(model=model, contents=prompt)
            )
            for chunk in chunks:  # SDK stream is a sync iterable
                if chunk.text:
                    yield chunk.text
