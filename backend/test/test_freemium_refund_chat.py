"""R16 (integration): a failed chat turn refunds the free-tier reservation it consumed.

The allowance is reserved inside the ``get_llm_provider`` dependency BEFORE the agentic graph runs.
If the graph then fails, the reservation must be credited back so a failed/aborted turn nets ZERO
shared-quota consumption. These tests drive the real ``/api/chat`` handler with the REAL
``get_llm_provider`` (so the refund wiring on ``request.state`` is exercised) against a fakeredis,
forcing the graph to fail, and assert the daily counters end where they started.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from app import app
from auth.dependencies import get_current_user
from database.models import User
from dependencies import (
    get_db_session,
    get_embedding_client,
    get_graph,
    get_pinecone_client,
    get_redis,
    get_web_search_client,
)
from llm import freemium
from llm.freemium import GLOBAL_CALLS_PER_QUERY, _utc_day_stamp


def _fake_user() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    return user


def _counters(redis, user_id) -> tuple[int | None, int | None]:
    """Return (user_count, global_count) as ints, or None if the key is absent."""
    stamp = _utc_day_stamp()

    async def _read():
        u = await redis.get(f"freetier:user:{user_id}:{stamp}")
        g = await redis.get(f"freetier:global:{stamp}")
        return (int(u) if u is not None else None, int(g) if g is not None else None)

    return _read()


@pytest.mark.asyncio
async def test_failed_json_turn_nets_zero_allowance(monkeypatch):
    """A free-tier JSON turn whose graph raises ends with both counters back at 0 (net-zero)."""
    monkeypatch.setattr(freemium.settings, "FREE_TIER_DAILY_USER_QUERIES", 10)
    monkeypatch.setattr(freemium.settings, "FREE_TIER_GLOBAL_DAILY_CALLS", 1200)

    user = _fake_user()
    redis = fakeredis.FakeRedis()

    # A graph whose ainvoke raises → the JSON path's except runs (and must refund).
    failing_graph = MagicMock()
    failing_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph boom"))

    async def _db_session_override():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = _db_session_override
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph] = lambda: failing_graph
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    try:
        with (
            # Force the FREE-TIER rung: no stored key, operator fallback configured.
            patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock, return_value=None),
            patch(
                "llm.dependencies.get_user_llm_key_for_provider",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("llm.gemini.genai.Client"),
            patch("llm.dependencies.settings") as dep_settings,
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
        ):
            dep_settings.FREE_TIER_MODEL = "gemini-2.5-flash"
            dep_settings.LLM_FALLBACK_API_KEY = MagicMock()
            dep_settings.LLM_FALLBACK_API_KEY.get_secret_value.return_value = "sk-fallback"
            # tier_* are only consulted on the BYOK rungs; harmless defaults for safety.
            dep_settings.tier_route_model.return_value = "r"
            dep_settings.tier_synth_model.return_value = "s"

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hello", "web_search_allowed": False},
                    headers={"Authorization": "Bearer test-token"},
                )

        # The turn failed (graph raised) → 500, and the reservation was refunded to net-zero.
        assert resp.status_code == 500
        user_count, global_count = await _counters(redis, user.id)
        assert user_count == 0
        assert global_count == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_successful_json_turn_keeps_the_reservation(monkeypatch):
    """Control: a turn that SUCCEEDS keeps its reservation (1 user query + GLOBAL_CALLS_PER_QUERY)."""
    monkeypatch.setattr(freemium.settings, "FREE_TIER_DAILY_USER_QUERIES", 10)
    monkeypatch.setattr(freemium.settings, "FREE_TIER_GLOBAL_DAILY_CALLS", 1200)

    user = _fake_user()
    redis = fakeredis.FakeRedis()

    ok_graph = MagicMock()
    ok_graph.ainvoke = AsyncMock(return_value={"answer": "hi", "route": "DIRECT"})

    async def _db_session_override():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = _db_session_override
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph] = lambda: ok_graph
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    try:
        with (
            patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock, return_value=None),
            patch(
                "llm.dependencies.get_user_llm_key_for_provider",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("llm.gemini.genai.Client"),
            patch("llm.dependencies.settings") as dep_settings,
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            dep_settings.FREE_TIER_MODEL = "gemini-2.5-flash"
            dep_settings.LLM_FALLBACK_API_KEY = MagicMock()
            dep_settings.LLM_FALLBACK_API_KEY.get_secret_value.return_value = "sk-fallback"
            dep_settings.tier_route_model.return_value = "r"
            dep_settings.tier_synth_model.return_value = "s"

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hello", "web_search_allowed": False},
                    headers={"Authorization": "Bearer test-token"},
                )

        assert resp.status_code == 200
        user_count, global_count = await _counters(redis, user.id)
        assert user_count == 1
        assert global_count == GLOBAL_CALLS_PER_QUERY
    finally:
        app.dependency_overrides.clear()
