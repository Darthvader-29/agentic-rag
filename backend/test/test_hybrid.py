"""Phase 7: hybrid retriever merge/normalise/rank (no DB; fake graph + markdown stores)."""

import pytest

from memory.hybrid import HybridRetriever, query_terms

WEIGHTS = {"vector": 0.6, "graph": 0.25, "markdown": 0.15}


class _FakeGraph:
    def __init__(self, neighbours):
        self._n = neighbours

    async def neighbors(self, session_id, entities, hops=1):
        return self._n


class _FakeMarkdown:
    def __init__(self, notes):
        self._notes = notes

    async def read(self, session_id):
        return self._notes


@pytest.mark.asyncio
async def test_all_three_layers_contribute():
    hybrid = HybridRetriever(
        _FakeGraph(["Ada", "Babbage"]), _FakeMarkdown("Q: hi\nA: hello"), WEIGHTS
    )
    hits = await hybrid.retrieve("ada", "s1", vector_hits=[("VEC chunk", 0.9)])
    assert {h.source for h in hits} == {"vector", "graph", "memory"}
    blob = " ".join(h.text for h in hits)
    assert "VEC chunk" in blob and "Ada" in blob and "hello" in blob


@pytest.mark.asyncio
async def test_empty_layers_are_omitted():
    hybrid = HybridRetriever(_FakeGraph([]), _FakeMarkdown(""), WEIGHTS)
    hits = await hybrid.retrieve("q", "s1", vector_hits=[("only vector", 0.5)])
    assert {h.source for h in hits} == {"vector"}


@pytest.mark.asyncio
async def test_no_stores_returns_only_vector():
    hybrid = HybridRetriever(None, None, WEIGHTS)
    hits = await hybrid.retrieve("q", "s1", vector_hits=[("v", 1.0)])
    assert [h.source for h in hits] == ["vector"]


@pytest.mark.asyncio
async def test_weighting_ranks_vector_above_graph():
    # vector 1.0*0.6=0.6 > graph 1.0*0.25 > markdown 1.0*0.15 → deterministic order
    hybrid = HybridRetriever(_FakeGraph(["X"]), _FakeMarkdown("notes"), WEIGHTS)
    hits = await hybrid.retrieve("q", "s1", vector_hits=[("v", 0.8)])
    assert [h.source for h in hits] == ["vector", "graph", "memory"]


@pytest.mark.asyncio
async def test_top_k_caps_results():
    hybrid = HybridRetriever(_FakeGraph(["X"]), _FakeMarkdown("n"), WEIGHTS)
    hits = await hybrid.retrieve(
        "q", "s1", vector_hits=[("a", 0.9), ("b", 0.8), ("c", 0.7)], top_k=2
    )
    assert len(hits) == 2


def test_query_terms_extracts_candidate_seeds():
    terms = query_terms("Who is Ada Lovelace?")
    assert "Ada" in terms and "Lovelace" in terms
    assert "is" not in terms  # tokens of length <= 2 are dropped
