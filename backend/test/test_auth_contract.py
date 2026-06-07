"""C3 (FE/BE contract): /register and /upgrade return a TokenPair, not a bare UserOut.

The frontend validates both responses with TokenPairSchema (access_token + refresh_token required)
and signs the user in; a UserOut response made every registration/upgrade fail client-side. Offline
— UserRepository is patched so no DB is needed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import router as auth_router
from auth.schemas import RegisterIn, UpgradeIn
from auth.security import decode_token


@pytest.mark.asyncio
async def test_register_returns_non_guest_token_pair(monkeypatch):
    user = MagicMock()
    user.id = uuid.uuid4()
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=None)
    repo.get_by_username = AsyncMock(return_value=None)
    repo.create = AsyncMock(return_value=user)
    monkeypatch.setattr(auth_router, "UserRepository", lambda db: repo)

    out = await auth_router.register(
        RegisterIn(email="alice@example.com", username="alice", password="password123"),
        db=MagicMock(),
    )

    assert out.access_token and out.refresh_token
    assert out.token_type == "bearer"
    assert out.user_id == str(user.id)
    claims = decode_token(out.access_token)
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"
    assert claims["is_guest"] is False


@pytest.mark.asyncio
async def test_upgrade_returns_fresh_non_guest_token_pair(monkeypatch):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.is_guest = True  # passes the "must be a guest" guard
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=None)
    repo.get_by_username = AsyncMock(return_value=None)
    repo.upgrade_guest = AsyncMock(return_value=user)
    monkeypatch.setattr(auth_router, "UserRepository", lambda db: repo)

    out = await auth_router.upgrade(
        UpgradeIn(email="claimed@example.com", username="claimeduser", password="password123"),
        current_user=user,
        db=MagicMock(),
    )

    assert out.access_token and out.refresh_token
    assert out.user_id == str(user.id)
    assert decode_token(out.access_token)["is_guest"] is False
