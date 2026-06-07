"""Phase 7: OpenTelemetry trace-emission tests.

These run with ``OTEL_ENABLED`` off — the explicit spans (``chat.request`` …) normally go to the
no-op default tracer, but here a real ``TracerProvider`` + in-memory exporter is installed so we can
assert the span names appear. The JSON chat path carries ``chat.request`` (the SSE path is covered
by the FastAPI ASGI span in production). ``init_tracing``'s enabled branch is covered directly with
``set_tracer_provider`` monkeypatched so the tests don't mutate the process-global provider.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


@pytest.fixture
def span_exporter():
    """Capture spans created via ``observability.get_tracer()`` during a test.

    OTEL allows ``set_tracer_provider`` once per process; reuse the installed provider and attach a
    fresh in-memory exporter (multiple span processors are allowed), clearing it at teardown.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


class _FakeGraph:
    """Minimal stand-in: the JSON path only calls ``ainvoke``."""

    async def ainvoke(self, state):
        return {"answer": "hi there", "route": "DIRECT", "context": "", "components": []}


def _fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


@pytest.mark.asyncio
async def test_chat_json_emits_chat_request_span(span_exporter):
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: _FakeGraph()
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()  # repo.* is patched below
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
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 200
        spans = {s.name: s for s in span_exporter.get_finished_spans()}
        assert "chat.request" in spans
        attrs = spans["chat.request"].attributes or {}
        assert attrs.get("session.id") == "s1"
        assert attrs.get("transport") == "json"
    finally:
        app.dependency_overrides.clear()


def test_init_tracing_disabled_is_noop():
    from config import Settings
    from observability.tracing import get_tracer, init_tracing

    init_tracing(Settings())  # OTEL_ENABLED defaults False → no-op, must not raise
    assert get_tracer() is not None


def test_init_tracing_enabled_installs_provider(monkeypatch):
    import observability.tracing as t
    from config import Settings

    monkeypatch.setattr(t, "_INITIALIZED", False)
    captured: dict = {}
    monkeypatch.setattr(t.trace, "set_tracer_provider", lambda p: captured.__setitem__("p", p))

    t.init_tracing(Settings(OTEL_ENABLED=True, OTEL_EXPORTER_ENDPOINT=None))  # console exporter
    assert isinstance(captured["p"], TracerProvider)
    captured["p"].shutdown()


def test_init_tracing_with_endpoint_uses_otlp(monkeypatch):
    import observability.tracing as t
    from config import Settings

    monkeypatch.setattr(t, "_INITIALIZED", False)
    captured: dict = {}
    monkeypatch.setattr(t.trace, "set_tracer_provider", lambda p: captured.__setitem__("p", p))

    # OTLP gRPC exporter constructs offline (lazy channel) — exercises the endpoint branch.
    t.init_tracing(Settings(OTEL_ENABLED=True, OTEL_EXPORTER_ENDPOINT="http://localhost:4317"))
    assert isinstance(captured["p"], TracerProvider)
    captured["p"].shutdown()
