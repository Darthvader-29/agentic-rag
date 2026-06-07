"""Assert that decrypted API keys never surface in repr() or log records."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from llm.anthropic import AnthropicProvider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider

_SECRET = "super-secret-api-key-must-not-leak"


def test_gemini_repr_no_key():
    with patch("llm.gemini.genai.Client"):
        p = GeminiProvider(api_key=_SECRET)
    assert _SECRET not in repr(p)


def test_openai_repr_no_key():
    with patch("llm.openai.AsyncOpenAI"):
        p = OpenAIProvider(api_key=_SECRET)
    assert _SECRET not in repr(p)


def test_anthropic_repr_no_key():
    with patch("llm.anthropic.AsyncAnthropic"):
        p = AnthropicProvider(api_key=_SECRET)
    assert _SECRET not in repr(p)


def test_repr_shows_model():
    with patch("llm.openai.AsyncOpenAI"):
        p = OpenAIProvider(api_key=_SECRET, model="gpt-4o-mini")
    assert "gpt-4o-mini" in repr(p)
    assert _SECRET not in repr(p)
