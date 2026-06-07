"""Phase 5: per-user, Redis-backed rate limiting via slowapi.

Storage is memory:// in tests (conftest), so limits are exercised without a real Redis.
The autouse `_reset_rate_limiter` fixture clears counters between tests.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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
def fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    u.email = "rl@example.com"
    u.username = "rluser"
    return u


@pytest.fixture
def rl_client(fake_user):
    """TestClient with DI + auth overridden so /api/chat returns 200 cheaply."""
    fake_pc = AsyncMock(spec=PineconeClient)
    fake_pc.search_vectors.return_value = []
    fake_emb = AsyncMock()
    fake_emb.embed_batch.return_value = [[0.1] * 384]
    fake_web = AsyncMock()
    fake_web.search_web.return_value = []
    fake_provider = AsyncMock()
    fake_provider.route.return_value = "DIRECT"
    fake_provider.generate.return_value = "answer"

    async def _db_override():
        yield AsyncMock()

    app.dependency_overrides[get_pinecone_client] = lambda: fake_pc
    app.dependency_overrides[get_embedding_client] = lambda: fake_emb
    app.dependency_overrides[get_s3_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: fake_web
    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_sessionmaker] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_llm_provider] = lambda: fake_provider

    with patch.object(PineconeClient, "ensure_index", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    app.dependency_overrides.clear()


# ── key function: per authenticated user, else client IP ──────────────────────


def test_rate_limit_key_uses_user_when_authenticated():
    import app as app_module

    req = MagicMock()
    req.headers = {"authorization": "Bearer faketoken"}
    with patch.object(app_module, "decode_token", return_value={"sub": "user-123"}):
        assert app_module._rate_limit_key(req) == "user:user-123"


def test_rate_limit_key_falls_back_to_ip_when_anonymous():
    import app as app_module

    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "203.0.113.7"
    assert app_module._rate_limit_key(req) == "203.0.113.7"


def test_rate_limit_key_falls_back_to_ip_on_invalid_token():
    import app as app_module

    req = MagicMock()
    req.headers = {"authorization": "Bearer garbage"}
    req.client = MagicMock()
    req.client.host = "203.0.113.9"
    with patch.object(app_module, "decode_token", side_effect=Exception("bad token")):
        assert app_module._rate_limit_key(req) == "203.0.113.9"


# ── enforcement ───────────────────────────────────────────────────────────────


def test_chat_exceeds_limit_returns_429(rl_client, fake_user):
    """Past RATE_LIMIT_CHAT (30/minute) the chat route returns 429."""
    fake_session = MagicMock()
    fake_session.user_id = fake_user.id
    with (
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=fake_session),
        patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
        patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
        patch("app.repo.save_message", new_callable=AsyncMock),
    ):
        statuses = [
            rl_client.post(
                "/api/chat",
                json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
            ).status_code
            for _ in range(40)
        ]
    assert 200 in statuses  # early requests pass
    assert 429 in statuses  # later requests are throttled
    assert statuses[-1] == 429


def test_health_is_never_rate_limited(rl_client):
    """/health carries no limit decorator → liveness probes are never throttled."""
    statuses = {rl_client.get("/health").status_code for _ in range(150)}
    assert statuses == {200}
