"""Supervisor routing-contract tests (agents.nodes.supervisor_node).

The supervisor ports the old ``decide_combined_route`` *intent* step: it makes ONE provider call
that yields a flat ``route`` ∈ {RAG, WEB, BOTH, DIRECT} plus a context-resolved ``rewritten_query``
(query rewriting folded in per docs/09 Decision 4 — zero extra LLM calls). Below we pin the routing
map and the defensive fallback default on a malformed/raising provider.
"""

import pytest

from agents.nodes import route_after_supervisor, supervisor_node


class _FakeProvider:
    """Records the route() it was asked for and returns a canned base route (RAG/WEB/DIRECT)."""

    def __init__(self, base_route: str = "DIRECT", raises: Exception | None = None) -> None:
        self.base_route = base_route
        self.raises = raises
        self.route_calls: list[dict] = []

    async def route(self, query, *, has_documents, web_allowed, history=None):
        self.route_calls.append(
            {
                "query": query,
                "has_documents": has_documents,
                "web_allowed": web_allowed,
                "history": history,
            }
        )
        if self.raises:
            raise self.raises
        return self.base_route

    async def generate(self, query, context, decision, *, history=None):  # pragma: no cover
        return "answer"

    async def stream(self, query, context, decision, *, history=None):  # pragma: no cover
        if False:
            yield ""


def _state(provider, *, query="What is X?", history=None, has_documents=False, web_allowed=True):
    return {
        "query": query,
        "session_id": "s1",
        "user_id": "u1",
        "provider": provider,
        "pinecone": object(),
        "embedder": object(),
        "web": object(),
        "history": history if history is not None else [],
        "has_documents": has_documents,
        "web_search_allowed": web_allowed,
    }


# ── route mapping ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_rag_intent_with_docs_routes_rag():
    p = _FakeProvider("RAG")
    out = await supervisor_node(_state(p, has_documents=True, web_allowed=False))
    assert out["route"] == "RAG"


@pytest.mark.asyncio
async def test_supervisor_web_intent_routes_web_when_allowed():
    p = _FakeProvider("WEB")
    out = await supervisor_node(_state(p, has_documents=False, web_allowed=True))
    assert out["route"] == "WEB"


@pytest.mark.asyncio
async def test_supervisor_direct_intent_routes_direct():
    p = _FakeProvider("DIRECT")
    out = await supervisor_node(_state(p, has_documents=False, web_allowed=False))
    assert out["route"] == "DIRECT"


@pytest.mark.asyncio
async def test_supervisor_web_intent_with_relevant_docs_prefers_both():
    """When the user has documents AND web is allowed, an intent toward the web should fan out to
    BOTH so the relevance gate in the vector node can fall back to web (docs/09 §2.1)."""
    p = _FakeProvider("WEB")
    out = await supervisor_node(_state(p, has_documents=True, web_allowed=True))
    assert out["route"] == "BOTH"


@pytest.mark.asyncio
async def test_supervisor_web_intent_with_docs_but_web_disabled_routes_rag():
    """Web disabled collapses a WEB+docs intent onto RAG (parity with the old web_allowed gate)."""
    p = _FakeProvider("WEB")
    out = await supervisor_node(_state(p, has_documents=True, web_allowed=False))
    assert out["route"] == "RAG"


@pytest.mark.asyncio
async def test_supervisor_passes_flags_to_provider_route():
    p = _FakeProvider("DIRECT")
    await supervisor_node(_state(p, has_documents=True, web_allowed=False))
    assert p.route_calls == [
        {
            "query": "What is X?",
            "has_documents": True,
            "web_allowed": False,
            "history": [],
        }
    ]


# ── rewritten_query ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_sets_rewritten_query_to_raw_query_without_history():
    p = _FakeProvider("DIRECT")
    out = await supervisor_node(_state(p, query="What is X?", history=[]))
    assert out["rewritten_query"] == "What is X?"


@pytest.mark.asyncio
async def test_supervisor_rewritten_query_is_present_for_followups():
    """With prior turns, the supervisor still emits a usable, non-empty rewritten_query."""
    history = [
        {"role": "user", "content": "Tell me about the Apollo and Gemini programs."},
        {"role": "assistant", "content": "Apollo landed on the Moon; Gemini was earlier."},
    ]
    p = _FakeProvider("DIRECT")
    out = await supervisor_node(_state(p, query="what about the second one?", history=history))
    assert isinstance(out["rewritten_query"], str)
    assert out["rewritten_query"].strip() != ""


@pytest.mark.asyncio
async def test_supervisor_passes_history_to_provider_route():
    """History reaches the routing call so a follow-up routes with prior context (H-B1 / R01)."""
    history = [
        {"role": "user", "content": "Tell me about the Apollo and Gemini programs."},
        {"role": "assistant", "content": "Apollo landed on the Moon; Gemini was earlier."},
    ]
    p = _FakeProvider("DIRECT")
    await supervisor_node(_state(p, query="what about the second one?", history=history))
    assert p.route_calls[0]["history"] == history


# ── defensive fallback ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_malformed_route_falls_back_to_safe_default():
    """A provider returning junk must not crash routing; fall back to the safe default route.

    Parity: today's flow defaults toward using the user's documents when routing is uncertain.
    With documents present the safe default is RAG; the node never raises."""
    p = _FakeProvider("not-a-real-label")
    out = await supervisor_node(_state(p, has_documents=True, web_allowed=True))
    assert out["route"] in {"RAG", "BOTH"}
    assert "rewritten_query" in out


@pytest.mark.asyncio
async def test_supervisor_provider_exception_falls_back_without_raising():
    p = _FakeProvider(raises=RuntimeError("model blew up"))
    out = await supervisor_node(_state(p, has_documents=False, web_allowed=True))
    # Must not propagate; produces a usable default route + rewritten_query.
    assert out["route"] in {"RAG", "WEB", "BOTH", "DIRECT"}
    assert out["rewritten_query"] == "What is X?"


# ── route_after_supervisor (conditional edge) ─────────────────────────────────


def test_route_after_supervisor_rag():
    assert route_after_supervisor({"route": "RAG"}) == ["vector"]


def test_route_after_supervisor_web():
    assert route_after_supervisor({"route": "WEB"}) == ["web"]


def test_route_after_supervisor_both_fans_out_parallel():
    assert sorted(route_after_supervisor({"route": "BOTH"})) == ["vector", "web"]


def test_route_after_supervisor_direct_skips_retrieval():
    assert route_after_supervisor({"route": "DIRECT"}) == ["synthesis"]
