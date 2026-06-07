"""Tests for get_current_user dependency (Phase 3).

Uses real DB for the happy path; no DB needed for token-rejection tests.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth.dependencies import get_current_user
from auth.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
)
from database.repository import UserRepository

pytestmark = pytest.mark.asyncio


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return MagicMock(spec=HTTPAuthorizationCredentials, credentials=token)


async def test_get_current_user_valid_token(db_session):
    user = await UserRepository(db_session).create(
        email="valid@test.com",
        username="validuser",
        hashed_password=hash_password("pass"),
    )
    token = create_access_token(str(user.id))
    result = await get_current_user(creds=_creds(token), db=db_session)
    assert result.id == user.id


async def test_get_current_user_missing_header_raises_401(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(creds=None, db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_garbage_token_raises_401(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(creds=_creds("garbage.token"), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_expired_token_raises_401(db_session):
    token = create_access_token(subject="x", ttl=timedelta(seconds=-1))
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(creds=_creds(token), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_refresh_token_rejected(db_session):
    """A refresh token must be rejected at access-token–protected endpoints."""
    refresh = create_refresh_token(subject="user-abc")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(creds=_creds(refresh), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_deleted_user_raises_401(db_session):
    """Token is valid but the user no longer exists in the DB."""
    import uuid

    phantom_id = str(uuid.uuid4())
    token = create_access_token(subject=phantom_id)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(creds=_creds(token), db=db_session)
    assert exc_info.value.status_code == 401
