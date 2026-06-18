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

Concurrency (R13): the streaming SDK call returns a **synchronous** iterator. Iterating it directly
on the event loop runs a blocking ``next()`` per chunk, which — because Gemini is the default and
free-tier provider — serializes every concurrent request behind one slow stream. ``_stream_call``
therefore pumps the sync iterator through ``anyio.to_thread.run_sync`` one chunk at a time (mirroring
how ``_call`` already off-loads the blocking unary call), so the loop stays free between chunks.

Timeouts (R12): the client carries an explicit per-request timeout via ``HttpOptions`` (Gemini's
timeout is specified in **milliseconds**), so a hung upstream can't pin the worker thread forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anyio
from google import genai
from google.api_core import exceptions as gexc
from google.genai import types as genai_types

from llm.base import BaseLLMProvider

# Sentinel marking the end of the sync stream when pumped across the thread boundary. A bare
# ``StopIteration`` raised inside a coroutine/thread does not propagate cleanly, so the worker
# returns this object instead and the async side translates it into loop termination.
_STREAM_DONE = object()


class GeminiProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "gemini-2.5-flash"

    _AUTH_EXCS = (gexc.PermissionDenied, gexc.Unauthenticated)
    _RATELIMIT_EXCS = (gexc.ResourceExhausted,)
    _UNAVAILABLE_EXCS = (gexc.ServiceUnavailable, gexc.DeadlineExceeded)

    def _build_client(self, api_key: str) -> None:
        # R12: HttpOptions.timeout is in MILLISECONDS (the SDK divides by 1000 for httpx).
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(self._timeout_seconds * 1000)),
        )

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
            # Stream CREATION is blocking → off-load it (as before).
            chunks = await anyio.to_thread.run_sync(
                lambda: self._client.models.generate_content_stream(model=model, contents=prompt)
            )
            iterator = iter(chunks)

            def _next() -> Any:
                # Pull ONE chunk off the sync iterator on a worker thread. StopIteration can't cross
                # the thread boundary as itself, so end-of-stream is signalled with a sentinel.
                try:
                    return next(iterator)
                except StopIteration:
                    return _STREAM_DONE

            # R13: pump the sync iterator one chunk at a time through a worker thread so each
            # blocking next() yields the event loop back to other concurrent requests.
            while True:
                chunk = await anyio.to_thread.run_sync(_next)
                if chunk is _STREAM_DONE:
                    break
                if chunk.text:
                    yield chunk.text
