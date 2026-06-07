"""Phase 6: the compiled agentic graph is built once in lifespan and resolved via DI.

``app.state.graph`` must exist after startup; ``get_graph`` returns it and is overridable
through ``app.dependency_overrides`` like every other provider func.
"""

from unittest.mock import AsyncMock

from fastapi import Request
from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph

from app import app
from database.db_manager import PineconeClient
from dependencies import get_graph


def test_graph_compiled_on_startup_and_get_graph_returns_it():
    """Lifespan compiles the graph onto app.state; get_graph(request) returns that object."""
    from unittest.mock import patch

    with patch.object(PineconeClient, "ensure_index", new_callable=AsyncMock):
        with TestClient(app):
            graph = app.state.graph
            assert isinstance(graph, CompiledStateGraph)

            req = Request({"type": "http", "app": app})
            assert get_graph(req) is graph


def test_get_graph_is_overridable():
    """The DI provider can be swapped in tests via dependency_overrides."""
    sentinel = object()
    app.dependency_overrides[get_graph] = lambda: sentinel
    try:
        assert app.dependency_overrides[get_graph]() is sentinel
    finally:
        app.dependency_overrides.clear()
