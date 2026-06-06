"""Phase 7: worker-side tracing.

A Celery worker is a separate process that never imports ``app.py``, so it bootstraps its own
``TracerProvider`` + ``CeleryInstrumentor`` on ``worker_process_init``. With ``CeleryInstrumentor``
instrumented on BOTH the API process (where ``ingest_document.delay(...)`` is enqueued) and here,
the active trace context rides the task message headers automatically, so ``ingest.document`` and
its children nest under the originating request's trace rather than orphaning.
"""

from __future__ import annotations

from typing import Any

from celery.signals import worker_process_init

from config import Settings
from config import settings as _settings


def init_worker_tracing(settings: Settings | None = None) -> None:
    """Install tracing + Celery instrumentation in a worker process (gated on ``OTEL_ENABLED``)."""
    s = settings or _settings
    from observability.tracing import init_tracing

    init_tracing(s)
    if s.OTEL_ENABLED:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()


@worker_process_init.connect
def _on_worker_process_init(**_: Any) -> None:  # pragma: no cover - only fires in a real worker
    init_worker_tracing()
