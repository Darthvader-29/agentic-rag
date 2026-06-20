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

**Refund (R16).** ``within_free_allowance`` *reserves* budget BEFORE the agentic graph runs, so a
turn that then fails or is aborted before producing an answer would otherwise burn the shared free
quota for nothing. ``refund_free_allowance`` is the symmetric inverse: it gives back exactly what a
successful reservation took (1 user query + ``GLOBAL_CALLS_PER_QUERY`` global calls). It mirrors the
reserve path's stance — it **fails open** on a Redis outage (a failed turn must never itself become
a 500) and it clamps each counter at ``0`` so a stray/double refund can never drive a counter
negative and hand out free budget. Reserve+refund are thus net-zero for a turn that never produced
an answer.
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


async def _decr_clamped(redis: aioredis.Redis, key: str, amount: int) -> None:
    """Give ``amount`` back to ``key`` (the refund inverse of ``_incr_within``), never below 0.

    ``DECRBY`` would happily go negative — a counter below 0 hands out free budget on the next
    reservation. We clamp to 0 if the refund overshoots (a double refund, or a refund that races the
    daily reset which already cleared the key). The corrective ``SET`` mirrors the reserve path's own
    non-atomic ``INCRBY``+``DECRBY`` style (see the module docstring on bounded concurrency); the
    clamp's only job is the floor, not exact-once accounting.
    """
    new_value = await redis.decrby(key, amount)
    if new_value < 0:
        await redis.set(key, 0)


async def refund_free_allowance(redis: aioredis.Redis, user_id: object) -> None:
    """Refund one free query's reservation for ``user_id`` on BOTH daily counters.

    The symmetric inverse of a SUCCESSFUL ``within_free_allowance`` reservation: it credits back the
    same 1 user query + ``GLOBAL_CALLS_PER_QUERY`` global calls. Call this only when a reservation
    was actually made (the free tier was used) AND the turn then failed/aborted before producing an
    answer, so a wasted turn nets zero quota consumed. Fails open on a Redis outage and clamps both
    counters at 0 (see ``_decr_clamped``).
    """
    stamp = _utc_day_stamp()
    user_key = f"freetier:user:{user_id}:{stamp}"
    global_key = f"freetier:global:{stamp}"
    try:
        await _decr_clamped(redis, user_key, 1)
        await _decr_clamped(redis, global_key, GLOBAL_CALLS_PER_QUERY)
    except RedisError:
        # FAIL OPEN, mirroring within_free_allowance: a Redis outage must not turn a failed/aborted
        # turn into a 500. The reservation simply isn't refunded; the daily TTL reclaims it anyway.
        logger.error("freetier_refund_redis_unavailable_fail_open", exc_info=True)


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
