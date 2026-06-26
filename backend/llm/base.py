"""Provider-agnostic LLM interface + shared base class.

``LLMProvider`` is the structural (Protocol) contract every adapter satisfies. ``BaseLLMProvider``
is the concrete base that holds ALL shared logic — model-slot resolution, repr, error mapping,
timeout/retry policy, and the route/generate/stream templates — so the per-provider adapters
declare only their SDK-specific config (a handful of class variables) plus the thin
``_build_client``/``_call``/``_stream_call`` hooks.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``; one client, two model ids.

No-key-leak invariant: the ``api_key`` is consumed only to build the client (``_build_client``) and
is NEVER stored on ``self`` (``__repr__`` therefore cannot leak it).

Resilience (R12): every adapter builds its SDK client with an explicit request ``timeout`` (so a
hung upstream can't pin the request — and the worker thread / SSE generator it runs on — forever)
and the route/generate/stream templates retry the SDK call with bounded, jittered exponential
backoff on the *transient* neutral errors (``LLMRateLimitError`` / ``LLMUnavailableError`` →
429/503/529). Auth and response errors are NOT transient, so they raise on the first attempt. The
timeout/attempt budgets live in ``config.Settings`` (``LLM_TIMEOUT_SECONDS`` /
``LLM_MAX_RETRY_ATTEMPTS`` / ``LLM_RETRY_BACKOFF_SECONDS``); they are read once at construction so a
provider instance carries an immutable policy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Literal, Protocol, runtime_checkable

import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config import settings
from exceptions import (
    LLMAuthError,
    LLMRateLimitError,
    LLMResponseError,
    LLMUnavailableError,
)
from llm._prompts import (
    REWRITE_SYSTEM,
    ROUTING_SYSTEM,
    History,
    generation_system_user,
    normalize_decision,
    rewrite_user,
    routing_user,
)

logger = structlog.get_logger(__name__)

Route = Literal["RAG", "WEB", "DIRECT"]

# Transient neutral errors worth retrying (the upstream is up but throttling/overloaded). Auth and
# response errors are deterministic — retrying them only wastes the user's time, so they are not
# listed and propagate on the first attempt.
_TRANSIENT_LLM_ERRORS: tuple[type[Exception], ...] = (LLMRateLimitError, LLMUnavailableError)


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic LLM interface. Exactly one instance per request."""

    async def route(
        self, query: str, *, has_documents: bool, web_allowed: bool, history: History | None = None
    ) -> Route:
        """Classify a query into RAG / WEB / DIRECT."""
        ...

    async def rewrite_query(self, query: str, *, history: History | None = None) -> str:
        """Rewrite a follow-up query into a standalone retrieval string using recent history."""
        ...

    async def generate(
        self, query: str, context: str, decision: Route, *, history: History | None = None
    ) -> str:
        """Produce the final answer for the decided route."""
        ...

    def stream(
        self, query: str, context: str, decision: Route, *, history: History | None = None
    ) -> AsyncIterator[str]:
        """Yield answer text deltas (consumed by SSE in Phase 6)."""
        ...


class BaseLLMProvider:
    """Concrete base holding the logic shared by every adapter.

    Subclasses declare:
      * ``_DEFAULT_MODEL`` — the adapter's default model id.
      * Error-mapping class vars (``_AUTH_EXCS`` / ``_RATELIMIT_EXCS`` / ``_STATUS_EXC`` /
        ``_UNAVAILABLE_STATUSES``) consumed by the shared ``_map_error`` ladder.
      * ``_ROUTE_MAX_TOKENS`` / ``_GENERATE_MAX_TOKENS`` — only Anthropic overrides these.
      * The hooks ``_build_client``, ``_call``, ``_stream_call``. ``_build_client`` reads
        ``self._timeout_seconds`` to wire its SDK client's request timeout.
    """

    _DEFAULT_MODEL: str = ""

    # Error-mapping config (see _map_error). Defaults map nothing → everything is LLMResponseError.
    _AUTH_EXCS: tuple[type[Exception], ...] = ()
    _RATELIMIT_EXCS: tuple[type[Exception], ...] = ()
    _STATUS_EXC: type[Exception] | None = None
    _UNAVAILABLE_STATUSES: frozenset[int] = frozenset()
    # Exception types that already carry a ``status_code`` but aren't ``_STATUS_EXC`` (e.g. Gemini's
    # google.api_core exceptions, which are matched directly rather than by status code).
    _UNAVAILABLE_EXCS: tuple[type[Exception], ...] = ()

    # Generation token budgets (Anthropic requires an explicit max_tokens; OpenAI/Gemini ignore).
    _ROUTE_MAX_TOKENS: int | None = None
    _GENERATE_MAX_TOKENS: int | None = None
    # Query rewriting runs on the cheap route model, but a rewritten QUERY needs far more room than
    # the tiny routing budget (one word) — Anthropic overrides this; OpenAI/Gemini ignore max_tokens.
    _REWRITE_MAX_TOKENS: int | None = None

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        route_model: str | None = None,
        synth_model: str | None = None,
        timeout_seconds: float | None = None,
        max_retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        # api_key is used only to construct the client; it is never stored on self.
        self._route_model = route_model or model or self._DEFAULT_MODEL
        self._synth_model = synth_model or model or self._DEFAULT_MODEL
        # Resilience policy (R12): resolved once, immutable for the instance's life. Defaults come
        # from Settings; explicit kwargs (used by tests) win so retries don't sleep for real.
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.LLM_TIMEOUT_SECONDS
        )
        self._max_retry_attempts = (
            max_retry_attempts
            if max_retry_attempts is not None
            else settings.LLM_MAX_RETRY_ATTEMPTS
        )
        self._retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.LLM_RETRY_BACKOFF_SECONDS
        )
        self._build_client(api_key)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"route_model={self._route_model!r}, synth_model={self._synth_model!r})"
        )

    # ── hooks (overridden by subclasses) ──────────────────────────────────────

    def _build_client(self, api_key: str) -> None:
        """Construct and store the SDK client on ``self._client`` (key never stored).

        Implementations wire the SDK's request timeout from ``self._timeout_seconds``.
        """
        raise NotImplementedError

    async def _call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> str:
        """Single non-streaming completion → trimmed text. Wrap calls in ``self._guard()``."""
        raise NotImplementedError

    def _stream_call(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        """Stream text deltas. Wrap calls in ``self._guard()``."""
        raise NotImplementedError

    # ── shared error handling ─────────────────────────────────────────────────

    def _map_error(self, exc: Exception) -> Exception:
        """Map an SDK exception to the neutral LLM error taxonomy via the class-var config."""
        if self._AUTH_EXCS and isinstance(exc, self._AUTH_EXCS):
            return LLMAuthError()
        if self._RATELIMIT_EXCS and isinstance(exc, self._RATELIMIT_EXCS):
            return LLMRateLimitError()
        if self._UNAVAILABLE_EXCS and isinstance(exc, self._UNAVAILABLE_EXCS):
            return LLMUnavailableError()
        if (
            self._STATUS_EXC is not None
            and isinstance(exc, self._STATUS_EXC)
            and getattr(exc, "status_code", None) in self._UNAVAILABLE_STATUSES
        ):
            return LLMUnavailableError()
        return LLMResponseError()

    @contextmanager
    def _guard(self) -> Iterator[None]:
        """Re-raise already-neutral errors as-is; map any other SDK exception once."""
        try:
            yield
        except (LLMAuthError, LLMRateLimitError, LLMUnavailableError, LLMResponseError):
            raise
        except Exception as e:  # noqa: BLE001
            raise self._map_error(e) from e

    # ── shared retry policy (R12) ─────────────────────────────────────────────

    def _retrying(self) -> AsyncRetrying:
        """Build the bounded, jittered retry controller for one SDK call.

        Retries only the *transient* neutral errors (rate-limit / unavailable); reraises the last
        one after the attempt budget is spent (so exhaustion surfaces the same neutral taxonomy the
        caller already handles, never a tenacity ``RetryError``). ``max_retry_attempts`` is the
        TOTAL number of attempts (1 ⇒ no retry), so a 429/529 burst is bounded.
        """
        return AsyncRetrying(
            stop=stop_after_attempt(max(1, self._max_retry_attempts)),
            wait=wait_exponential_jitter(initial=self._retry_backoff_seconds, max=8.0),
            retry=retry_if_exception_type(_TRANSIENT_LLM_ERRORS),
            reraise=True,
        )

    async def _call_with_retry(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> str:
        """``_call`` wrapped in the transient-error retry policy."""
        async for attempt in self._retrying():
            with attempt:
                return await self._call(model, system, user, max_tokens=max_tokens)
        raise AssertionError("unreachable: AsyncRetrying(reraise=True) re-raises on exhaustion")

    async def _open_stream(
        self, model: str, system: str, user: str, *, max_tokens: int | None
    ) -> tuple[AsyncIterator[str], str | None]:
        """Open a stream and pull its first delta, retrying transient pre-first-chunk failures.

        Returns ``(generator, first_chunk)`` where ``first_chunk is None`` means the stream closed
        empty. ONLY this establishment phase is retried: nothing has been delivered to the consumer
        yet, so re-opening is safe. The returned generator is then drained incrementally by the
        caller WITHOUT retry, so a mid-stream failure surfaces (mapped) instead of replaying.
        """
        async for attempt in self._retrying():
            with attempt:
                agen = self._stream_call(model, system, user, max_tokens=max_tokens)
                try:
                    first = await agen.__anext__()
                except StopAsyncIteration:
                    return agen, None  # empty stream — nothing to retry or drain
                except _TRANSIENT_LLM_ERRORS:
                    await agen.aclose()  # failed before first delta → safe for tenacity to retry
                    raise
                return agen, first
        raise AssertionError("unreachable: AsyncRetrying(reraise=True) re-raises on exhaustion")

    async def _stream_call_with_retry(
        self, model: str, system: str, user: str, *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        """Stream deltas, retrying ONLY while no chunk has been emitted yet.

        A transient failure before the first delta (connection/handshake/early 429) is safely
        retried — the consumer has seen nothing. Once the first delta is yielded the stream is
        committed; a later failure propagates (mapped to the neutral taxonomy) rather than replaying
        already-delivered text (which would corrupt the SSE output). Chunks are yielded incrementally
        as they arrive, so streaming stays real-time.
        """
        agen, first = await self._open_stream(model, system, user, max_tokens=max_tokens)
        if first is None:
            return
        yield first
        async for chunk in agen:  # committed: drained without retry; mid-stream errors propagate
            yield chunk

    # ── public templates ──────────────────────────────────────────────────────

    async def route(
        self, query: str, *, has_documents: bool, web_allowed: bool, history: History | None = None
    ) -> Route:
        text = await self._call_with_retry(
            self._route_model,
            ROUTING_SYSTEM,
            routing_user(query, has_documents, web_allowed, history),
            max_tokens=self._ROUTE_MAX_TOKENS,
        )
        return normalize_decision(text)

    async def rewrite_query(self, query: str, *, history: History | None = None) -> str:
        """Rewrite a follow-up into a standalone retrieval query using recent conversation history.

        Uses the CHEAP ``route_model`` (this is a light reformulation, not synthesis). With no
        history there is nothing to resolve, so the SDK call is skipped and the query returned
        unchanged — keeping single-turn requests free and byte-identical to the pre-rewrite path. A
        model that returns blank falls back to the original query, so retrieval always receives a
        usable, non-empty string.
        """
        if not history:
            return query
        text = await self._call_with_retry(
            self._route_model,
            REWRITE_SYSTEM,
            rewrite_user(query, history),
            max_tokens=self._REWRITE_MAX_TOKENS,
        )
        return text.strip() or query

    async def generate(
        self, query: str, context: str, decision: Route, *, history: History | None = None
    ) -> str:
        sys_msg, usr_msg = generation_system_user(decision, query, context, history)
        return await self._call_with_retry(
            self._synth_model, sys_msg, usr_msg, max_tokens=self._GENERATE_MAX_TOKENS
        )

    async def stream(  # type: ignore[override]
        self, query: str, context: str, decision: Route, *, history: History | None = None
    ) -> AsyncIterator[str]:
        sys_msg, usr_msg = generation_system_user(decision, query, context, history)
        async for chunk in self._stream_call_with_retry(
            self._synth_model, sys_msg, usr_msg, max_tokens=self._GENERATE_MAX_TOKENS
        ):
            yield chunk
