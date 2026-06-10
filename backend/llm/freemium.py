"""Free-tier allowance guards (Phase 6, docs/09 §3 + §3.1).

Two Redis counters protect the operator's *shared* Google free quota:

* **Per-user daily queries** (`freetier:user:{id}:{YYYYMMDD}`) — UX fairness so a single
  evaluator can't drain the whole free pool. Limit: ``FREE_TIER_DAILY_USER_QUERIES``.
* **Global daily LLM calls** (`freetier:global:{YYYYMMDD}`) — the hard ceiling against Google's
  shared per-key quota. Limit: ``FREE_TIER_GLOBAL_DAILY_CALLS``.

**Increment units** (documented per docs/09 §6 — *the cost contract*):

* The per-user counter is incremented by **1 per chat request** (one request == one "query").
* The global counter is incremented by ``GLOBAL_CALLS_PER_QUERY`` (== 2): one agentic query costs
  ~2 LLM calls (supervisor route + synthesis). Sizing the global ceiling in *calls* (not queries)
  keeps it directly comparable to Google's per-key request quota.

Both counters are **atomic** (``INCR``/``INCRBY``) and reset daily. A denied request must not
consume budget, so an increment that breaches its limit is immediately **rolled back** (``DECR``)
before returning ``False``. Per-user is checked first; if it fails we never touch the global
counter. If per-user passes but global fails, the per-user increment is rolled back too — so a
request that is ultimately denied leaves *both* counters where they started.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from config import settings

logger = structlog.get_logger(__name__)

# A single free query ≈ supervisor (route) + synthesis (generate) = 2 provider calls.
# The global guard is denominated in *calls* to match Google's per-key request quota directly.
GLOBAL_CALLS_PER_QUERY = 2

_SECONDS_PER_DAY = 86_400


def _utc_day_stamp(now: datetime | None = None) -> str:
    """``YYYYMMDD`` for the current UTC day — the daily-reset key suffix."""
    return (now or datetime.now(UTC)).strftime("%Y%m%d")


def _seconds_until_utc_midnight(now: datetime | None = None) -> int:
    """Seconds remaining until 00:00 UTC, so a freshly-created counter expires at day's end.

    Clamped to ``[1, 86400]`` so EXPIRE always gets a positive TTL even at the boundary.
    """
    now = now or datetime.now(UTC)
    elapsed = now.hour * 3600 + now.minute * 60 + now.second
    remaining = _SECONDS_PER_DAY - elapsed
    return max(1, min(remaining, _SECONDS_PER_DAY))


async def _incr_within(redis: aioredis.Redis, key: str, amount: int, limit: int, ttl: int) -> bool:
    """Reserve ``amount`` against ``key`` if it keeps the total ``<= limit``.

    The counter is created with its daily TTL via ``SET key 0 EX ttl NX`` — atomic, so there is no
    window where the key exists without an expiry (the old INCRBY-then-EXPIRE could leak a no-TTL
    key on a crash). ``NX`` arms the TTL once, on creation, and never pushes it forward on later
    increments. Rolls the increment back and returns ``False`` if it would exceed ``limit``.
    """
    await redis.set(key, 0, ex=ttl, nx=True)  # arm the day's counter + TTL once, atomically
    new_value = await redis.incrby(key, amount)
    if new_value > limit:
        await redis.decrby(key, amount)  # denied request must not consume budget
        return False
    return True


async def within_free_allowance(redis: aioredis.Redis, user_id: object) -> bool:
    """Reserve one free query for ``user_id`` against BOTH daily counters.

    Returns ``True`` only if the per-user query allowance AND the global call ceiling both have
    room; in that case the reservation (1 user query + ``GLOBAL_CALLS_PER_QUERY`` global calls)
    is committed. Returns ``False`` without net budget consumption otherwise.
    """
    stamp = _utc_day_stamp()
    ttl = _seconds_until_utc_midnight()
    user_key = f"freetier:user:{user_id}:{stamp}"
    global_key = f"freetier:global:{stamp}"

    try:
        if not await _incr_within(redis, user_key, 1, settings.FREE_TIER_DAILY_USER_QUERIES, ttl):
            return False

        if not await _incr_within(
            redis, global_key, GLOBAL_CALLS_PER_QUERY, settings.FREE_TIER_GLOBAL_DAILY_CALLS, ttl
        ):
            # Global ceiling hit even though the user had room — undo the per-user reservation so a
            # denied request leaves both counters untouched.
            await redis.decrby(user_key, 1)
            return False

        return True
    except RedisError:
        # FAIL OPEN: a Redis outage must not turn every free-tier chat into a 500. Allow the
        # request and log; the operator's hard provider quota is the real backstop, and BYOK users
        # never reach this path. (Trading strict per-user fairness for availability during an
        # outage is the deliberate choice — see docs/09 §3.)
        logger.error("freetier_redis_unavailable_fail_open", exc_info=True)
        return True
