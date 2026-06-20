"""R07: security response headers on every backend response (API JSON + the legacy /static SPA)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app import app
from config import settings

pytestmark = pytest.mark.asyncio


async def _health_headers():
    # /health is lifespan-free (no app.state); the middleware runs regardless.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    return resp.headers


async def test_core_security_headers_present():
    headers = await _health_headers()
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in headers.get("permissions-policy", "")


async def test_csp_locks_down_framing_and_objects():
    csp = (await _health_headers()).get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


async def test_hsts_absent_in_development():
    # conftest sets ENVIRONMENT=development → no HSTS over plain http.
    assert "strict-transport-security" not in (await _health_headers())


async def test_hsts_present_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    hsts = (await _health_headers()).get("strict-transport-security", "")
    assert "max-age=" in hsts
