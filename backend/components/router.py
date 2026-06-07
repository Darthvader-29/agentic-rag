"""Router module: query classification + the flat agentic route decision.

Phase 4: the Gemini process-global and GoogleAPIError ladder are removed. Error mapping lives in
the provider adapter; neutral LLM errors bubble to app_exception_handler in exceptions.py.

Phase 6: ``decide_agentic_route`` folds the old ``app.py::decide_combined_route`` *intent* step into
a single flat label (``RAG`` / ``WEB`` / ``BOTH`` / ``DIRECT``) the LangGraph supervisor emits. The
post-retrieval relevance gate stays in the vector node (docs/09 §2.1); the supervisor routes on
intent only, preferring ``BOTH`` when documents exist and web is allowed so the gate can fall back
to web. ``route_query`` (the single-label classifier) is preserved for existing callers.
"""

from __future__ import annotations

import structlog

from agents.state import Route as AgentRoute
from llm.base import LLMProvider, Route

logger = structlog.get_logger(__name__)


async def route_query(
    provider: LLMProvider,
    query: str,
    *,
    has_documents: bool,
    web_search_allowed: bool,
) -> Route:
    """Route query to RAG, WEB, or DIRECT using the injected provider."""
    decision = await provider.route(
        query, has_documents=has_documents, web_allowed=web_search_allowed
    )
    logger.info(
        "router_decision",
        query_preview=query[:50],
        decision=decision,
        has_documents=has_documents,
        web_search_allowed=web_search_allowed,
    )
    return decision


def decide_agentic_route(
    base_route: str,
    *,
    has_documents: bool,
    web_allowed: bool,
) -> AgentRoute:
    """Map a provider's intent label (RAG/WEB/DIRECT) + flags → flat agentic route.

    Parity intent with the old ``decide_combined_route``, expressed acyclically:

    - The supervisor sees only ``has_documents`` (not Pinecone scores); the vector node applies the
      >=0.4 cosine gate later.
    - When documents exist *and* web is allowed, a WEB or DIRECT intent fans out to ``BOTH`` so the
      vector node's relevance gate can fall back to web if the docs turn out weak (docs/09 §2.1).
    - A normalized-but-unrecognized label degrades to the safe default: prefer the user's documents
      when present (``RAG``/``BOTH``), else web, else ``DIRECT`` — never raising.
    """
    base = (base_route or "").strip().upper()
    if base not in {"RAG", "WEB", "DIRECT"}:
        # Defensive default mirrors the original flow: lean on documents when uncertain.
        base = "RAG" if has_documents else ("WEB" if web_allowed else "DIRECT")

    if has_documents:
        if base == "RAG":
            # Document-only intent; corroborate with web when it's available.
            return "BOTH" if web_allowed else "RAG"
        if base == "WEB":
            return "BOTH" if web_allowed else "RAG"
        # DIRECT intent but documents exist + web available → cast a wide net.
        return "BOTH" if web_allowed else "DIRECT"

    # No documents: web intent (or fallback) needs web; otherwise answer directly.
    if base == "WEB":
        return "WEB" if web_allowed else "DIRECT"
    if base == "RAG":
        # Claimed RAG with no docs → there is nothing to retrieve; use web if allowed.
        return "WEB" if web_allowed else "DIRECT"
    return "DIRECT"
