"""Refresh-token revocation via a Redis ``jti`` denylist (R03).

Logout records the presented refresh token's ``jti`` here; ``/refresh`` rejects any token whose
``jti`` is on the list. Each entry self-expires at the token's own ``exp`` (no manual cleanup, and
the key can never outlive the token it revokes).

Availability stance (mirrors ``llm.freemium``'s deliberate fail-open): the read path
(``is_token_revoked``) **fails open** — a Redis outage must not lock every user out of refreshing —
and the write path (``revoke_token``) is **best-effort** — a blip during logout logs rather than
500s, since the client drops its tokens locally regardless. R04 layers rotation + ``aud``/``iss`` on
top of this.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

_REVOKED_PREFIX = "auth:revoked:"


def _revocation_key(jti: str) -> str:
    return f"{_REVOKED_PREFIX}{jti}"


def _ttl_until_expiry(exp: int | None, now: datetime | None = None) -> int:
    """Seconds until the token's ``exp`` so the denylist entry dies with the token. Clamped to >= 1."""
    if not exp:
        return 1
    now_ts = int((now or datetime.now(UTC)).timestamp())
    return max(1, int(exp) - now_ts)


async def revoke_token(redis: aioredis.Redis, claims: dict) -> bool:
    """Denylist a token's ``jti`` until it would expire. Best-effort.

    Returns ``True`` if the jti was recorded as revoked, ``False`` for a legacy token without a
    ``jti`` or if Redis was unreachable (logged — the caller still completes logout).
    """
    jti = claims.get("jti")
    if not jti:
        return False  # legacy token minted before jti existed — nothing to revoke
    ttl = _ttl_until_expiry(claims.get("exp"))
    try:
        await redis.set(_revocation_key(str(jti)), "1", ex=ttl)
        return True
    except RedisError:
        logger.error("token_revocation_write_failed", exc_info=True)
        return False


async def is_token_revoked(redis: aioredis.Redis, claims: dict) -> bool:
    """Return whether the token's ``jti`` is on the denylist. FAILS OPEN on a Redis outage.

    A Redis blip returns ``False`` (treat as not-revoked) so refresh stays available — the same
    availability-over-strictness tradeoff ``llm.freemium`` makes for the free-tier counters.
    """
    jti = claims.get("jti")
    if not jti:
        return False
    try:
        return await redis.exists(_revocation_key(str(jti))) > 0
    except RedisError:
        logger.error("token_revocation_check_failed_fail_open", exc_info=True)
        return False
