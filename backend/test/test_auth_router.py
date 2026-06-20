"""Tests for auth register/login/refresh endpoints.

Uses the real DB via db_session fixture; skipped if TEST_DATABASE_URL not set.
Calls handler functions directly (no HTTP layer) to keep tests fast and focused.
"""

import pytest
from fastapi import HTTPException

from auth.router import login, logout, refresh, register
from auth.schemas import LoginIn, RefreshIn, RegisterIn
from auth.security import create_access_token, create_refresh_token, decode_token

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Minimal in-memory async stand-in for the revocation denylist (set + exists)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.store else 0


class _BrokenRedis:
    """A Redis whose every op raises RedisError — to assert the fail-open refresh path."""

    async def set(self, *args, **kwargs):
        from redis.exceptions import RedisError

        raise RedisError("redis down")

    async def exists(self, *args, **kwargs):
        from redis.exceptions import RedisError

        raise RedisError("redis down")


async def test_register_success(db_session):
    result = await register(
        RegisterIn(email="alice@example.com", username="alice", password="password123"),
        db=db_session,
    )
    # Phase 6 contract: register returns a NON-guest token pair (the shape the frontend
    # TokenPairSchema validates), not a bare UserOut.
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    assert result.user_id
    claims = decode_token(result.access_token)
    assert claims["type"] == "access"
    assert claims["sub"] == result.user_id
    assert claims.get("is_guest") is False


async def test_register_duplicate_email_raises_409(db_session):
    body = RegisterIn(email="dup@example.com", username="user1", password="password123")
    await register(body, db=db_session)

    body2 = RegisterIn(email="dup@example.com", username="user2", password="password123")
    with pytest.raises(HTTPException) as exc_info:
        await register(body2, db=db_session)
    assert exc_info.value.status_code == 409


async def test_register_duplicate_username_raises_409(db_session):
    body = RegisterIn(email="u1@example.com", username="dupuser", password="password123")
    await register(body, db=db_session)

    body2 = RegisterIn(email="u2@example.com", username="dupuser", password="password123")
    with pytest.raises(HTTPException) as exc_info:
        await register(body2, db=db_session)
    assert exc_info.value.status_code == 409


async def test_login_success(db_session):
    await register(
        RegisterIn(email="bob@example.com", username="bob", password="securepass1"),
        db=db_session,
    )
    result = await login(LoginIn(email="bob@example.com", password="securepass1"), db=db_session)
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"

    access_claims = decode_token(result.access_token)
    assert access_claims["type"] == "access"

    refresh_claims = decode_token(result.refresh_token)
    assert refresh_claims["type"] == "refresh"


async def test_login_bad_password_raises_401(db_session):
    await register(
        RegisterIn(email="charlie@example.com", username="charlie", password="correct-pass"),
        db=db_session,
    )
    with pytest.raises(HTTPException) as exc_info:
        await login(LoginIn(email="charlie@example.com", password="wrong-pass"), db=db_session)
    assert exc_info.value.status_code == 401


async def test_login_unknown_email_raises_401(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await login(LoginIn(email="nobody@example.com", password="pass"), db=db_session)
    assert exc_info.value.status_code == 401


async def test_refresh_with_valid_refresh_token():
    sub = "user-abc-123"
    token = create_refresh_token(subject=sub)
    result = await refresh(RefreshIn(refresh_token=token), redis=_FakeRedis())
    new_claims = decode_token(result.access_token)
    assert new_claims["sub"] == sub
    assert new_claims["type"] == "access"


async def test_refresh_preserves_guest_claim():
    """B11: refreshing a GUEST token must keep is_guest=True on both new tokens — otherwise a
    guest silently looks registered after one refresh and loses the upgrade CTA."""
    sub = "guest-xyz"
    token = create_refresh_token(subject=sub, is_guest=True)
    result = await refresh(RefreshIn(refresh_token=token), redis=_FakeRedis())
    assert decode_token(result.access_token).get("is_guest") is True
    assert decode_token(result.refresh_token).get("is_guest") is True


async def test_refresh_keeps_registered_non_guest():
    """A registered identity's refresh stays is_guest=False."""
    token = create_refresh_token(subject="reg-1")  # is_guest defaults False
    result = await refresh(RefreshIn(refresh_token=token), redis=_FakeRedis())
    assert decode_token(result.access_token).get("is_guest") is False
    assert decode_token(result.refresh_token).get("is_guest") is False


async def test_refresh_rejects_access_token():
    access = create_access_token(subject="user-abc")
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token=access), redis=_FakeRedis())
    assert exc_info.value.status_code == 401


async def test_refresh_rejects_garbage_token():
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token="not.a.token"), redis=_FakeRedis())
    assert exc_info.value.status_code == 401


async def test_logout_revokes_refresh_token():
    """R03: after logout, the SAME refresh token can no longer be refreshed (401)."""
    redis = _FakeRedis()
    token = create_refresh_token(subject="user-logout-1")
    # Sanity: it refreshes fine before logout.
    assert (await refresh(RefreshIn(refresh_token=token), redis=redis)).access_token
    # Log out (revoke), then a refresh with the same token is rejected.
    await logout(RefreshIn(refresh_token=token), redis=redis)
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token=token), redis=redis)
    assert exc_info.value.status_code == 401


async def test_logout_is_idempotent_on_invalid_token():
    """Logout never raises on a garbage/expired token — it's an idempotent no-op (204)."""
    assert await logout(RefreshIn(refresh_token="not.a.token"), redis=_FakeRedis()) is None


async def test_logout_revokes_only_the_presented_token():
    """A different (distinct-jti) refresh token still refreshes after another is revoked."""
    redis = _FakeRedis()
    revoked = create_refresh_token(subject="u-multi")
    other = create_refresh_token(subject="u-multi")
    await logout(RefreshIn(refresh_token=revoked), redis=redis)
    with pytest.raises(HTTPException):
        await refresh(RefreshIn(refresh_token=revoked), redis=redis)
    assert (await refresh(RefreshIn(refresh_token=other), redis=redis)).access_token


async def test_refresh_fails_open_when_redis_unavailable():
    """A Redis outage must not break refresh — the denylist check fails open (allows)."""
    token = create_refresh_token(subject="user-failopen")
    result = await refresh(RefreshIn(refresh_token=token), redis=_BrokenRedis())
    assert result.access_token  # refresh succeeded despite Redis being down


async def test_refresh_rotates_token_and_rejects_reuse():
    """R04: refresh is single-use — the presented token is consumed, so replaying it is rejected."""
    redis = _FakeRedis()
    rt1 = create_refresh_token(subject="rotate-me")
    pair = await refresh(RefreshIn(refresh_token=rt1), redis=redis)
    # A fresh refresh token (distinct jti) was issued.
    assert pair.refresh_token != rt1
    assert decode_token(pair.refresh_token)["jti"] != decode_token(rt1)["jti"]
    # Replaying the now-consumed rt1 is rejected (rotation).
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token=rt1), redis=redis)
    assert exc_info.value.status_code == 401
    # The freshly issued token still works.
    assert (await refresh(RefreshIn(refresh_token=pair.refresh_token), redis=redis)).access_token
