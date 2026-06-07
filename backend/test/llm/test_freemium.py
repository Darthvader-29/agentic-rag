"""Tests for llm/freemium.py — the two Redis free-tier counters.

Uses fakeredis (real INCR/DECR/EXPIRE semantics, no network) so the atomic reserve-and-roll-back
behaviour is exercised exactly as in production.
"""

from datetime import UTC, datetime

import fakeredis.aioredis as fakeredis
import pytest

from llm import freemium
from llm.freemium import (
    GLOBAL_CALLS_PER_QUERY,
    _seconds_until_utc_midnight,
    _utc_day_stamp,
    within_free_allowance,
)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def _set_limits(monkeypatch, *, per_user: int, global_calls: int) -> None:
    monkeypatch.setattr(freemium.settings, "FREE_TIER_DAILY_USER_QUERIES", per_user)
    monkeypatch.setattr(freemium.settings, "FREE_TIER_GLOBAL_DAILY_CALLS", global_calls)


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_within_allowance_passes_and_increments(redis, monkeypatch):
    _set_limits(monkeypatch, per_user=10, global_calls=1200)
    stamp = _utc_day_stamp()

    assert await within_free_allowance(redis, "u1") is True

    # One query reserved for the user; GLOBAL_CALLS_PER_QUERY reserved globally.
    assert int(await redis.get(f"freetier:user:u1:{stamp}")) == 1
    assert int(await redis.get(f"freetier:global:{stamp}")) == GLOBAL_CALLS_PER_QUERY


@pytest.mark.asyncio
async def test_users_are_isolated(redis, monkeypatch):
    _set_limits(monkeypatch, per_user=1, global_calls=1200)
    stamp = _utc_day_stamp()

    assert await within_free_allowance(redis, "alice") is True
    assert await within_free_allowance(redis, "bob") is True  # bob has his own counter

    assert int(await redis.get(f"freetier:user:alice:{stamp}")) == 1
    assert int(await redis.get(f"freetier:user:bob:{stamp}")) == 1
    # Both queries counted against the shared global ceiling.
    assert int(await redis.get(f"freetier:global:{stamp}")) == 2 * GLOBAL_CALLS_PER_QUERY


# ── Per-user exhaustion ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_user_limit_exhausts(redis, monkeypatch):
    _set_limits(monkeypatch, per_user=3, global_calls=1200)
    stamp = _utc_day_stamp()

    for _ in range(3):
        assert await within_free_allowance(redis, "u1") is True
    # 4th request for the same user is denied.
    assert await within_free_allowance(redis, "u1") is False

    # The denied request did not consume budget: user stays at exactly the limit,
    # and the global counter was not bumped by the denied attempt.
    assert int(await redis.get(f"freetier:user:u1:{stamp}")) == 3
    assert int(await redis.get(f"freetier:global:{stamp}")) == 3 * GLOBAL_CALLS_PER_QUERY


# ── Global exhaustion (even when per-user has room) ───────────────────────────


@pytest.mark.asyncio
async def test_global_ceiling_exhausts_with_user_room(redis, monkeypatch):
    # Generous per-user, tiny global: ceiling = exactly one query's worth of calls.
    _set_limits(monkeypatch, per_user=100, global_calls=GLOBAL_CALLS_PER_QUERY)
    stamp = _utc_day_stamp()

    assert await within_free_allowance(redis, "u1") is True  # fills the global ceiling
    assert await within_free_allowance(redis, "u1") is False  # global full though user has room

    # Global stays at the ceiling (denied attempt rolled back its INCRBY)...
    assert int(await redis.get(f"freetier:global:{stamp}")) == GLOBAL_CALLS_PER_QUERY
    # ...and the per-user counter was rolled back too, so the user wasn't charged for a denied req.
    assert int(await redis.get(f"freetier:user:u1:{stamp}")) == 1


@pytest.mark.asyncio
async def test_denied_request_does_not_permanently_consume_budget(redis, monkeypatch):
    """A denial frees budget so a later (eligible) request can still succeed."""
    _set_limits(monkeypatch, per_user=100, global_calls=GLOBAL_CALLS_PER_QUERY)

    assert await within_free_allowance(redis, "u1") is True
    assert await within_free_allowance(redis, "u2") is False  # global full → denied + rolled back

    # Raise the global ceiling by one query's worth; u2 can now get through because the prior
    # denial did NOT leave phantom consumption behind.
    monkeypatch.setattr(
        freemium.settings, "FREE_TIER_GLOBAL_DAILY_CALLS", 2 * GLOBAL_CALLS_PER_QUERY
    )
    assert await within_free_allowance(redis, "u2") is True


# ── Daily reset / EXPIRE ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_keys_have_expiry_set(redis, monkeypatch):
    _set_limits(monkeypatch, per_user=10, global_calls=1200)
    stamp = _utc_day_stamp()

    await within_free_allowance(redis, "u1")

    user_ttl = await redis.ttl(f"freetier:user:u1:{stamp}")
    global_ttl = await redis.ttl(f"freetier:global:{stamp}")
    # Both counters carry a positive TTL (<= one day) so they reset automatically.
    assert 0 < user_ttl <= 86_400
    assert 0 < global_ttl <= 86_400


def test_day_stamp_is_utc_yyyymmdd():
    fixed = datetime(2026, 6, 2, 13, 30, 0, tzinfo=UTC)
    assert _utc_day_stamp(fixed) == "20260602"


def test_seconds_until_midnight_is_positive_and_bounded():
    # Just before midnight UTC → small positive TTL, never zero or negative.
    near_midnight = datetime(2026, 6, 2, 23, 59, 59, tzinfo=UTC)
    ttl = _seconds_until_utc_midnight(near_midnight)
    assert ttl == 1

    just_after_midnight = datetime(2026, 6, 2, 0, 0, 1, tzinfo=UTC)
    ttl2 = _seconds_until_utc_midnight(just_after_midnight)
    assert 86_390 < ttl2 <= 86_400


@pytest.mark.asyncio
async def test_expire_set_once_not_pushed_forward(redis, monkeypatch):
    """EXPIRE is armed when the key is created and not reset on every increment."""
    _set_limits(monkeypatch, per_user=10, global_calls=1200)
    stamp = _utc_day_stamp()
    key = f"freetier:user:u1:{stamp}"

    await within_free_allowance(redis, "u1")
    ttl_after_first = await redis.ttl(key)
    # Manually shrink the TTL to simulate time passing within the day.
    await redis.expire(key, ttl_after_first - 100)
    shrunk = await redis.ttl(key)

    await within_free_allowance(redis, "u1")  # second increment must NOT re-arm EXPIRE
    ttl_after_second = await redis.ttl(key)
    assert ttl_after_second <= shrunk + 1  # not pushed back up to a full day
