"""Provider-agnostic LLM interface + shared base class.

``LLMProvider`` is the structural (Protocol) contract every adapter satisfies. ``BaseLLMProvider``
is the concrete base that holds ALL shared logic — model-slot resolution, repr, error mapping, and
the route/generate/stream templates — so the per-provider adapters declare only their SDK-specific
config (a handful of class variables) plus the thin ``_build_client``/``_call``/``_stream_call``
hooks.

Per-node model tiering (Phase 6): ``route()`` uses the cheap ``route_model`` and
``generate()``/``stream()`` use the strong ``synth_model``; one client, two model ids.

No-key-leak invariant: the ``api_key`` is consumed only to build the client (``_build_client``) and
is NEVER stored on ``self`` (``__repr__`` therefore cannot leak it).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Literal, Protocol, runtime_checkable

from exceptions import LLMAuthError, LLMRateLimitError, LLMResponseError, LLMUnavailableError
from llm._prompts import (
    ROUTING_SYSTEM,
    generation_system_user,
    normalize_decision,
    routing_user,
)

Route = Literal["RAG", "WEB", "DIRECT"]


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic LLM interface. Exactly one instance per request."""

    async def route(self, query: str, *, has_documents: bool, web_allowed: bool) -> Route:
        """Classify a query into RAG / WEB / DIRECT."""
        ...

    async def generate(self, query: str, context: str, decision: Route) -> str:
        """Produce the final answer for the decided route."""
        ...

    def stream(self, query: str, context: str, decision: Route) -> AsyncIterator[str]:
        """Yield answer text deltas (consumed by SSE in Phase 6)."""
        ...


class BaseLLMProvider:
    """Concrete base holding the logic shared by every adapter.

    Subclasses declare:
      * ``_DEFAULT_MODEL`` — the adapter's default model id.
      * Error-mapping class vars (``_AUTH_EXCS`` / ``_RATELIMIT_EXCS`` / ``_STATUS_EXC`` /
        ``_UNAVAILABLE_STATUSES``) consumed by the shared ``_map_error`` ladder.
      * ``_ROUTE_MAX_TOKENS`` / ``_GENERATE_MAX_TOKENS`` — only Anthropic overrides these.
      * The hooks ``_build_client``, ``_call``, ``_stream_call``.
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

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        route_model: str | None = None,
        synth_model: str | None = None,
    ) -> None:
        # api_key is used only to construct the client; it is never stored on self.
        self._route_model = route_model or model or self._DEFAULT_MODEL
        self._synth_model = synth_model or model or self._DEFAULT_MODEL
        self._build_client(api_key)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"route_model={self._route_model!r}, synth_model={self._synth_model!r})"
        )

    # ── hooks (overridden by subclasses) ──────────────────────────────────────

    def _build_client(self, api_key: str) -> None:
        """Construct and store the SDK client on ``self._client`` (key never stored)."""
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

    # ── public templates ──────────────────────────────────────────────────────

    async def route(self, query: str, *, has_documents: bool, web_allowed: bool) -> Route:
        text = await self._call(
            self._route_model,
            ROUTING_SYSTEM,
            routing_user(query, has_documents, web_allowed),
            max_tokens=self._ROUTE_MAX_TOKENS,
        )
        return normalize_decision(text)

    async def generate(self, query: str, context: str, decision: Route) -> str:
        sys_msg, usr_msg = generation_system_user(decision, query, context)
        return await self._call(
            self._synth_model, sys_msg, usr_msg, max_tokens=self._GENERATE_MAX_TOKENS
        )

    async def stream(self, query: str, context: str, decision: Route) -> AsyncIterator[str]:  # type: ignore[override]
        sys_msg, usr_msg = generation_system_user(decision, query, context)
        async for chunk in self._stream_call(
            self._synth_model, sys_msg, usr_msg, max_tokens=self._GENERATE_MAX_TOKENS
        ):
            yield chunk
