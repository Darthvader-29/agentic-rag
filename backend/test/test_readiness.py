"""R23: readiness probe (`GET /health/ready`).

`/health` is liveness only. Readiness additionally checks that DB / Redis / S3 / Pinecone are
reachable and returns 503 (the `{detail}` envelope the FE parses) when any dependency is down, so an
orchestrator can pull the instance out of rotation instead of routing traffic into 500s.

These tests drive the route over ASGITransport (no lifespan) with the `app.state` clients replaced by
mocks — a healthy set returns 200 `ready`, and a single raising dependency flips the whole probe to
503 while still reporting the per-dependency status (one failure doesn't mask the others).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app import app

pytestmark = pytest.mark.asyncio


def _healthy_state() -> dict:
    """Build mock clients for app.state where every dependency probe succeeds."""
    # DB: sessionmaker() → async context manager yielding a session with an async execute().
    session = AsyncMock()
    session.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _session_cm():
        yield session

    sessionmaker = MagicMock(side_effect=lambda: _session_cm())

    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)

    s3 = AsyncMock()
    s3.object_exists = AsyncMock(return_value=False)  # 404 (key absent) still = reachable

    pinecone = AsyncMock()
    pinecone.describe_stats = AsyncMock(return_value={})

    return {"db_sessionmaker": sessionmaker, "redis": redis, "s3": s3, "pinecone": pinecone}


def _apply_state(state: dict) -> None:
    app.state.db_sessionmaker = state["db_sessionmaker"]
    app.state.redis = state["redis"]
    app.state.s3 = state["s3"]
    app.state.pinecone = state["pinecone"]


async def _get_ready():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health/ready")


async def test_ready_returns_200_when_all_dependencies_up():
    _apply_state(_healthy_state())
    resp = await _get_ready()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "database": "up",
        "redis": "up",
        "s3": "up",
        "pinecone": "up",
    }


@pytest.mark.parametrize(
    "dep_key,check_name",
    [
        ("redis", "redis"),
        ("s3", "s3"),
        ("pinecone", "pinecone"),
    ],
)
async def test_ready_returns_503_when_a_dependency_is_down(dep_key, check_name):
    state = _healthy_state()
    # Make the chosen dependency's probe raise (simulates an outage).
    if dep_key == "redis":
        state["redis"].ping = AsyncMock(side_effect=ConnectionError("redis down"))
    elif dep_key == "s3":
        state["s3"].object_exists = AsyncMock(side_effect=OSError("s3 endpoint unreachable"))
    elif dep_key == "pinecone":
        state["pinecone"].describe_stats = AsyncMock(side_effect=RuntimeError("pinecone down"))
    _apply_state(state)

    resp = await _get_ready()
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "detail" in body  # FE-parseable envelope
    assert body["checks"][check_name] == "down"
    # The other dependencies still report up — one failure doesn't mask the rest.
    healthy = {k: v for k, v in body["checks"].items() if k != check_name}
    assert all(v == "up" for v in healthy.values())


async def test_ready_503_when_database_raises():
    state = _healthy_state()

    @asynccontextmanager
    async def _boom_cm():
        raise ConnectionError("db down")
        yield  # pragma: no cover

    state["db_sessionmaker"] = MagicMock(side_effect=lambda: _boom_cm())
    _apply_state(state)

    resp = await _get_ready()
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "down"


async def test_liveness_health_unchanged():
    """The existing /health route stays liveness-only (no dependency checks)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "version": "1.0.0"}
