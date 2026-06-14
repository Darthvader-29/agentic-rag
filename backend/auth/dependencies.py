"""FastAPI dependency that validates a bearer token and loads the current user (Phase 3)."""

import jwt
import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import decode_token, require_token_type
from config import settings
from database.models import User
from database.repository import UserRepository
from dependencies import get_db_session, get_redis
from exceptions import InvalidTokenTypeError

logger = structlog.get_logger(__name__)

# auto_error=False so we can return 401 instead of FastAPI's default 403
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db_session),  # shares the per-request session (no double-open)
) -> User:
    if creds is None:
        raise HTTPException(401, "missing authorization header")
    try:
        claims = require_token_type(decode_token(creds.credentials), expected="access")
    except (jwt.PyJWTError, InvalidTokenTypeError) as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = await UserRepository(db).get(claims["sub"])
    if user is None:
        raise HTTPException(401, "user no longer exists")
    return user


async def enforce_auth_rate_limit(
    request: Request,
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """Per-IP fixed-window throttle on the auth endpoints (R05).

    Bounds credential stuffing and unbounded ``/auth/guest`` minting. A fixed window keyed by client
    IP, armed atomically (``SET 0 EX NX`` → ``INCR``) like the freemium counters so a crash can't
    leave a no-TTL key. FAILS OPEN on a Redis outage — a blip must not lock everyone out of signing
    in (the same availability tradeoff as the freemium guard + the token-revocation denylist). Wired
    as a router-level dependency, so it runs for every ``/api/auth/*`` HTTP request (not direct
    handler calls in tests), and surfaces a clean ``{"detail": ...}`` 429.
    """
    ip = request.client.host if request.client else "unknown"
    key = f"ratelimit:auth:{ip}"
    try:
        await redis.set(key, 0, ex=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS, nx=True)
        count = await redis.incr(key)
    except RedisError:
        logger.error("auth_rate_limit_redis_unavailable_fail_open", exc_info=True)
        return
    if count > settings.AUTH_RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication requests — please wait a moment and try again.",
        )
