"""CORS middleware tests (Phase 3).

Verifies that CORS_ALLOWED_ORIGINS is enforced: allowed origin echoed back,
disallowed origin gets no ACAO header, and '*' is never returned with credentials.
"""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from config import settings

pytestmark = pytest.mark.asyncio


def _build_cors_app(origins: list[str]) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/ping")
    async def ping():
        return {"ok": True}

    return test_app


async def test_allowed_origin_echoed():
    app = _build_cors_app(["http://localhost:3000"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ping", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


async def test_disallowed_origin_not_echoed():
    app = _build_cors_app(["http://localhost:3000"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ping", headers={"Origin": "http://evil.example.com"})
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"


async def test_wildcard_never_returned_with_credentials():
    """'*' + credentials is the unsafe combination — must never be emitted."""
    app = _build_cors_app(["http://localhost:3000"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ping", headers={"Origin": "http://localhost:3000"})
    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "*", "ACAO must never be '*' when allow_credentials=True"


async def test_settings_cors_origins_not_wildcard():
    """The production config must not allow '*' as a listed origin."""
    assert "*" not in settings.CORS_ALLOWED_ORIGINS, (
        "CORS_ALLOWED_ORIGINS must not contain '*' — use an explicit allow-list"
    )


async def test_preflight_allowed_origin():
    app = _build_cors_app(["http://localhost:3000"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.options(
            "/ping",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
