"""Test that /api/chat resolves the LLM provider via DI and uses it through the agentic graph.

Phase 6: the JSON path runs the real compiled graph with the injected provider. A DIRECT route
(no docs, web off) reaches synthesis without touching Pinecone/web, so the canned ``generate``
answer surfaces in the ``{answer, route, context_count, session_id}`` JSON response.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agents.graph import build_graph
from app import app
from auth.dependencies import get_current_user
from database.models import User
from dependencies import (
    get_embedding_client,
    get_graph,
    get_pinecone_client,
    get_web_search_client,
)
from llm.dependencies import get_llm_provider


class _FakeProvider:
    canned_answer = "fake-provider-answer"

    async def route(self, query, *, has_documents, web_allowed):
        return "DIRECT"

    async def generate(self, query, context, decision):
        return self.canned_answer

    async def stream(self, query, context, decision):
        yield self.canned_answer


def _fake_user():
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    return user


@pytest.mark.asyncio
async def test_chat_uses_injected_provider():
    fake_provider = _FakeProvider()
    fake_user = _fake_user()

    app.dependency_overrides[get_llm_provider] = lambda: fake_provider
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_graph] = build_graph  # real graph, no lifespan needed
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hello", "web_search_allowed": False},
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == fake_provider.canned_answer
        assert body["route"] == "DIRECT"
        assert body["context_count"] == 0
        assert "session_id" in body
    finally:
        app.dependency_overrides.clear()
