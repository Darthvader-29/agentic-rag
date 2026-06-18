"""Graph-level tests for agents.graph.build_graph().

1. ROUTING/SYNTHESIS: for a given query + fixed fake provider + fake collection,
   ``graph.ainvoke(initial_state)`` resolves the route and yields the provider's answer.
2. PARALLEL: the ``BOTH`` route runs web + vector concurrently — both result keys appear in the
   final state.

(The old linear-flow parity comparison was dropped in R29 with the removal of the dead
``route_query`` / ``generate_final_response`` helpers; the graph is now the only execution path,
so these tests assert its output directly.)
"""

import pytest
from langgraph.graph.state import CompiledStateGraph

from agents.graph import build_graph

# ── fakes (fixed outputs so the assertions are deterministic) ─────────────────


class _FakeProvider:
    """A provider whose route()/generate() ignore their inputs and return fixed values — so the
    graph's resolved route and final answer are deterministic (the wrapped synthesis query and the
    retrieved context can't change them)."""

    def __init__(self, base_route: str, gen_returns: str) -> None:
        self.base_route = base_route
        self.gen_returns = gen_returns

    async def route(self, query, *, has_documents, web_allowed, history=None):
        return self.base_route

    async def generate(self, query, context, decision, *, history=None):
        return self.gen_returns

    async def stream(self, query, context, decision, *, history=None):
        yield self.gen_returns


class _FakeEmbedder:
    async def embed_single(self, text):
        return [0.0, 0.1, 0.2]


class _FakePinecone:
    def __init__(self, matches) -> None:
        self.matches = matches

    async def search_vectors(self, query_vector, top_k=5, session_id=None, user_id=None):
        return self.matches


class _FakeWeb:
    def __init__(self, results) -> None:
        self.results = results

    async def search_web(self, query, max_results=5):
        return self.results


def _initial_state(
    provider, pinecone, embedder, web, *, has_documents, web_allowed, query="what is X?"
):
    return {
        "query": query,
        "session_id": "s1",
        "user_id": "u1",
        "provider": provider,
        "pinecone": pinecone,
        "embedder": embedder,
        "web": web,
        "history": [],
        "has_documents": has_documents,
        "web_search_allowed": web_allowed,
    }


# ── build ─────────────────────────────────────────────────────────────────────


def test_build_graph_compiles():
    graph = build_graph()
    assert isinstance(graph, CompiledStateGraph)


# ── routing + synthesis ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_rag_with_relevant_docs():
    """RAG intent + relevant docs: the graph retrieves and returns the grounded answer."""
    matches = [{"text": "doc-a", "score": 0.9}, {"text": "doc-b", "score": 0.6}]
    p = _FakeProvider("RAG", "Grounded answer.")
    pc, emb, web = _FakePinecone(matches), _FakeEmbedder(), _FakeWeb([])

    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(p, pc, emb, web, has_documents=True, web_allowed=False)
    )
    assert final["route"] == "RAG"
    assert final["answer"] == "Grounded answer."


@pytest.mark.asyncio
async def test_graph_direct_no_docs():
    """DIRECT intent, no docs, web disabled: the graph answers directly."""
    p = _FakeProvider("DIRECT", "Direct answer.")
    pc, emb, web = _FakePinecone([]), _FakeEmbedder(), _FakeWeb([])

    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(p, pc, emb, web, has_documents=False, web_allowed=False)
    )
    assert final["route"] == "DIRECT"
    assert final["answer"] == "Direct answer."


@pytest.mark.asyncio
async def test_graph_web_no_docs():
    """WEB intent, no docs, web enabled: the graph searches the web and returns its answer."""
    p = _FakeProvider("WEB", "Web answer.")
    pc, emb, web = _FakePinecone([]), _FakeEmbedder(), _FakeWeb([{"title": "t", "snippet": "s"}])

    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(p, pc, emb, web, has_documents=False, web_allowed=True)
    )
    assert final["route"] == "WEB"
    assert final["answer"] == "Web answer."


@pytest.mark.asyncio
async def test_graph_strips_component_block_from_answer():
    """A component block in the model output is stripped from answer + surfaced in components."""
    gen = 'Prose.\n```json\n{"type": "callout", "text": "note"}\n```'
    p = _FakeProvider("DIRECT", gen)
    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(
            p,
            _FakePinecone([]),
            _FakeEmbedder(),
            _FakeWeb([]),
            has_documents=False,
            web_allowed=False,
        )
    )
    assert "```json" not in final["answer"]
    assert final["components"] == [{"type": "callout", "level": "info", "text": "note"}]


# ── parallel fan-out ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_both_route_runs_web_and_vector_in_parallel():
    """BOTH route (WEB intent + relevant docs + web allowed) populates BOTH disjoint result keys."""
    matches = [{"text": "doc-a", "score": 0.95}]
    p = _FakeProvider("WEB", "Combined answer.")
    pc = _FakePinecone(matches)
    web = _FakeWeb([{"title": "t", "snippet": "web snippet"}])

    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(p, pc, _FakeEmbedder(), web, has_documents=True, web_allowed=True)
    )
    assert final["route"] == "BOTH"
    # Both branches wrote their disjoint keys — neither clobbered the other on fan-in.
    assert "web snippet" in final["web_result"]
    assert "doc-a" in final["vector_result"]
    assert final["docs_relevant"] is True
    assert final["answer"] == "Combined answer."


@pytest.mark.asyncio
async def test_graph_direct_skips_retrieval():
    """DIRECT route reaches synthesis without populating vector/web result keys."""
    p = _FakeProvider("DIRECT", "Direct.")
    graph = build_graph()
    final = await graph.ainvoke(
        _initial_state(
            p,
            _FakePinecone([{"text": "x", "score": 0.9}]),
            _FakeEmbedder(),
            _FakeWeb([{"title": "t", "snippet": "s"}]),
            has_documents=False,
            web_allowed=False,
        )
    )
    assert final["route"] == "DIRECT"
    assert "vector_result" not in final
    assert "web_result" not in final
    assert final["answer"] == "Direct."
