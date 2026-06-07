"""build_provider: single construction site for all LLM adapters."""

from __future__ import annotations

from exceptions import LLMResponseError
from llm.anthropic import AnthropicProvider
from llm.base import LLMProvider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider

_REGISTRY: dict[str, type] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


def build_provider(
    provider_name: str,
    api_key: str,
    model: str | None = None,
    *,
    route_model: str | None = None,
    synth_model: str | None = None,
) -> LLMProvider:
    """Instantiate the named provider adapter with the given API key.

    Per-node model tiering (Phase 6): one provider instance can serve two models — a cheap
    ``route_model`` for the supervisor classification and a strong ``synth_model`` for
    generation/streaming. Resolution, per slot, is ``route_model`` / ``synth_model`` first,
    else the single ``model`` arg, else the adapter's own default (``None`` → adapter default).

    Backward compatible: passing only ``model`` (or nothing) sets both slots to that value, so
    existing single-model callers behave exactly as before.
    """
    cls = _REGISTRY.get(provider_name.lower())
    if cls is None:
        raise LLMResponseError(f"unknown LLM provider: {provider_name!r}")
    resolved_route = route_model or model
    resolved_synth = synth_model or model
    return cls(api_key=api_key, route_model=resolved_route, synth_model=resolved_synth)
