"""DI-override integration tests: prove every endpoint pulls clients from DI."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import app
from auth.dependencies import get_current_user
from database.db_manager import PineconeClient
from database.models import User
from dependencies import (
    get_db_session,
    get_db_sessionmaker,
    get_embedding_client,
    get_pinecone_client,
    get_s3_client,
    get_web_search_client,
)
from llm.dependencies import get_llm_provider


@pytest.fixture
def fake_pinecone():
    pc = AsyncMock(spec=PineconeClient)
    pc.search_vectors.return_value = []
    pc.save_vectors.return_value = None
    pc.delete_vectors_by_session.return_value = None
    return pc


@pytest.fixture
def fake_embedder():
    emb = AsyncMock()
    emb.embed_batch.return_value = [[0.1] * 384]
    emb.embed_single.return_value = [0.1] * 384
    return emb


@pytest.fixture
def fake_s3():
    s3 = AsyncMock()
    s3.upload_fileobj.return_value = "uploads/test-key.pdf"
    s3.delete_objects.return_value = None
    return s3


@pytest.fixture
def fake_web():
    web = AsyncMock()
    web.search_web.return_value = []
    return web


@pytest.fixture
def fake_db():
    """Mock AsyncSession — returned by get_db_session override."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def fake_user():
    """A minimal User object for get_current_user override."""
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    u.email = "test@example.com"
    u.username = "testuser"
    return u


@pytest.fixture
def di_client(fake_pinecone, fake_embedder, fake_s3, fake_web, fake_db, fake_user):
    """TestClient with DI overrides and a patched lifespan to avoid real network calls."""
    fake_sessionmaker = AsyncMock()

    async def _db_session_override():
        yield fake_db

    app.dependency_overrides[get_pinecone_client] = lambda: fake_pinecone
    app.dependency_overrides[get_embedding_client] = lambda: fake_embedder
    app.dependency_overrides[get_s3_client] = lambda: fake_s3
    app.dependency_overrides[get_web_search_client] = lambda: fake_web
    app.dependency_overrides[get_db_session] = _db_session_override
    app.dependency_overrides[get_db_sessionmaker] = lambda: fake_sessionmaker
    # Phase 3: bypass auth for DI tests
    app.dependency_overrides[get_current_user] = lambda: fake_user
    # Phase 4: bypass LLM provider DI for infrastructure tests
    fake_provider = AsyncMock()
    fake_provider.route.return_value = "DIRECT"
    fake_provider.generate.return_value = "Test answer"
    app.dependency_overrides[get_llm_provider] = lambda: fake_provider

    with patch.object(PineconeClient, "ensure_index", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    app.dependency_overrides.clear()


# ── cleanup endpoint ──────────────────────────────────────────────────────────


def test_cleanup_uses_di_pinecone_s3_and_db(di_client, fake_pinecone, fake_s3, fake_user):
    """cleanup_session resolves pinecone, s3, and db from DI; keys come from Postgres."""
    fake_session = MagicMock()
    fake_session.user_id = fake_user.id

    with (
        patch("app.repo.get_session", new_callable=AsyncMock) as mock_get_sess,
        patch("app.repo.list_s3_keys_for_session", new_callable=AsyncMock) as mock_keys,
        patch("app.repo.delete_session", new_callable=AsyncMock),
    ):
        mock_get_sess.return_value = fake_session
        mock_keys.return_value = ["key1", "key2"]

        resp = di_client.post("/api/cleanup", json={"session_id": "test-session"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test-session"
    assert data["deleted_files"] == 2

    fake_pinecone.delete_vectors_by_session.assert_awaited_once_with("test-session")
    fake_s3.delete_objects.assert_awaited_once_with(["key1", "key2"])


def test_cleanup_no_files_skips_s3_delete(di_client, fake_pinecone, fake_s3, fake_user):
    """When session has no documents, s3.delete_objects is not called."""
    fake_session = MagicMock()
    fake_session.user_id = fake_user.id

    with (
        patch("app.repo.get_session", new_callable=AsyncMock) as mock_get_sess,
        patch("app.repo.list_s3_keys_for_session", new_callable=AsyncMock) as mock_keys,
        patch("app.repo.delete_session", new_callable=AsyncMock),
    ):
        mock_get_sess.return_value = fake_session
        mock_keys.return_value = []

        resp = di_client.post("/api/cleanup", json={"session_id": "empty-session"})

    assert resp.status_code == 200
    assert resp.json()["deleted_files"] == 0
    fake_s3.delete_objects.assert_not_awaited()


# ── chat endpoint ─────────────────────────────────────────────────────────────


def test_chat_uses_di_clients(di_client, fake_pinecone, fake_embedder, fake_web, fake_user):
    """chat (JSON path) runs the agentic graph with DI clients; a RAG route hits Pinecone.

    Phase 6: the compiled graph resolves provider + pinecone/embedder/web from DI. With a RAG
    intent + documents present, the vector node embeds the query and searches Pinecone for the
    >=0.4 relevance gate, then synthesis answers via the injected provider.
    """
    fake_session = MagicMock()
    fake_session.user_id = fake_user.id
    # drive a RAG route so the vector node fires (DIRECT would skip retrieval entirely)
    rag_provider = AsyncMock()
    rag_provider.route.return_value = "RAG"
    rag_provider.generate.return_value = "Test answer"
    app.dependency_overrides[get_llm_provider] = lambda: rag_provider

    with (
        patch("app.repo.get_session", new_callable=AsyncMock) as mock_get_sess,
        patch("app.repo.session_has_documents", new_callable=AsyncMock) as mock_hd,
        patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
        patch("app.repo.save_message", new_callable=AsyncMock),
    ):
        mock_get_sess.return_value = fake_session
        mock_hd.return_value = True

        resp = di_client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "session_id": "test-session",
                "web_search_allowed": False,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Test answer"
    # session_has_documents is read by the endpoint to build graph state before invoking
    mock_hd.assert_awaited()
    # the vector node embeds + searches Pinecone for the relevance gate
    fake_pinecone.search_vectors.assert_awaited()


# ── health endpoint ───────────────────────────────────────────────────────────


def test_health_returns_healthy(di_client):
    resp = di_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
