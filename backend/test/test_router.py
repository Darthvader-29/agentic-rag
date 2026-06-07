"""Tests for components/router.py — Phase 4: injected provider, no Gemini globals."""

import pytest

from components.router import route_query
from exceptions import LLMAuthError


class _FakeProvider:
    """Minimal LLMProvider stub for router tests."""

    def __init__(self, decision: str = "DIRECT", raises: Exception | None = None) -> None:
        self._decision = decision
        self._raises = raises

    async def route(self, query, *, has_documents, web_allowed):
        if self._raises:
            raise self._raises
        return self._decision

    async def generate(self, query, context, decision):
        return "answer"

    def stream(self, query, context, decision):
        return iter([])


@pytest.mark.asyncio
async def test_route_query_rag():
    decision = await route_query(
        _FakeProvider("RAG"),
        "Summarize the uploaded PDF for me.",
        has_documents=True,
        web_search_allowed=False,
    )
    assert decision == "RAG"


@pytest.mark.asyncio
async def test_route_query_direct():
    decision = await route_query(
        _FakeProvider("DIRECT"),
        "Write a python script to scrape google.",
        has_documents=False,
        web_search_allowed=False,
    )
    assert decision == "DIRECT"


@pytest.mark.asyncio
async def test_route_query_web():
    decision = await route_query(
        _FakeProvider("WEB"),
        "Who is the president of France in 2025?",
        has_documents=False,
        web_search_allowed=True,
    )
    assert decision == "WEB"


@pytest.mark.asyncio
async def test_route_query_propagates_llm_error():
    with pytest.raises(LLMAuthError):
        await route_query(
            _FakeProvider(raises=LLMAuthError()),
            "query",
            has_documents=False,
            web_search_allowed=False,
        )


@pytest.mark.asyncio
async def test_no_gemini_globals():
    """Ensure no process-global Gemini config remains in router or generation."""
    import components.generation as gen
    import components.router as rtr

    assert not hasattr(rtr, "gemini_model"), "gemini_model global found in router"
    assert not hasattr(gen, "gemini_model"), "gemini_model global found in generation"
