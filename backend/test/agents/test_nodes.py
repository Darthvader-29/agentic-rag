"""Isolated node tests (agents.nodes) — each node with FAKE provider/pinecone/embedder/web.

No network, no graph. Verifies disjoint state writes, the >=0.4 relevance gate in the vector node,
and the synthesis node's dual streaming/non-streaming behavior + component handling.
"""

import pytest

from agents.nodes import synthesis_node, vector_node, web_node

# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed_single(self, text):
        self.calls.append(text)
        return [0.1, 0.2, 0.3]


class _FakePinecone:
    """Returns canned matches; ``scores`` controls the relevance gate."""

    def __init__(self, matches=None) -> None:
        self.matches = matches if matches is not None else []
        self.search_calls: list[dict] = []

    async def search_vectors(self, query_vector, top_k=5, session_id=None, user_id=None):
        self.search_calls.append(
            {"query_vector": query_vector, "top_k": top_k, "session_id": session_id}
        )
        return self.matches


class _FakeWeb:
    def __init__(self, results=None) -> None:
        self.results = results if results is not None else []
        self.search_calls: list[dict] = []

    async def search_web(self, query, max_results=5):
        self.search_calls.append({"query": query, "max_results": max_results})
        return self.results


class _FakeProvider:
    """generate() returns a canned string; stream() yields canned deltas."""

    def __init__(self, gen_returns="GEN", deltas=None) -> None:
        self.gen_returns = gen_returns
        self.deltas = deltas if deltas is not None else ["GEN"]
        self.generate_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def route(self, query, *, has_documents, web_allowed, history=None):  # pragma: no cover
        return "DIRECT"

    async def generate(self, query, context, decision, *, history=None):
        self.generate_calls.append({"query": query, "context": context, "decision": decision})
        return self.gen_returns

    async def stream(self, query, context, decision, *, history=None):
        self.stream_calls.append({"query": query, "context": context, "decision": decision})
        for d in self.deltas:
            yield d


def _noop_writer(_chunk):
    """Stand-in for the StreamWriter langgraph injects; discards in the non-streaming path."""


def _capturing_writer(sink: list):
    """A writer that records every custom payload, mimicking astream(stream_mode=['custom'])."""

    def _w(chunk):
        sink.append(chunk)

    return _w


_STREAM_CONFIG = {"configurable": {"stream": True}}
_NO_STREAM_CONFIG: dict = {"configurable": {}}


def _state(**over):
    base = {
        "query": "What is X?",
        "session_id": "s1",
        "user_id": "u1",
        "provider": _FakeProvider(),
        "pinecone": _FakePinecone(),
        "embedder": _FakeEmbedder(),
        "web": _FakeWeb(),
        "history": [],
        "has_documents": False,
        "web_search_allowed": True,
        "rewritten_query": "What is X?",
        "route": "DIRECT",
    }
    base.update(over)
    return base


# ── vector_node ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vector_node_relevant_docs_set_context_and_flag():
    pc = _FakePinecone(
        matches=[
            {"text": "alpha chunk", "score": 0.82},
            {"text": "beta chunk", "score": 0.51},
        ]
    )
    emb = _FakeEmbedder()
    out = await vector_node(_state(pinecone=pc, embedder=emb, route="RAG"))
    assert out["docs_relevant"] is True
    assert "alpha chunk" in out["context"]
    assert "beta chunk" in out["context"]
    assert "alpha chunk" in out["vector_result"]
    # embedder was called with the rewritten query; pinecone scoped to the session
    assert emb.calls == ["What is X?"]
    assert pc.search_calls[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_vector_node_below_threshold_drops_context():
    pc = _FakePinecone(matches=[{"text": "weak chunk", "score": 0.31}])
    out = await vector_node(_state(pinecone=pc, route="RAG"))
    assert out["docs_relevant"] is False
    assert out["context"] == ""  # weak context dropped
    assert out["vector_result"] == ""


@pytest.mark.asyncio
async def test_vector_node_no_matches_marks_irrelevant():
    out = await vector_node(_state(pinecone=_FakePinecone(matches=[]), route="RAG"))
    assert out["docs_relevant"] is False
    assert out["context"] == ""


@pytest.mark.asyncio
async def test_vector_node_uses_rewritten_query_for_embedding():
    emb = _FakeEmbedder()
    pc = _FakePinecone(matches=[{"text": "c", "score": 0.9}])
    await vector_node(
        _state(
            embedder=emb, pinecone=pc, query="raw", rewritten_query="resolved query", route="RAG"
        )
    )
    assert emb.calls == ["resolved query"]


@pytest.mark.asyncio
async def test_vector_node_search_failure_degrades_gracefully():
    class _BoomPinecone(_FakePinecone):
        async def search_vectors(self, *a, **k):
            raise RuntimeError("pinecone down")

    out = await vector_node(_state(pinecone=_BoomPinecone(), route="RAG"))
    assert out["docs_relevant"] is False
    assert out["context"] == ""


# ── web_node ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_node_writes_web_result_disjoint_key():
    web = _FakeWeb(
        results=[
            {"title": "T1", "snippet": "snippet one"},
            {"title": "T2", "snippet": "snippet two"},
        ]
    )
    out = await web_node(_state(web=web, route="WEB"))
    assert "snippet one" in out["web_result"]
    assert "snippet two" in out["web_result"]
    assert set(out.keys()) == {"web_result"}  # disjoint: never writes context/vector_result
    assert web.search_calls[0]["query"] == "What is X?"


@pytest.mark.asyncio
async def test_web_node_uses_rewritten_query():
    web = _FakeWeb(results=[{"title": "T", "snippet": "s"}])
    await web_node(_state(web=web, query="raw", rewritten_query="resolved", route="WEB"))
    assert web.search_calls[0]["query"] == "resolved"


@pytest.mark.asyncio
async def test_web_node_empty_results_yields_empty_string():
    out = await web_node(_state(web=_FakeWeb(results=[]), route="WEB"))
    assert out["web_result"] == ""


@pytest.mark.asyncio
async def test_web_node_search_failure_degrades_gracefully():
    class _BoomWeb(_FakeWeb):
        async def search_web(self, *a, **k):
            raise RuntimeError("ddg down")

    out = await web_node(_state(web=_BoomWeb(), route="WEB"))
    assert out["web_result"] == ""


# ── synthesis_node (non-streaming / ainvoke path) ─────────────────────────────


@pytest.mark.asyncio
async def test_synthesis_node_non_streaming_returns_prose_and_components():
    prose_with_block = (
        "Here is the comparison.\n\n"
        '```json\n{"type": "table", "columns": ["A"], "rows": [["1"]]}\n```\n'
    )
    p = _FakeProvider(gen_returns=prose_with_block)
    out = await synthesis_node(
        _state(provider=p, context="ctx", route="RAG"), _noop_writer, _NO_STREAM_CONFIG
    )
    assert "Here is the comparison." in out["answer"]
    assert "```json" not in out["answer"]  # component block stripped from prose
    assert out["components"] == [{"type": "table", "columns": ["A"], "rows": [["1"]]}]
    # non-streaming path uses generate(), not stream()
    assert p.generate_calls and not p.stream_calls


@pytest.mark.asyncio
async def test_synthesis_node_drops_malformed_component_keeps_prose():
    p = _FakeProvider(gen_returns="Answer body.\n```json\n{bad json}\n```")
    out = await synthesis_node(_state(provider=p, route="DIRECT"), _noop_writer, _NO_STREAM_CONFIG)
    assert "Answer body." in out["answer"]
    assert out["components"] == []  # malformed → dropped


@pytest.mark.asyncio
async def test_synthesis_node_plain_prose_no_components():
    p = _FakeProvider(gen_returns="Just a plain answer.")
    out = await synthesis_node(_state(provider=p, route="DIRECT"), _noop_writer, _NO_STREAM_CONFIG)
    assert out["answer"] == "Just a plain answer."
    assert out["components"] == []


@pytest.mark.asyncio
async def test_synthesis_node_passes_context_and_route_to_provider():
    p = _FakeProvider(gen_returns="x")
    await synthesis_node(
        _state(provider=p, context="MY CONTEXT", route="RAG"), _noop_writer, _NO_STREAM_CONFIG
    )
    call = p.generate_calls[0]
    assert call["context"] == "MY CONTEXT"
    assert call["decision"] == "RAG"


# ── synthesis_node (streaming / writer-aware path) ────────────────────────────


@pytest.mark.asyncio
async def test_synthesis_node_streams_tokens_to_writer():
    """With the stream flag set, prose deltas are emitted as {"kind":"token"} writes and the
    returned answer is the concatenation."""
    p = _FakeProvider(deltas=["Hello", ", ", "world"])
    sink: list = []
    out = await synthesis_node(
        _state(provider=p, route="DIRECT"), _capturing_writer(sink), _STREAM_CONFIG
    )
    assert p.stream_calls and not p.generate_calls  # streaming path used stream()
    tokens = [c["text"] for c in sink if c["kind"] == "token"]
    assert "".join(tokens) == "Hello, world"
    assert out["answer"] == "Hello, world"
    assert out["components"] == []


@pytest.mark.asyncio
async def test_synthesis_node_streams_component_whole_even_when_fence_split():
    """A component fence split across deltas is emitted as ONE {"kind":"component"} write, never
    leaked as prose tokens, and accumulated into the returned components."""
    p = _FakeProvider(
        deltas=["Intro ", "``", "`json\n", '{"type": "call', 'out", "text": "hi"}', "\n```", " end"]
    )
    sink: list = []
    out = await synthesis_node(
        _state(provider=p, route="RAG", context="ctx"), _capturing_writer(sink), _STREAM_CONFIG
    )
    comp_events = [c for c in sink if c["kind"] == "component"]
    assert comp_events == [
        {"data": {"type": "callout", "level": "info", "text": "hi"}, "kind": "component"}
    ]
    token_text = "".join(c["text"] for c in sink if c["kind"] == "token")
    assert "Intro " in token_text and "end" in token_text
    assert "```json" not in token_text  # recognized fence never streamed as prose
    assert out["components"] == [{"type": "callout", "level": "info", "text": "hi"}]
    assert "Intro" in out["answer"]


@pytest.mark.asyncio
async def test_synthesis_node_stream_flag_false_uses_generate():
    """A present-but-falsey stream flag must NOT stream (parity path stays on generate())."""
    p = _FakeProvider(gen_returns="from generate")
    sink: list = []
    out = await synthesis_node(
        _state(provider=p, route="DIRECT"),
        _capturing_writer(sink),
        {"configurable": {"stream": False}},
    )
    assert p.generate_calls and not p.stream_calls
    assert sink == []  # nothing written
    assert out["answer"] == "from generate"
