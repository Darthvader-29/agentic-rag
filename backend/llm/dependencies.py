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
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.crypto import decrypt_key
from auth.dependencies import get_current_user
from config import settings
from database.models import User
from database.repository import get_user_llm_key
from dependencies import get_db_session, get_redis
from exceptions import FreeTierExhaustedError
from llm.base import LLMProvider
from llm.factory import build_provider
from llm.freemium import within_free_allowance


async def get_llm_provider(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> LLMProvider:
    """Resolve the per-request LLM provider via the BYOK → free-tier → exhausted ladder."""
    # 1. BYOK — the user's own key, with cheap/strong per-node model tiering.
    row = await get_user_llm_key(db, user_id=user.id)
    if row is not None:
        provider_name = row.provider or settings.DEFAULT_LLM_PROVIDER
        api_key = decrypt_key(row.ciphertext)  # plaintext: local only, never persisted/logged
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
