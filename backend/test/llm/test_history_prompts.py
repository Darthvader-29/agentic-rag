"""Conversation-history threading (H-B1 / R01).

Before this fix, history was loaded into ``GraphState`` but never reached the LLM: ``_rewrite_query``
ignored it and the provider contract had no ``history`` parameter, so multi-turn chat was effectively
single-turn. These tests pin the fix:

* history lands in the VARIABLE user suffix of BOTH the routing and the generation prompt — for every
  route, including DIRECT (the common follow-up-chat route, which ignores ``context`` and which a
  "fold history into context" shortcut would have missed);
* history never leaks into the stable, cacheable system prefix (prefix-caching invariant);
* the no-history call is byte-identical to the pre-fix output (so existing cache structure holds);
* the ``history`` keyword threads end-to-end through ``BaseLLMProvider.route/generate/stream`` into
  the user message handed to the SDK.
"""

from collections.abc import AsyncIterator

import pytest

from llm._prompts import (
    REWRITE_SYSTEM,
    ROUTING_SYSTEM,
    _format_history,
    generation_system,
    generation_user,
    rewrite_user,
    routing_user,
)
from llm.base import BaseLLMProvider

_HISTORY = [
    {"role": "user", "content": "Tell me about the Apollo and Gemini programs."},
    {"role": "assistant", "content": "Apollo landed on the Moon; Gemini flew earlier."},
]
_FOLLOWUP = "what about the second one?"
_APOLLO = "Apollo landed on the Moon"


# ── _format_history ───────────────────────────────────────────────────────────


def test_format_history_empty_is_blank():
    assert _format_history(None) == ""
    assert _format_history([]) == ""
    # Turns with no usable content collapse to "" (no stray header).
    assert _format_history([{"role": "user", "content": "   "}]) == ""


def test_format_history_renders_speakers_in_order():
    out = _format_history(_HISTORY)
    assert out.startswith("CONVERSATION SO FAR")
    assert "User: Tell me about the Apollo and Gemini programs." in out
    assert "Assistant: Apollo landed on the Moon; Gemini flew earlier." in out
    assert out.index("User:") < out.index("Assistant:")  # oldest-first


def test_format_history_caps_turns():
    many = [{"role": "user", "content": f"turn-{i}"} for i in range(50)]
    out = _format_history(many, max_turns=3)
    assert "turn-49" in out and "turn-47" in out
    assert "turn-46" not in out  # only the last 3 survive


def test_format_history_clips_long_turns():
    out = _format_history([{"role": "user", "content": "x" * 5000}], max_chars=100)
    assert "…" in out
    assert len(out) < 300  # the 5000-char turn was clipped, not embedded whole


# ── routing prompt ────────────────────────────────────────────────────────────


def test_routing_user_without_history_is_unchanged():
    """Empty history → byte-identical to the pre-H-B1 output (prefix-cache stability)."""
    expected = (
        'Query: "hi"\n'
        "Documents available: NO\n"
        "Web search: ALLOWED\n\n"
        "Respond with ONLY one word: RAG, WEB, or DIRECT."
    )
    assert routing_user("hi", False, True) == expected
    assert routing_user("hi", False, True, None) == expected
    assert routing_user("hi", False, True, []) == expected


def test_routing_user_includes_history_in_variable_suffix():
    out = routing_user(_FOLLOWUP, False, True, _HISTORY)
    assert _APOLLO in out
    assert _FOLLOWUP in out
    # The query still appears after the conversation block.
    assert out.index(_APOLLO) < out.index(_FOLLOWUP)
    # The stable cached rubric must NOT carry per-request history.
    assert _APOLLO not in ROUTING_SYSTEM


# ── rewrite prompt ────────────────────────────────────────────────────────────


def test_rewrite_user_includes_history_and_query():
    """The rewrite's variable suffix carries the conversation + the latest message (in that order);
    the stable REWRITE_SYSTEM prefix never does (prefix-cache invariant)."""
    out = rewrite_user(_FOLLOWUP, _HISTORY)
    assert _APOLLO in out
    assert _FOLLOWUP in out
    assert out.index(_APOLLO) < out.index(_FOLLOWUP)  # history precedes the latest message
    assert _APOLLO not in REWRITE_SYSTEM


def test_rewrite_user_without_history_is_just_the_query():
    """No history → the suffix is only the latest message, with no conversation header."""
    out = rewrite_user(_FOLLOWUP, None)
    assert _FOLLOWUP in out
    assert "CONVERSATION SO FAR" not in out


# ── generation prompt ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("decision", ["RAG", "WEB", "DIRECT"])
def test_generation_user_without_history_unchanged(decision):
    assert generation_user(decision, "Q?", "CTX") == generation_user(decision, "Q?", "CTX", None)
    assert generation_user(decision, "Q?", "CTX") == generation_user(decision, "Q?", "CTX", [])
    assert "CONVERSATION SO FAR" not in generation_user(decision, "Q?", "CTX")


@pytest.mark.parametrize("decision", ["RAG", "WEB", "DIRECT"])
def test_generation_user_includes_history(decision):
    """Every route — incl. DIRECT, which ignores ``context`` — surfaces prior turns."""
    out = generation_user(decision, _FOLLOWUP, "CTX", _HISTORY)
    assert _APOLLO in out
    assert _FOLLOWUP in out


@pytest.mark.parametrize("decision", ["RAG", "WEB", "DIRECT"])
def test_history_absent_from_cached_system_prefix(decision):
    """Caching invariant: history is variable data, never in the stable system prefix."""
    assert _APOLLO not in generation_system(decision)


# ── end-to-end threading through BaseLLMProvider ──────────────────────────────


class _CapturingProvider(BaseLLMProvider):
    """Minimal BaseLLMProvider that records the (system, user) passed to the SDK hooks."""

    _DEFAULT_MODEL = "test-model"

    def _build_client(self, api_key: str) -> None:
        self.calls: list[tuple[str, str]] = []

    async def _call(self, model, system, user, *, max_tokens=None):
        self.calls.append((system, user))
        return "DIRECT"

    async def _stream_call(self, model, system, user, *, max_tokens=None) -> AsyncIterator[str]:
        self.calls.append((system, user))
        yield "ok"


@pytest.mark.asyncio
async def test_history_threads_through_route():
    p = _CapturingProvider(api_key="x")
    await p.route(_FOLLOWUP, has_documents=False, web_allowed=True, history=_HISTORY)
    system, user = p.calls[-1]
    assert system == ROUTING_SYSTEM  # stable prefix untouched
    assert _APOLLO in user
    assert _APOLLO not in system


@pytest.mark.asyncio
async def test_history_threads_through_generate():
    p = _CapturingProvider(api_key="x")
    await p.generate(_FOLLOWUP, "", "DIRECT", history=_HISTORY)
    _, user = p.calls[-1]
    assert _APOLLO in user


@pytest.mark.asyncio
async def test_history_threads_through_stream():
    p = _CapturingProvider(api_key="x")
    _ = [c async for c in p.stream(_FOLLOWUP, "", "DIRECT", history=_HISTORY)]
    _, user = p.calls[-1]
    assert _APOLLO in user


@pytest.mark.asyncio
async def test_history_threads_through_rewrite_query():
    """rewrite_query feeds the stable REWRITE_SYSTEM prefix + a history-bearing user suffix to the
    SDK, and returns the model's standalone query."""
    p = _CapturingProvider(api_key="x")
    out = await p.rewrite_query(_FOLLOWUP, history=_HISTORY)
    system, user = p.calls[-1]
    assert system == REWRITE_SYSTEM  # stable rubric untouched
    assert _APOLLO in user  # history reached the variable suffix
    assert _APOLLO not in system
    assert _FOLLOWUP in user
    assert out == "DIRECT"  # _CapturingProvider._call returns "DIRECT" → stripped, non-empty


@pytest.mark.asyncio
async def test_rewrite_query_without_history_skips_sdk_call():
    """No history → nothing to resolve: rewrite_query returns the query unchanged and never calls
    the SDK (keeps single-turn requests free and identical to the pre-rewrite path)."""
    p = _CapturingProvider(api_key="x")
    out = await p.rewrite_query("standalone question?", history=None)
    assert out == "standalone question?"
    assert p.calls == []
