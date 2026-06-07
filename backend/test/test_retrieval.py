from unittest.mock import AsyncMock

import pytest

from components.retrieval import retrieve_context


def _fake_clients(
    embed_result: list | None = None,
    search_result: list | None = None,
    web_result: list | None = None,
):
    """Return (pinecone, embedder, web) AsyncMocks with optional pre-set return values."""
    pinecone = AsyncMock()
    embedder = AsyncMock()
    web = AsyncMock()
    if embed_result is not None:
        embedder.embed_single.return_value = embed_result
    if search_result is not None:
        pinecone.search_vectors.return_value = search_result
    if web_result is not None:
        web.search_web.return_value = web_result
    return pinecone, embedder, web


@pytest.mark.asyncio
async def test_retrieve_context_rag():
    pinecone, embedder, web = _fake_clients(
        embed_result=[0.1] * 384, search_result=[{"text": "RAG context"}]
    )

    context = await retrieve_context(
        "test query", "RAG", "session123", False, pinecone, embedder, web
    )

    assert context == ["RAG context"]
    embedder.embed_single.assert_called_once_with("test query")
    pinecone.search_vectors.assert_called_once_with(
        query_vector=[0.1] * 384, top_k=5, session_id="session123"
    )


@pytest.mark.asyncio
async def test_retrieve_context_web():
    pinecone, embedder, web = _fake_clients(web_result=[{"snippet": "WEB context"}])

    context = await retrieve_context(
        "test query", "WEB", "session123", True, pinecone, embedder, web
    )

    assert context == ["WEB context"]
    web.search_web.assert_called_once_with("test query", max_results=5)


@pytest.mark.asyncio
async def test_retrieve_context_direct():
    pinecone, embedder, web = _fake_clients()

    context = await retrieve_context(
        "test query", "DIRECT", "session123", False, pinecone, embedder, web
    )

    assert context == []


@pytest.mark.asyncio
async def test_retrieve_context_web_disabled():
    pinecone, embedder, web = _fake_clients()

    context = await retrieve_context(
        "test query", "WEB", "session123", False, pinecone, embedder, web
    )

    assert context == []
    web.search_web.assert_not_called()
