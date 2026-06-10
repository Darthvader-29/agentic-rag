"""Per-request LLM provider dependency — the Phase 6 freemium ladder.

Three-tier resolution (docs/09 §3):
  1. **BYOK** — the user has a stored key → decrypt it and build a tiered provider on the user's
     own credentials (cheap ``route_model`` + strong ``synth_model`` for that provider).
  2. **Free tier** — no key, but the operator configured ``LLM_FALLBACK_API_KEY`` AND the user is
     still within the daily allowance (per-user query count AND the global call ceiling, both in
     Redis) → build a single-model Gemini provider on the operator's free key.
  3. **Exhausted** — no key and (no fallback OR the allowance is used up) → raise
     ``FreeTierExhaustedError`` (402, ``code="free_tier_exhausted"``). Because this raises inside
     the dependency, it gates the request BEFORE the SSE stream opens, so the 402 surfaces as a
     real HTTP status rather than an in-stream error.

Kept separate from dependencies.py to avoid a circular import:
  auth/dependencies.py → dependencies.py → (would be circular if we imported auth here)

No-key-leak invariant (Phase 4, carried forward): the decrypted api_key is a local variable only
— never logged, never cached, never placed on app.state, and gone when the function returns.
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog
from cryptography.fernet import InvalidToken
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from auth.crypto import decrypt_key
from auth.dependencies import get_current_user
from config import settings
from database.models import User
from database.repository import get_user_llm_key, get_user_llm_key_for_provider
from dependencies import get_db_session, get_redis
from exceptions import FreeTierExhaustedError, KeyDecryptionError
from llm.base import LLMProvider
from llm.factory import build_provider
from llm.freemium import within_free_allowance

logger = structlog.get_logger(__name__)

# The provider names the picker may send; anything else is treated as "no choice".
_ALLOWED_PROVIDERS = {"gemini", "openai", "anthropic"}


def _decrypt_or_raise(ciphertext: str) -> str:
    """Decrypt a stored BYOK key, mapping a Fernet failure to a clear, actionable error.

    An ``InvalidToken`` means the master key was rotated or the ciphertext is corrupt; since one
    master key encrypts ALL of a user's keys, there is no other key to fall through to — surface a
    400 telling the client to re-enter the key instead of a bare 500. Never logs key material.
    """
    try:
        return decrypt_key(ciphertext)
    except InvalidToken as exc:
        logger.error("byok_key_decrypt_failed")
        raise KeyDecryptionError() from exc


async def _read_model_selection(request: Request | None) -> tuple[str | None, str | None]:
    """Pull the optional per-conversation ``provider``/``model`` off the chat body.

    Reading ``request.json()`` here is safe even though the endpoint also parses the body — Starlette
    caches it. Any problem (no request, non-JSON, GET) degrades to "no choice".
    """
    if request is None:
        return None, None
    try:
        body = await request.json()
    except Exception:
        return None, None
    if not isinstance(body, dict):
        return None, None
    provider = body.get("provider")
    model = body.get("model")
    provider = provider if isinstance(provider, str) and provider in _ALLOWED_PROVIDERS else None
    model = model if isinstance(model, str) and model else None
    return provider, model


async def resolve_provider(
    db: AsyncSession,
    redis: aioredis.Redis,
    user: User,
    *,
    provider_choice: str | None = None,
    model_choice: str | None = None,
) -> LLMProvider:
    """The BYOK → free-tier → exhausted ladder, honoring an optional provider/model pick.

    Precedence: (1a) an explicit ``provider_choice`` the user actually holds a key for — the
    picked ``model_choice`` becomes the synthesis model; (1b) otherwise any stored key with the
    default per-node tiering; (2) the operator free tier; (3) exhausted → 402. A picked provider
    the user has NO key for is ignored (falls through) rather than 500-ing the request.
    """
    # 1a. Honor an explicit provider pick when the user has a stored key for it.
    if provider_choice in _ALLOWED_PROVIDERS:
        row = await get_user_llm_key_for_provider(db, user_id=user.id, provider=provider_choice)
        if row is not None:
            api_key = _decrypt_or_raise(row.ciphertext)  # plaintext: local only
            return build_provider(
                provider_choice,
                api_key,
                route_model=settings.tier_route_model(provider_choice),
                synth_model=model_choice or settings.tier_synth_model(provider_choice),
            )

    # 1b. BYOK default — the user's own key, with cheap/strong per-node model tiering.
    row = await get_user_llm_key(db, user_id=user.id)
    if row is not None:
        provider_name = row.provider or settings.DEFAULT_LLM_PROVIDER
        api_key = _decrypt_or_raise(row.ciphertext)  # plaintext: local only, never persisted/logged
        return build_provider(
            provider_name,
            api_key,
            route_model=settings.tier_route_model(provider_name),
            synth_model=settings.tier_synth_model(provider_name),
        )

    # 2. Free tier — operator's shared Gemini key, single basic model, Redis-guarded allowance.
    fallback = settings.LLM_FALLBACK_API_KEY.get_secret_value()
    if fallback and await within_free_allowance(redis, user.id):
        return build_provider(
            "gemini",
            fallback,
            route_model=settings.FREE_TIER_MODEL,
            synth_model=settings.FREE_TIER_MODEL,
        )

    # 3. Exhausted (or never eligible) — tell the frontend to prompt for BYOK. Raising here gates
    # the request before any SSE stream is opened, so it becomes a real 402 HTTP response.
    raise FreeTierExhaustedError()


async def get_llm_provider(
    request: Request = None,  # type: ignore[assignment]  # FastAPI injects; None only for direct calls
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> LLMProvider:
    """Per-request provider resolution, honoring the chat picker's optional provider/model.

    FastAPI injects ``request`` (the default ``None`` only applies to direct unit-test calls, which
    pass no body → no choice → the plain ladder). Annotated as plain ``Request`` (not ``Request |
    None``) so FastAPI treats it as the special request type rather than a Pydantic body field.
    """
    provider_choice, model_choice = await _read_model_selection(request)
    return await resolve_provider(
        db, redis, user, provider_choice=provider_choice, model_choice=model_choice
    )
