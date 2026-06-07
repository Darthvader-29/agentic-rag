"""Phase 5: horizontal-scale invariants.

Any identical, stateless instance can serve any request because all shared state is
external — limiter counters in Redis, ingestion status in Postgres, vectors in Pinecone.
The only per-process objects are connection pools rebuilt in lifespan.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import app
from auth.dependencies import get_current_user
from database.db_manager import PineconeClient
from database.models import DocumentStatus, User
from dependencies import get_db_session


def test_no_module_level_mutable_request_state():
    """No module-level dict/list/set in app.py could accumulate cross-request state."""
    suspicious = {
        name: type(val).__name__
        for name, val in vars(app_module).items()
        if not name.startswith("__") and isinstance(val, (dict, list, set))
    }
    assert suspicious == {}, f"module-level mutable state found: {suspicious}"


def test_clients_are_built_per_process_not_shared_singletons():
    """from_settings builds a fresh client each call — every instance owns its pools."""
    from config import settings
    from integrations.s3.client import S3Client

    a = S3Client.from_settings(settings)
    b = S3Client.from_settings(settings)
    assert a is not b


def test_two_clients_serve_the_same_document_status():
    """Two independent TestClient sessions (≈ two instances behind a load balancer) read
    the same document status from the shared store — no client/instance affinity."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    owned = MagicMock()
    owned.user_id = user.id
    doc = MagicMock()
    doc.id, doc.filename, doc.session_id = "doc-1", "x.pdf", "s1"
    doc.s3_key, doc.status = "uploads/x", DocumentStatus.READY

    fake_db = AsyncMock()

    async def _db():
        yield fake_db

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch.object(PineconeClient, "ensure_index", new_callable=AsyncMock),
            patch("app.repo.get_document", new_callable=AsyncMock, return_value=doc),
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=owned),
        ):
            with TestClient(app) as c1, TestClient(app) as c2:
                r1 = c1.get("/api/documents/doc-1")
                r2 = c2.get("/api/documents/doc-1")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["status"] == r2.json()["status"] == "ready"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_document_status_persists_in_postgres(db_session):
    """Ingestion status lives in Postgres so any instance can poll it (upload-A / chat-B).

    Real DB round-trip — skipped unless TEST_DATABASE_URL is set (see conftest).
    """
    from auth.security import hash_password
    from database import repository as repo
    from database.repository import UserRepository

    owner = await UserRepository(db_session).create(
        email=f"mi-{uuid.uuid4().hex}@t.com",
        username=f"mi_{uuid.uuid4().hex[:12]}",
        hashed_password=hash_password("pw"),
    )
    await repo.get_or_create_session(db_session, "sess-mi", owner.id)
    doc = await repo.create_document(
        db_session, session_id="sess-mi", s3_key="uploads/mi/x.pdf", filename="x.pdf"
    )
    await repo.set_document_status(
        db_session, s3_key="uploads/mi/x.pdf", status=DocumentStatus.READY
    )
    await db_session.flush()

    # instance "B" reads the same shared row by id
    fetched = await repo.get_document(db_session, doc.id)
    assert fetched is not None
    assert fetched.status == DocumentStatus.READY
