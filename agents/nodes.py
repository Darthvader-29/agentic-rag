"""LangGraph node functions for the Phase 6 agentic chat graph.

Four nodes (docs/09 §2): **supervisor** (LLM, cheap) → route + context-resolved query; **vector**
(no LLM) → Pinecone top-k + >=0.4 relevance gate; **web** (no LLM) → DuckDuckGo; **synthesis** (LLM,
strong, streamed) → Markdown prose + optional component JSON. Plus ``route_after_supervisor``, the
conditional-edge fn.

Every node takes only ``state`` (a ``GraphState``) and reads the per-request provider/clients from
it — no module globals, no ``app.state`` (Phase 4 invariant). Each returns a *partial* state update;
parallel branches (the ``BOTH`` route) write **disjoint** keys (``vector_result`` vs ``web_result``)
so a fan-out can't clobber a sibling write.

Streaming (docs/09 §5): ``synthesis_node`` is async and writer-aware. When BE-2's SSE endpoint runs
``graph.astream(state, stream_mode=["updates","custom"], config={"configurable": {"stream": True}})``
the node streams ``provider.stream`` deltas through a ``ComponentStreamSplitter``, emitting
``{"kind":"token","text":...}`` / ``{"kind":"component","data":...}`` to the injected ``writer``.
Without that flag (e.g. ``graph.ainvoke`` in the parity test) it falls back to a single
``provider.generate`` + ``parse_components``. Either way it returns ``{"answer", "components"}``.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from agents.schemas import ComponentStreamSplitter, parse_components
from agents.state import GraphState, Route, Turn
from components.generation import build_synthesis_query
from components.retrieval import format_context, retrieve_vector, retrieve_web
from components.router import decide_agentic_route

logger = structlog.get_logger(__name__)


# ── supervisor ────────────────────────────────────────────────────────────────


async def supervisor_node(state: GraphState) -> dict[str, Any]:
    """Classify the query (one cheap LLM call) → flat ``route`` + context-resolved ``rewritten_query``.

    Ports the intent half of the old ``decide_combined_route``: the provider returns an intent label
    (RAG/WEB/DIRECT) which ``decide_agentic_route`` maps to ``RAG``/``WEB``/``BOTH``/``DIRECT`` using
    ``has_documents`` + ``web_search_allowed`` (the >=0.4 score gate stays in the vector node,
    docs/09 §2.1). Query rewriting is folded in here (Decision 4) with no extra LLM call. On any
    malformed/raising provider response we fall back to a safe default route and never raise —
    mirroring today's defensive routing.
    """
    provider = state["provider"]
    query = state["query"]
    has_documents = state["has_documents"]
    web_allowed = state["web_search_allowed"]

    try:
        base_route = await provider.route(
            query, has_documents=has_documents, web_allowed=web_allowed
        )
    except Exception:
        logger.error("supervisor_route_failed", exc_info=True)
        base_route = ""  # decide_agentic_route maps "" to the safe default below

    route = decide_agentic_route(
        str(base_route), has_documents=has_documents, web_allowed=web_allowed
    )
    rewritten_query = _rewrite_query(query, state.get("history") or [])

    logger.info(
        "supervisor_decision",
        query_preview=query[:50],
        base_route=str(base_route),
        route=route,
        has_documents=has_documents,
        web_allowed=web_allowed,
    )
    return {"route": route, "rewritten_query": rewritten_query}


def _rewrite_query(query: str, history: list[Turn]) -> str:
    """Context-resolve the query against recent turns.

    docs/09 Decision 4 folds rewriting into the supervisor's single call. Until a provider-native
    rewrite is wired into ``LLMProvider.route``, this is a deterministic, zero-LLM-cost resolver: the
    raw query already carries its own context for first turns, and the verbatim last-N history (also
    fed to synthesis) covers follow-ups. Always returns a non-empty string.
    """
    q = (query or "").strip()
    return q or (query or "")


# ── vector ────────────────────────────────────────────────────────────────────


async def vector_node(state: GraphState) -> dict[str, Any]:
    """Pinecone top-k (session-scoped) + the >=0.4 cosine relevance gate.

    Writes the disjoint ``vector_result`` plus the ``context`` fed to synthesis and ``docs_relevant``.
    Below threshold (or on empty/failed search) it reports ``docs_relevant=False`` and drops the weak
    context so synthesis falls back to web (``BOTH``) or general knowledge (``RAG``).
    """
    query = state.get("rewritten_query") or state["query"]
    chunks, docs_relevant = await retrieve_vector(
        query, state["session_id"], state["pinecone"], state["embedder"]
    )
    context = format_context(chunks) if chunks else ""
    return {
        "vector_result": context,
        "context": context,
        "docs_relevant": docs_relevant,
    }


# ── web ───────────────────────────────────────────────────────────────────────


async def web_node(state: GraphState) -> dict[str, Any]:
    """DuckDuckGo search → the disjoint ``web_result`` (a formatted snippet block, or "")."""
    query = state.get("rewritten_query") or state["query"]
    snippets = await retrieve_web(query, state["web"])
    web_result = format_context(snippets) if snippets else ""
    return {"web_result": web_result}


# ── synthesis ─────────────────────────────────────────────────────────────────


def _resolve_decision(state: GraphState, doc_context: str, web_result: str) -> Route:
    """Pick the provider's RAG/WEB/DIRECT generation prompt from the route + what context exists.

    ``BOTH`` resolves by availability: prefer the document (RAG) prompt when relevant docs were
    retrieved, else the WEB prompt when there are web snippets, else DIRECT. This keeps the prompt
    grounded in whatever the retrieval nodes actually produced.
    """
    route = state.get("route", "DIRECT")
    if route == "RAG":
        return "RAG" if doc_context else "DIRECT"
    if route == "WEB":
        return "WEB" if web_result else "DIRECT"
    if route == "BOTH":
        if doc_context:
            return "RAG"
        if web_result:
            return "WEB"
        return "DIRECT"
    return "DIRECT"


def _merge_context(doc_context: str, web_result: str) -> str:
    """Concatenate whatever context the retrieval nodes produced for the synthesis prompt."""
    parts = [p for p in (doc_context, web_result) if p]
    return "\n\n".join(parts)


def _should_stream(config: RunnableConfig | None) -> bool:
    """BE-2's SSE endpoint signals token streaming via config.configurable.stream=True.

    langgraph injects the same ``stream_writer`` into a node under both ``ainvoke`` and ``astream``,
    so the writer object alone can't tell us whether anyone is listening. The explicit config flag is
    the reliable signal: absent (the parity ``ainvoke`` path) → non-streaming ``generate``.
    """
    if not config:
        return False
    return bool(config.get("configurable", {}).get("stream"))


async def synthesis_node(
    state: GraphState,
    writer: StreamWriter,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Rich-Markdown synthesis. Streams when asked, else one-shot; returns ``{answer, components}``.

    Streaming path (``config.configurable.stream``): iterate ``provider.stream``, feed each delta to a
    ``ComponentStreamSplitter``; emit ``{"kind":"token","text":...}`` for prose and
    ``{"kind":"component","data":...}`` for each completed/validated component to ``writer``; flush at
    the end. Prose is accumulated into ``answer`` and components into the list so the returned state
    matches the streamed output. Non-streaming path: ``provider.generate`` → ``parse_components``.
    """
    provider = state["provider"]
    doc_context = state.get("context", "") or ""
    web_result = state.get("web_result", "") or ""
    merged = _merge_context(doc_context, web_result)
    decision = _resolve_decision(state, doc_context, web_result)
    synth_query = build_synthesis_query(state["query"])

    if _should_stream(config):
        splitter = ComponentStreamSplitter()
        prose_parts: list[str] = []
        components: list[dict] = []

        def _emit(events: list[tuple[str, Any]]) -> None:
            for kind, value in events:
                if kind == "token":
                    prose_parts.append(value)
                    writer({"kind": "token", "text": value})
                else:  # "component"
                    components.append(value)
                    writer({"kind": "component", "data": value})

        async for delta in provider.stream(synth_query, merged, decision):
            _emit(splitter.feed(delta))
        _emit(splitter.flush())

        answer = "".join(prose_parts).strip()
        await _persist_markdown(state, answer)
        logger.info("synthesis_streamed", decision=decision, components=len(components))
        return {"answer": answer, "components": components}

    raw = await provider.generate(synth_query, merged, decision)
    prose, components = parse_components(raw)
    await _persist_markdown(state, prose)
    logger.info("synthesis_generated", decision=decision, components=len(components))
    return {"answer": prose, "components": components}


async def _persist_markdown(state: GraphState, answer: str) -> None:
    """Phase 7: append this turn's Q/A to the per-session markdown memory, if a store is wired in.

    Best-effort and non-fatal — a memory write must never break answer delivery. The store opens
    its own fresh session, which is safe mid-stream when the request session is already closing.
    """
    memory = state.get("markdown_memory")
    if memory is None or not answer:
        return
    try:
        await memory.append(state["session_id"], f"Q: {state['query']}\nA: {answer}")
    except Exception:
        logger.error("markdown_memory_append_failed", exc_info=True)


# ── conditional edge ──────────────────────────────────────────────────────────


def route_after_supervisor(state: GraphState) -> list[str]:
    """Map the supervisor's flat route to the next node(s).

    RAG→[vector], WEB→[web], BOTH→[web, vector] (parallel fan-out, disjoint keys), DIRECT→[synthesis]
    (skip retrieval entirely). An unexpected/missing route degrades to DIRECT.
    """
    route = state.get("route", "DIRECT")
    if route == "RAG":
        return ["vector"]
    if route == "WEB":
        return ["web"]
    if route == "BOTH":
        return ["web", "vector"]
    return ["synthesis"]
