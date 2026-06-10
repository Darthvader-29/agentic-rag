"""HTTP-level tests for GET /api/keys (Phase 6) — runs offline (no DB needed).

Asserts the route is wired and auth-gated: no bearer → 401 before any DB access, and a valid
user (auth + DB mocked) gets a JSON list of {id, provider, created_at} with no ciphertext.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import app
from auth.dependencies import get_current_user
from database.models import User
from dependencies import get_db_session


def _fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


@pytest.mark.asyncio
async def test_get_keys_requires_auth():
    """No Authorization header → 401, never a 200 listing."""
    # get_current_user is NOT overridden → the real dependency runs and 401s on the missing header.
    # Override get_db_session so the no-lifespan ASGI app doesn't trip on a missing sessionmaker.
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/keys")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_rotate_key_rejects_invalid_path_provider():
    """B12: PUT /api/keys/<bad> is 422 (path provider validated) — a typo can no longer store a
    junk-provider row that later bricks chat with a 502."""
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/keys/grmini",
                json={"provider": "gemini", "api_key": "k"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_rotate_key_accepts_valid_path_provider():
    """A valid provider passes path validation and reaches the handler."""
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    rec = MagicMock()
    rec.id = uuid.uuid4()
    rec.provider = "openai"
    try:
        with patch(
            "auth.keys_router.LLMKeyRepository.rotate", new_callable=AsyncMock, return_value=rec
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/keys/openai",
                    json={"provider": "openai", "api_key": "k"},
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "openai"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_keys_lists_providers_without_ciphertext():
    """Auth + DB mocked: returns [{id, provider, created_at}] and never the ciphertext."""
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    key_id = uuid.uuid4()
    row = MagicMock()
    row.id = key_id
    row.provider = "gemini"
    row.created_at = "2026-06-02T00:00:00+00:00"
    row.ciphertext = "SECRET-CIPHERTEXT-SHOULD-NOT-LEAK"
    try:
        with patch(
            "auth.keys_router.LLMKeyRepository.list_for_user",
            new_callable=AsyncMock,
            return_value=[row],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/keys", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert body[0]["provider"] == "gemini"
        assert body[0]["id"] == str(key_id)
        assert "created_at" in body[0]
        assert "ciphertext" not in body[0]
        assert "SECRET-CIPHERTEXT-SHOULD-NOT-LEAK" not in resp.text
    finally:
        app.dependency_overrides.clear()
