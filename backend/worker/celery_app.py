"""Celery application backing durable ingestion (Phase 5).

Broker and result backend both point at Redis (``settings.celery_broker_url``, which
defaults to ``REDIS_URL``). Tasks live in ``worker.tasks`` and are registered via
``include``. ``task_acks_late`` + ``worker_prefetch_multiplier=1`` mean a task
interrupted by a worker restart is redelivered — ingestion is idempotent (deterministic
chunk ids overwrite rather than duplicate), so redelivery is safe.

In tests, ``conftest`` forces eager mode (``task_always_eager``) so ``delay()`` runs
inline with no broker or worker process.
"""

from celery import Celery

from config import settings

# Phase 7: importing registers the worker_process_init tracing signal so `celery -A worker` wires
# OpenTelemetry without importing the FastAPI app (a no-op until a real worker starts, and only
# active when OTEL_ENABLED).
from worker import tracing as _worker_tracing  # noqa: E402,F401

celery_app = Celery(
    "rag",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
