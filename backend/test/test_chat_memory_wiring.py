"""Integration-style test: a second chat turn sees the first turn in graph history (Phase 6).

BE-2 wired conversation memory in app.py via ``repo.load_recent_messages`` →
``state["history"]`` and ``repo.save_message`` on each turn. This test does NOT re-implement that
wiring; it asserts it by mocking the repo + graph (no DB, no LLM) and inspecting the GraphState the
endpoint feeds into the graph on turn two.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

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
    get_web_search_client,
)
from llm.dependencies import get_llm_provider


def _fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


class _StateCapturingGraph:
    """Captures the state passed to ainvoke so we can assert history was injected."""

    def __init__(self):
        self.seen_state = None

    async def ainvoke(self, state):
        self.seen_state = state
        return {"answer": "second answer", "route": "DIRECT", "context": "", "components": []}


@pytest.mark.asyncio
async def test_second_turn_sees_first_turn_in_history():
    """load_recent_messages feeds prior turns into state['history'] for the next request."""
    fake_graph = _StateCapturingGraph()

    # Simulate persisted history from turn one: a user message + its assistant reply.
    prior = [
        MagicMock(role="user", content="what is the capital of France?"),
        MagicMock(role="assistant", content="Paris."),
    ]

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch(
                "app.repo.load_recent_messages", new_callable=AsyncMock, return_value=prior
            ) as load_hist,
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={
                        "message": "and what about Germany?",
                        "session_id": "sess-memory",
                        "web_search_allowed": False,
                    },
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200

        # history was loaded for THIS session
        assert load_hist.await_args.kwargs["session_id"] == "sess-memory"

        # the graph received the prior turn in state['history'] as ordered Turn dicts
        history = fake_graph.seen_state["history"]
        assert history == [
            {"role": "user", "content": "what is the capital of France?"},
            {"role": "assistant", "content": "Paris."},
        ]
        # and the new user message is the live query (not in history yet)
        assert fake_graph.seen_state["query"] == "and what about Germany?"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_turn_is_persisted_after_answer():
    """Each completed turn persists user + assistant messages so the NEXT turn can load them."""
    fake_graph = _StateCapturingGraph()
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock) as save_msg,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={
                        "message": "first question",
                        "session_id": "sess-persist",
                        "web_search_allowed": False,
                    },
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        roles = [c.kwargs["role"] for c in save_msg.await_args_list]
        assert roles == ["user", "assistant"]
        contents = {c.kwargs["role"]: c.kwargs["content"] for c in save_msg.await_args_list}
        assert contents["user"] == "first question"
        assert contents["assistant"] == "second answer"
    finally:
        app.dependency_overrides.clear()
