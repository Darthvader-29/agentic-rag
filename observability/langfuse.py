"""Phase 7: Langfuse agent-trace bootstrap (gated, OTEL-native).

Langfuse v4 is built on OpenTelemetry, so the spans already emitted on the agent path
(``agent.*`` / ``memory.*``) are ingested once the client is initialised — no separate
instrumentation. Gated behind ``LANGFUSE_ENABLED`` + both keys present; keys are ``SecretStr`` and
never logged. A no-op (returns ``False``) when disabled or misconfigured — chosen over LangSmith for
this portfolio/demo because it is MIT-licensed, self-hostable and framework-agnostic (docs/08 B3).
"""

from __future__ import annotations

import os

import structlog

from config import Settings

logger = structlog.get_logger(__name__)


def init_langfuse(settings: Settings) -> bool:
    """Initialise the Langfuse client if enabled + configured. Returns whether it was activated."""
    if not settings.LANGFUSE_ENABLED:
        return False
    pk = settings.LANGFUSE_PUBLIC_KEY.get_secret_value() if settings.LANGFUSE_PUBLIC_KEY else ""
    sk = settings.LANGFUSE_SECRET_KEY.get_secret_value() if settings.LANGFUSE_SECRET_KEY else ""
    if not (pk and sk):
        logger.warning("langfuse_enabled_but_keys_missing")
        return False

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", pk)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", sk)
    os.environ.setdefault("LANGFUSE_HOST", settings.LANGFUSE_HOST)
    try:
        from langfuse import get_client

        get_client()  # constructs the singleton; v4 exports OTEL spans to Langfuse
        logger.info("langfuse_initialized", host=settings.LANGFUSE_HOST)
        return True
    except Exception:
        logger.error("langfuse_init_failed", exc_info=True)
        return False
