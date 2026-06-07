"""Retrieval module for the RAG system.

All external calls go through injected client instances; no module-level singletons.

Phase 6: ``retrieve_vector`` and ``retrieve_web`` are node-callable helpers that take the clients
explicitly (the LangGraph vector/web nodes read them from ``GraphState`` and pass them in). The
vector helper applies the >=0.4 cosine relevance gate (parity with the old ``check_docs_relevant``
+ ``decide_combined_route`` flow, docs/09 §2.1): below threshold it reports ``relevant=False`` and
returns no context so synthesis falls back to web/general knowledge. The legacy
``retrieve_context`` (single combined label) is preserved for existing callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from database.db_manager import PineconeClient
    from integrations.duckduckgo.client import DuckDuckGoClient
    from integrations.huggingface.client import HuggingFaceClient

logger = structlog.get_logger(__name__)

# Top cosine score a session's best chunk must clear to count as relevant (parity: app.RAG_THRESHOLD).
RAG_THRESHOLD = 0.4
# Same probe depth the old check_docs_relevant used; keeps the gate decision identical.
RELEVANCE_TOP_K = 5


async def retrieve_vector(
    query: str,
    session_id: str,
    pinecone: PineconeClient,
    embedder: HuggingFaceClient,
    *,
    top_k: int = RELEVANCE_TOP_K,
    threshold: float = RAG_THRESHOLD,
) -> tuple[list[str], bool]:
    """Embed + search Pinecone (session-scoped), then apply the relevance gate.

    Returns ``(chunks, docs_relevant)``. When the top score is below ``threshold`` (or there are no
    matches, or the search fails) returns ``([], False)`` — the weak context is dropped so synthesis
    answers from web/general knowledge instead. Never raises; retrieval failure degrades to "no
    relevant docs" exactly like the old defensive ``check_docs_relevant``.
    """
    try:
        query_embedding = await embedder.embed_single(query)
        results = await pinecone.search_vectors(
            query_vector=query_embedding, top_k=top_k, session_id=session_id
        )
    except Exception:
        logger.error("vector_retrieval_failed", exc_info=True)
        return [], False

    if not results:
        logger.info("vector_retrieval_empty", session_id=session_id)
        return [], False

    top_score = results[0]["score"]
    docs_relevant = top_score >= threshold
    logger.info(
        "vector_relevance_check",
        top_score=round(top_score, 3),
        docs_relevant=docs_relevant,
        session_id=session_id,
    )
    if not docs_relevant:
        return [], False
    return [r["text"] for r in results], True


async def retrieve_web(
    query: str,
    web: DuckDuckGoClient,
    *,
    max_results: int = 5,
) -> list[str]:
    """Search the web and return snippet strings. Never raises (the client logs + returns [])."""
    try:
        web_results = await web.search_web(query, max_results=max_results)
    except Exception:
        logger.error("web_retrieval_failed", exc_info=True)
        return []
    snippets = [r["snippet"] for r in web_results]
    logger.info("web_retrieval_complete", snippets=len(snippets))
    return snippets


async def retrieve_context(
    query: str,
    decision: str,
    session_id: str,
    web_search_allowed: bool,
    pinecone: PineconeClient,
    embedder: HuggingFaceClient,
    web: DuckDuckGoClient,
) -> list[str]:
    """Retrieve context based on a (possibly combined) router decision. Legacy linear-path helper."""
    context: list[str] = []

    if decision == "DIRECT":
        logger.info("retrieval_skip", reason="DIRECT route needs no context")
        return context

    elif decision == "RAG":
        logger.info("retrieval_rag", action="searching Pinecone")
        query_embedding = await embedder.embed_single(query)
        logger.debug("query_embedding", dims=len(query_embedding))

        results = await pinecone.search_vectors(
            query_vector=query_embedding, top_k=5, session_id=session_id
        )
        context = [result["text"] for result in results]
        logger.info("retrieval_rag_complete", chunks=len(context), session_id=session_id)

    elif decision == "WEB":
        if web_search_allowed:
            logger.info("retrieval_web", action="searching DuckDuckGo")
            web_results = await web.search_web(query, max_results=5)
            context = [result["snippet"] for result in web_results]
            logger.info("retrieval_web_complete", snippets=len(context))
        else:
            logger.info("retrieval_web_skipped", reason="web_search_allowed=False")

    return context


def format_context(context: list[str], max_tokens: int = 4000) -> str:
    """Format context for the generation prompt (token-aware truncation)."""
    if not context:
        return "No relevant context found."

    formatted = "\n\n".join([f"CONTEXT {i + 1}:\n{chunk}" for i, chunk in enumerate(context)])

    max_chars = max_tokens * 3
    if len(formatted) > max_chars:
        formatted = formatted[:max_chars] + "\n\n[Context truncated...]"

    return formatted
