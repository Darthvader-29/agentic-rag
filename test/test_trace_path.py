"""Phase 7: full-path trace emission across the agent graph.

Drives the REAL compiled graph (RAG route via a mocked provider) with mocked clients + a hybrid
retriever wired onto app.state, and asserts the key-path spans appear:
chat.request → agent.retrieval → memory.hybrid.retrieve → agent.synthesis. OTEL_ENABLED is off; a
real TracerProvider + in-memory exporter is installed so the always-on explicit spans are captured.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agents.graph import build_graph
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
from memory.hybrid import HybridRetriever

WEIGHTS = {"vector": 0.6, "graph": 0.25, "markdown": 0.15}


@pytest.fixture
def span_exporter():
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def _fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


@pytest.mark.asyncio
async def test_chat_emits_full_span_path(span_exporter):
    provider = AsyncMock()
    provider.route.return_value = "RAG"
    provider.generate.return_value = "the answer"
    pinecone = AsyncMock()
    pinecone.search_vectors.return_value = [{"text": "chunk", "score": 0.9}]
    embedder = AsyncMock()
    embedder.embed_single.return_value = [0.1] * 384
    web = AsyncMock()
    web.search_web.return_value = []

    graph_store = AsyncMock()
    graph_store.neighbors.return_value = ["Ada"]
    markdown = AsyncMock()
    markdown.read.return_value = "prior notes"

    saved_hybrid = getattr(app.state, "hybrid_retriever", None)
    saved_md = getattr(app.state, "markdown_memory", None)
    app.state.hybrid_retriever = HybridRetriever(graph_store, markdown, WEIGHTS)
    app.state.markdown_memory = markdown

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: build_graph()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    app.dependency_overrides[get_pinecone_client] = lambda: pinecone
    app.dependency_overrides[get_embedding_client] = lambda: embedder
    app.dependency_overrides[get_web_search_client] = lambda: web
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=True),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={
                        "message": "tell me about Ada",
                        "session_id": "s1",
                        "web_search_allowed": False,
                    },
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 200
        layers = resp.json()["layers"]
        assert "graph" in layers or "memory" in layers  # hybrid fed synthesis
        names = {s.name for s in span_exporter.get_finished_spans()}
        assert {
            "chat.request",
            "agent.retrieval",
            "agent.synthesis",
            "memory.hybrid.retrieve",
        } <= names
    finally:
        app.dependency_overrides.clear()
        app.state.hybrid_retriever = saved_hybrid
        app.state.markdown_memory = saved_md
