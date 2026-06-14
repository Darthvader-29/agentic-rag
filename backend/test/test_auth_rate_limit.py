"""R05: per-IP rate limiting on the auth endpoints.

The limiter is a router-level dependency (``enforce_auth_rate_limit``) so it runs only on HTTP
requests, never the direct-call handler tests. These tests exercise the dependency directly with a
fake Redis (no app/DB), plus a structural check that it's wired onto every auth route.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from auth.dependencies import enforce_auth_rate_limit
from auth.router import router as auth_router
from config import settings


class _FakeRedis:
    """In-memory async stand-in supporting the limiter's SET-NX-EX + INCR window."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = int(value)
        return True

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]


class _BrokenRedis:
    async def set(self, *args, **kwargs):
        raise RedisError("redis down")

    async def incr(self, *args, **kwargs):
        raise RedisError("redis down")


def _req(host: str = "1.2.3.4"):
    """Minimal request stand-in: the limiter only reads request.client.host."""
    return SimpleNamespace(client=SimpleNamespace(host=host))


@pytest.mark.asyncio
async def test_allows_up_to_limit_then_429s():
    redis = _FakeRedis()
    req = _req()
    # The first AUTH_RATE_LIMIT_MAX requests pass.
    for _ in range(settings.AUTH_RATE_LIMIT_MAX):
        await enforce_auth_rate_limit(req, redis=redis)
    # The next request exceeds the window → 429.
    with pytest.raises(HTTPException) as exc_info:
        await enforce_auth_rate_limit(req, redis=redis)
    assert exc_info.value.status_code == 429
    # A plain HTTPException → FastAPI renders {"detail": ...}, the shape the FE error parser reads.
    assert isinstance(exc_info.value.detail, str) and exc_info.value.detail


@pytest.mark.asyncio
async def test_limit_is_per_ip():
    redis = _FakeRedis()
    # Exhaust one IP.
    for _ in range(settings.AUTH_RATE_LIMIT_MAX + 5):
        try:
            await enforce_auth_rate_limit(_req("10.0.0.1"), redis=redis)
        except HTTPException:
            pass
    # A different IP still has its full budget (no raise on the first call).
    await enforce_auth_rate_limit(_req("10.0.0.2"), redis=redis)


@pytest.mark.asyncio
async def test_fails_open_on_redis_outage():
    """A Redis outage must not block sign-in — the limiter fails open (no raise)."""
    await enforce_auth_rate_limit(_req(), redis=_BrokenRedis())


def test_limiter_is_wired_onto_the_auth_router():
    """Every /api/auth/* route inherits the limiter via the router-level dependency."""
    deps = [d.dependency for d in auth_router.dependencies]
    assert enforce_auth_rate_limit in deps
