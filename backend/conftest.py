"""Inject dummy secrets BEFORE any app import.

config.Settings() and several integration modules construct external clients at import time and read
required env vars. Tests are fully mocked/offline, so we fill harmless dummies. No constructor here
performs network I/O (pydantic validates only; genai.configure stores the key; Pinecone v8 is lazy;
boto3.client builds an object without contacting AWS).
"""

import asyncio
import os

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

# Load .env so TEST_DATABASE_URL (and other vars) are available via os.environ.
# This runs before the _DUMMY setdefaults so any real value in .env wins.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)  # don't overwrite values already in the shell environment
except ImportError:
    pass
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_DUMMY = {
    "GOOGLE_API_KEY": "test-google-key",
    "PINECONE_API_KEY": "test-pinecone-key",
    "HUGGINGFACE_TOKEN": "test-hf-token",
    "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "test-bucket",
    "AWS_ACCESS_KEY_ID": "test-akid",
    "AWS_SECRET_ACCESS_KEY": "test-secret",
    "PINECONE_INDEX_NAME": "rag-knowledge-base",
    "LOG_JSON": "false",
    "ENVIRONMENT": "development",
    # Phase 2: dummy satisfies Settings() validation; real DB tests use TEST_DATABASE_URL
    "DATABASE_URL": "postgresql+asyncpg://rag:rag@localhost:5432/rag_test",
    # S3_ENDPOINT_URL intentionally absent — tests exercise the None default
    # Phase 3: auth + encryption dummies (real-shaped but never used in prod)
    "JWT_SECRET": "test-jwt-secret-not-for-production",
    "LLM_KEY_ENCRYPTION_KEY": os.environ.get("LLM_KEY_ENCRYPTION_KEY")
    or Fernet.generate_key().decode(),
    "CORS_ALLOWED_ORIGINS": '["http://localhost:3000"]',
    # Phase 5: lazy from_url → no network at construction. Rate-limit storage points at
    # in-process memory so the offline suite exercises limits without a real Redis.
    "REDIS_URL": "redis://localhost:6379/0",
    "RATE_LIMIT_STORAGE_URI": "memory://",
}
for _k, _v in _DUMMY.items():
    os.environ.setdefault(_k, _v)  # a real shell/.env value still wins


# ── DB fixtures (require TEST_DATABASE_URL in env; skip if absent) ────────────


def _test_db_url() -> tuple[str, dict] | None:
    """Return (asyncpg_url, connect_args) for the test DB, or None to skip."""
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        return None
    from database.session import _to_asyncpg_url

    return _to_asyncpg_url(raw)


@pytest.fixture(scope="session")
def _engine():
    result = _test_db_url()
    if result is None:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB tests")
    url, connect_args = result

    from database.models import Base

    engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine

    async def _teardown() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_teardown())


@pytest_asyncio.fixture
async def db_session(_engine):
    """Per-test AsyncSession bound to a rolled-back connection — leaves no residue."""
    conn = await _engine.connect()
    txn = await conn.begin()
    factory = async_sessionmaker(bind=conn, expire_on_commit=False)
    async with factory() as session:
        yield session
    await txn.rollback()
    await conn.close()


# ── Phase 5: rate-limiter isolation ──────────────────────────────────────────


def _clear_limiter_storage() -> None:
    """Clear the module-level slowapi limiter's in-memory counters, if app is loaded."""
    import sys

    app_mod = sys.modules.get("app")
    limiter = getattr(app_mod, "limiter", None) if app_mod else None
    if limiter is not None:
        try:
            limiter.reset()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The slowapi limiter is a process-wide singleton with shared counters; reset it
    around every test so accumulated hits don't bleed across tests (memory:// storage)."""
    _clear_limiter_storage()
    yield
    _clear_limiter_storage()


# ── Phase 5: Celery eager mode ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _celery_eager():
    """Run Celery tasks inline (no broker/worker) so .delay() is synchronous and
    exceptions propagate to the caller — keeps the suite offline and deterministic."""
    try:
        from worker.celery_app import celery_app
    except Exception:
        yield
        return
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
