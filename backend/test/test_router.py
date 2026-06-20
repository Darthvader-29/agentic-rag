"""Tests for components/router.py — Phase 4: injected provider, no Gemini globals.

The single-label ``route_query`` helper was removed in R29 (the LangGraph supervisor node now calls
``provider.route`` directly — agents/nodes.py); routing-intent coverage lives in the graph tests
(test/agents/test_graph.py) and the LLM provider suites. What remains here is the guard that no
process-global Gemini config crept back into either module.
"""

import pytest


@pytest.mark.asyncio
async def test_no_gemini_globals():
    """Ensure no process-global Gemini config remains in router or generation."""
    import components.generation as gen
    import components.router as rtr

    assert not hasattr(rtr, "gemini_model"), "gemini_model global found in router"
    assert not hasattr(gen, "gemini_model"), "gemini_model global found in generation"
