"""Tests for llm/dependencies.py — the Phase 6 freemium provider ladder.

Covers all three rungs: BYOK (tiered) → free-tier (single model) → exhausted (402).
The decrypted key must never appear in the provider's repr.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest

from auth.crypto import encrypt_key
from config import settings
from database.models import User, UserLLMKey
from exceptions import FreeTierExhaustedError
from llm.dependencies import get_llm_provider, resolve_provider


def _fake_user() -> User:
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


def _fake_key_row(provider: str = "openai", plaintext: str = "sk-test") -> UserLLMKey:
    row = MagicMock(spec=UserLLMKey)
    row.provider = provider
    row.ciphertext = encrypt_key(plaintext)
    return row


def _fresh_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


# ── Rung 1: BYOK → tiered provider ────────────────────────────────────────────


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
@patch("llm.openai.AsyncOpenAI")
async def test_byok_builds_tiered_provider(mock_openai_cls, mock_get_key):
    """BYOK path: route() uses the CHEAP model id, generate()/stream() use the STRONG model id."""
    user = _fake_user()
    mock_get_key.return_value = _fake_key_row(provider="openai")
    db = AsyncMock()

    provider = await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    assert provider.__class__.__name__ == "OpenAIProvider"
    # One instance, two models: cheap for routing, strong for synthesis.
    assert provider._route_model == settings.tier_route_model("openai")
    assert provider._synth_model == settings.tier_synth_model("openai")
    assert provider._route_model != provider._synth_model  # tiering actually differs
    assert "sk-test" not in repr(provider)


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
@patch("llm.gemini.genai.Client")
async def test_byok_uses_row_provider_field(mock_gemini_cls, mock_get_key):
    user = _fake_user()
    mock_get_key.return_value = _fake_key_row(provider="gemini", plaintext="sk-gemini")
    db = AsyncMock()

    provider = await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    assert provider.__class__.__name__ == "GeminiProvider"
    assert provider._route_model == settings.tier_route_model("gemini")
    assert provider._synth_model == settings.tier_synth_model("gemini")


# ── Rung 2: free tier → single-model fallback provider ────────────────────────


@pytest.mark.asyncio
@patch("llm.dependencies.within_free_allowance", new_callable=AsyncMock)
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
@patch("llm.gemini.genai.Client")
async def test_free_tier_builds_single_model_gemini(mock_gemini_cls, mock_get_key, mock_allow):
    """No key + fallback configured + within allowance → free Gemini, route==synth==FREE_TIER_MODEL."""
    mock_get_key.return_value = None
    mock_allow.return_value = True
    user = _fake_user()
    db = AsyncMock()

    with patch("llm.dependencies.settings") as s:
        s.DEFAULT_LLM_PROVIDER = "gemini"
        s.FREE_TIER_MODEL = "gemini-2.5-flash"
        s.LLM_FALLBACK_API_KEY = MagicMock()
        s.LLM_FALLBACK_API_KEY.get_secret_value.return_value = "sk-fallback-server"

        provider = await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    assert provider.__class__.__name__ == "GeminiProvider"
    # Free path: a single basic model — no tiering.
    assert provider._route_model == "gemini-2.5-flash"
    assert provider._synth_model == "gemini-2.5-flash"
    assert provider._route_model == provider._synth_model
    assert "sk-fallback-server" not in repr(provider)
    mock_allow.assert_awaited_once()


# ── Rung 3: exhausted → FreeTierExhaustedError (402, code=free_tier_exhausted) ─


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
async def test_no_key_no_fallback_raises_402(mock_get_key):
    """No key and no server fallback → exhausted ladder raises the 402 freemium error."""
    mock_get_key.return_value = None
    user = _fake_user()
    db = AsyncMock()

    with patch("llm.dependencies.settings") as s:
        s.LLM_FALLBACK_API_KEY = MagicMock()
        s.LLM_FALLBACK_API_KEY.get_secret_value.return_value = ""  # no fallback

        with pytest.raises(FreeTierExhaustedError) as exc_info:
            await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "free_tier_exhausted"


@pytest.mark.asyncio
@patch("llm.dependencies.within_free_allowance", new_callable=AsyncMock)
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
async def test_fallback_set_but_allowance_used_raises_402(mock_get_key, mock_allow):
    """Fallback configured but the daily allowance is spent → still 402 (gates before SSE)."""
    mock_get_key.return_value = None
    mock_allow.return_value = False  # allowance exhausted
    user = _fake_user()
    db = AsyncMock()

    with patch("llm.dependencies.settings") as s:
        s.LLM_FALLBACK_API_KEY = MagicMock()
        s.LLM_FALLBACK_API_KEY.get_secret_value.return_value = "sk-fallback-server"

        with pytest.raises(FreeTierExhaustedError):
            await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    mock_allow.assert_awaited_once()


# ── B05: per-conversation provider/model pick ────────────────────────────────


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key_for_provider", new_callable=AsyncMock)
@patch("llm.anthropic.AsyncAnthropic")
async def test_provider_pick_honored_with_stored_key(mock_anthropic_cls, mock_get_for):
    """A picked provider the user holds a key for is used; the picked model becomes synth_model."""
    user = _fake_user()
    mock_get_for.return_value = _fake_key_row(provider="anthropic", plaintext="sk-ant")
    db = AsyncMock()

    provider = await resolve_provider(
        db, _fresh_redis(), user, provider_choice="anthropic", model_choice="claude-picked"
    )

    assert provider.__class__.__name__ == "AnthropicProvider"
    assert provider._synth_model == "claude-picked"  # picked model → synthesis model
    assert provider._route_model == settings.tier_route_model("anthropic")  # routing stays tiered
    mock_get_for.assert_awaited_once()


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
@patch("llm.dependencies.get_user_llm_key_for_provider", new_callable=AsyncMock)
@patch("llm.openai.AsyncOpenAI")
async def test_provider_pick_without_matching_key_falls_through(
    mock_openai_cls, mock_get_for, mock_get_key
):
    """Picking a provider the user has NO key for falls through to a stored key — never 500s."""
    user = _fake_user()
    mock_get_for.return_value = None  # no anthropic key
    mock_get_key.return_value = _fake_key_row(provider="openai", plaintext="sk-test")
    db = AsyncMock()

    provider = await resolve_provider(
        db, _fresh_redis(), user, provider_choice="anthropic", model_choice="x"
    )

    assert provider.__class__.__name__ == "OpenAIProvider"  # fell through to the held key
    mock_get_for.assert_awaited_once()


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key_for_provider", new_callable=AsyncMock)
@patch("llm.openai.AsyncOpenAI")
async def test_provider_pick_without_model_uses_tiered_synth(mock_openai_cls, mock_get_for):
    """Picking a provider but no model keeps the provider's tiered synth model."""
    user = _fake_user()
    mock_get_for.return_value = _fake_key_row(provider="openai", plaintext="sk-test")
    db = AsyncMock()

    provider = await resolve_provider(
        db, _fresh_redis(), user, provider_choice="openai", model_choice=None
    )

    assert provider.__class__.__name__ == "OpenAIProvider"
    assert provider._synth_model == settings.tier_synth_model("openai")
    mock_get_for.assert_awaited_once()


# ── No-key-leak invariant ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("llm.dependencies.get_user_llm_key", new_callable=AsyncMock)
@patch("llm.anthropic.AsyncAnthropic")
async def test_decrypted_byok_key_never_in_repr(mock_anthropic_cls, mock_get_key):
    secret = "sk-ant-super-secret-key-do-not-leak"
    mock_get_key.return_value = _fake_key_row(provider="anthropic", plaintext=secret)
    user = _fake_user()
    db = AsyncMock()

    provider = await get_llm_provider(user=user, db=db, redis=_fresh_redis())

    assert secret not in repr(provider)
    assert secret not in str(provider)
