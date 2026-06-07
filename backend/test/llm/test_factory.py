"""Tests for llm/factory.py — build_provider dispatch."""

import pytest

from exceptions import LLMResponseError
from llm.anthropic import AnthropicProvider
from llm.factory import build_provider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider


@pytest.mark.parametrize(
    "name, cls",
    [
        ("gemini", GeminiProvider),
        ("openai", OpenAIProvider),
        ("anthropic", AnthropicProvider),
    ],
)
def test_dispatch(name, cls, monkeypatch):
    # Patch the SDK constructors so no real clients are built
    monkeypatch.setattr("llm.gemini.genai.Client", lambda api_key=None, **kw: None)  # noqa: ARG005
    monkeypatch.setattr("llm.openai.AsyncOpenAI", lambda **kw: None)
    monkeypatch.setattr("llm.anthropic.AsyncAnthropic", lambda **kw: None)
    provider = build_provider(name, "k", model="m")
    assert isinstance(provider, cls)


def test_dispatch_case_insensitive(monkeypatch):
    monkeypatch.setattr("llm.gemini.genai.Client", lambda api_key=None, **kw: None)  # noqa: ARG005
    provider = build_provider("GEMINI", "k")
    assert isinstance(provider, GeminiProvider)


def test_unknown_provider():
    with pytest.raises(LLMResponseError):
        build_provider("bedrock", "k")


def test_model_override(monkeypatch):
    """A single `model` arg sets BOTH route and synth slots (backward compat)."""
    monkeypatch.setattr("llm.openai.AsyncOpenAI", lambda **kw: None)
    provider = build_provider("openai", "k", model="gpt-4o")
    assert provider._route_model == "gpt-4o"
    assert provider._synth_model == "gpt-4o"


def test_per_node_tiering(monkeypatch):
    """route_model / synth_model set the cheap and strong slots independently."""
    monkeypatch.setattr("llm.openai.AsyncOpenAI", lambda **kw: None)
    provider = build_provider("openai", "k", route_model="gpt-4o-mini", synth_model="gpt-4o")
    assert provider._route_model == "gpt-4o-mini"
    assert provider._synth_model == "gpt-4o"


def test_tier_models_take_precedence_over_model(monkeypatch):
    """Explicit route_model/synth_model win over the single `model` fallback."""
    monkeypatch.setattr("llm.gemini.genai.Client", lambda api_key=None, **kw: None)  # noqa: ARG005
    provider = build_provider(
        "gemini", "k", model="ignored", route_model="cheap", synth_model="strong"
    )
    assert provider._route_model == "cheap"
    assert provider._synth_model == "strong"
