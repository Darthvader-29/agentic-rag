"""Phase 7: GET /api/sessions/{id}/memory and /graph endpoints (mocked stores; no DB).

Stores + repo ownership are mocked, so these run offline: they assert the response shapes the
frontend Insights panels consume and that a foreign/missing session is rejected before any store
read.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import app
from auth.dependencies import get_current_user
from database.models import User
from dependencies import get_db_session, get_knowledge_graph, get_markdown_memory


def _fake_user(uid=None):
    u = MagicMock(spec=User)
    u.id = uid or uuid.uuid4()
    return u


def _owned(user):
    s = MagicMock()
    s.user_id = user.id
    return s


@pytest.mark.asyncio
async def test_get_memory_returns_content():
    user = _fake_user()
    md = AsyncMock()
    md.read_with_updated.return_value = ("Q: hi\nA: hello", "2026-06-04T00:00:00+00:00")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_markdown_memory] = lambda: md
    try:
        with patch("app.repo.get_session", new_callable=AsyncMock, return_value=_owned(user)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/sessions/s1/memory", headers={"Authorization": "Bearer t"}
                )
        assert resp.status_code == 200
        assert resp.json() == {
            "session_id": "s1",
            "content": "Q: hi\nA: hello",
            "updated_at": "2026-06-04T00:00:00+00:00",
        }
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_memory_404_for_foreign_session():
    user = _fake_user()
    foreign = MagicMock()
    foreign.user_id = uuid.uuid4()  # different owner
    md = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_markdown_memory] = lambda: md
    try:
        with patch("app.repo.get_session", new_callable=AsyncMock, return_value=foreign):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/sessions/s1/memory", headers={"Authorization": "Bearer t"}
                )
        assert resp.status_code == 404
        md.read_with_updated.assert_not_called()  # rejected before any store read
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_graph_returns_node_link():
    user = _fake_user()
    kg = AsyncMock()
    kg.export.return_value = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "links": [{"source": "A", "target": "B", "relation": "rel"}],
    }
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_knowledge_graph] = lambda: kg
    try:
        with patch("app.repo.get_session", new_callable=AsyncMock, return_value=_owned(user)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/sessions/s1/graph", headers={"Authorization": "Bearer t"}
                )
        assert resp.status_code == 200
        body = resp.json()
        assert {n["id"] for n in body["nodes"]} == {"A", "B"}
        assert body["links"][0]["relation"] == "rel"
    finally:
        app.dependency_overrides.clear()
