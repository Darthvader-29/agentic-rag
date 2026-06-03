"""Celery ingestion task (Phase 5): a durable wrapper around process_file_pipeline.

A worker has no FastAPI request scope, so nothing is injected — the task builds its own
Settings, clients, and DB session factory. Only JSON-serializable args cross the broker
(document_id, s3_key, filename, session_id). The pipeline owns the PROCESSING→READY/FAILED
status transitions (status lives in Postgres so any API instance can poll it); the task
re-asserts FAILED defensively in case the failure happened before the pipeline's own
handler, then re-raises for observability.

Retries: transient I/O is already retried at the client level (tenacity on S3/embedder/
Pinecone), and a deterministic parse failure should not loop — so no Celery autoretry.
task_acks_late (see celery_app) redelivers on worker crash; re-runs are idempotent because
chunk ids are deterministic ({session}_{filename}_{i}), so an upsert overwrites.
"""

import asyncio

import structlog

from config import Settings
from database import repository as repo
from database.models import DocumentStatus
from database.session import build_engine, build_sessionmaker
from observability.tracing import get_tracer
from worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _run_pipeline(s3_key: str, filename: str, session_id: str, settings: Settings) -> None:
    """Build worker-owned clients and run the existing ingestion pipeline."""
    from components.preprocessing import process_file_pipeline
    from database.db_manager import PineconeClient
    from integrations.huggingface.client import HuggingFaceClient
    from integrations.s3.client import S3Client

    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)
    s3 = S3Client.from_settings(settings)
    embedder = HuggingFaceClient.from_settings(settings)
    pinecone = PineconeClient.from_settings(settings)
    try:
        await process_file_pipeline(
            s3_key, filename, session_id, s3, embedder, pinecone, session_factory
        )
    finally:
        await engine.dispose()


async def _mark_failed(s3_key: str, settings: Settings) -> None:
    """Best-effort FAILED status write using a short-lived session."""
    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as db:
            await repo.set_document_status(db, s3_key=s3_key, status=DocumentStatus.FAILED)
            await db.commit()
    finally:
        await engine.dispose()


@celery_app.task(name="worker.tasks.ingest_document")
def ingest_document(*, document_id: str, s3_key: str, filename: str, session_id: str) -> None:
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads env, not kwargs
    logger.info("ingest_task_start", document_id=document_id, s3_key=s3_key)
    # Phase 7: span nests under the propagated request trace (CeleryInstrumentor carries context).
    with get_tracer().start_as_current_span("ingest.document") as span:
        span.set_attribute("doc.id", document_id)
        span.set_attribute("session.id", session_id)
        try:
            asyncio.run(_run_pipeline(s3_key, filename, session_id, settings))
            logger.info("ingest_task_done", document_id=document_id)
        except Exception:
            logger.error("ingest_task_failed", document_id=document_id, exc_info=True)
            try:
                asyncio.run(_mark_failed(s3_key, settings))
            except Exception:
                logger.error(
                    "ingest_task_status_update_failed", document_id=document_id, exc_info=True
                )
            raise
