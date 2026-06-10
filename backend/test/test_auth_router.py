"""Tests for auth register/login/refresh endpoints.

Uses the real DB via db_session fixture; skipped if TEST_DATABASE_URL not set.
Calls handler functions directly (no HTTP layer) to keep tests fast and focused.
"""

import pytest
from fastapi import HTTPException

from auth.router import login, refresh, register
from auth.schemas import LoginIn, RefreshIn, RegisterIn
from auth.security import create_access_token, create_refresh_token, decode_token

pytestmark = pytest.mark.asyncio


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
    result = await refresh(RefreshIn(refresh_token=token))
    new_claims = decode_token(result.access_token)
    assert new_claims["sub"] == sub
    assert new_claims["type"] == "access"


async def test_refresh_rejects_access_token():
    access = create_access_token(subject="user-abc")
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token=access))
    assert exc_info.value.status_code == 401


async def test_refresh_rejects_garbage_token():
    with pytest.raises(HTTPException) as exc_info:
        await refresh(RefreshIn(refresh_token="not.a.token"))
    assert exc_info.value.status_code == 401
