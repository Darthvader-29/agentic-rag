"""Typed LangGraph state for the Phase 6 agentic chat graph.

The compiled graph is stateless and shared on ``app.state``; every per-request value — the
user's LLM provider (Phase 4), the Pinecone/embedder/web clients, and the last-N conversation
turns — travels in the invocation state below, never as a module global. Each node returns a
*partial* state update; parallel branches (the ``BOTH`` route) write **disjoint** keys
(``web_result`` vs ``vector_result``) so a fan-out can't clobber a sibling write.

See docs/09_Phase6_Agentic_Architecture.md Appendix A.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

# Flat routing enum produced by the supervisor. Distinct from ``llm.base.Route``
# ("RAG" | "WEB" | "DIRECT"): the graph adds ``BOTH`` for parallel vector+web retrieval.
Route = Literal["RAG", "WEB", "BOTH", "DIRECT"]


class Turn(TypedDict):
    """One verbatim conversation turn fed to the supervisor + synthesis nodes."""

    role: Literal["user", "assistant"]
    content: str


class GraphState(TypedDict):
    # --- inputs (set when the request builds the initial state) ---
    query: str
    session_id: str
    user_id: str
    provider: Any  # llm.base.LLMProvider — Any keeps this TypedDict import-light for LangGraph
    pinecone: Any  # PineconeClient handle (vector node)
    embedder: Any  # HuggingFaceClient (vector node)
    web: Any  # DuckDuckGoClient (web node)
    history: list[Turn]  # last-N turns, verbatim
    has_documents: bool
    web_search_allowed: bool
    # Phase 7 memory collaborators (set per-request by app.py; optional so parity tests can omit)
    markdown_memory: NotRequired[Any]  # MarkdownMemory store — synthesis appends each turn

    # --- produced by nodes (disjoint keys for safe parallel fan-out) ---
    rewritten_query: NotRequired[str]  # supervisor: context-resolved query
    route: NotRequired[Route]  # supervisor
    web_result: NotRequired[str]  # web node
    vector_result: NotRequired[str]  # vector node
    docs_relevant: NotRequired[bool]  # vector node: top cosine score >= 0.4
    context: NotRequired[str]  # context fed to synthesis
    answer: NotRequired[str]  # synthesis: Markdown prose (component blocks stripped)
    components: NotRequired[list[dict]]  # synthesis: validated component specs
