"""Compile the Phase 6 agentic chat ``StateGraph`` (docs/09 §2).

Topology::

    START → supervisor → (conditional) ─┬─ RAG    → vector  ─┐
                                        ├─ WEB    → web     ─┤→ synthesis → END
                                        ├─ BOTH   → web + vector (parallel)
                                        └─ DIRECT → synthesis (skip retrieval)

``vector`` and ``web`` write **disjoint** state keys (``vector_result`` vs ``web_result``), so the
fan-in into ``synthesis`` needs **no reducers** — neither parallel write can clobber the other. The
graph is **pure**: nodes read the per-request provider/clients/history from the invocation state, so
the compiled object is stateless and safe to build once and share on ``app.state`` (BE-1 owns the
graph core; BE-2 wires the lifespan + SSE endpoint around it).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.nodes import (
    route_after_supervisor,
    supervisor_node,
    synthesis_node,
    vector_node,
    web_node,
)
from agents.state import GraphState


def build_graph() -> CompiledStateGraph:
    """Build + compile the agentic chat graph. Pure: no globals, no ``app.state``."""
    g = StateGraph(GraphState)

    g.add_node("supervisor", supervisor_node)
    g.add_node("vector", vector_node)
    g.add_node("web", web_node)
    g.add_node("synthesis", synthesis_node)

    g.add_edge(START, "supervisor")
    # route_after_supervisor returns a list of node names: RAG→[vector], WEB→[web],
    # BOTH→[web, vector] (parallel), DIRECT→[synthesis] (skip retrieval).
    g.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        ["vector", "web", "synthesis"],
    )
    g.add_edge("vector", "synthesis")  # fan-in
    g.add_edge("web", "synthesis")  # fan-in
    g.add_edge("synthesis", END)

    return g.compile()
