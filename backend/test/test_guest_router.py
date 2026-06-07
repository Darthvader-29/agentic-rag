"""Tests for guest mint + guest→registered upgrade endpoints (Phase 6).

DB-backed (db_session) — skipped offline. Handler functions are called directly (no HTTP layer),
mirroring test_auth_router.py. The upgrade path asserts the SAME user_id survives and BYOK keys
are preserved; it also rejects re-upgrading a registered user and taken email/username.
"""

import uuid

import pytest
from fastapi import HTTPException

from auth.dependencies import get_current_user
from auth.router import guest, register, upgrade
from auth.schemas import RegisterIn, UpgradeIn
from auth.security import create_access_token, decode_token, hash_password
from database.models import User
from database.repository import LLMKeyRepository, UserRepository

pytestmark = pytest.mark.asyncio


async def test_guest_mints_tokens_and_user_id(db_session):
    result = await guest(db=db_session)
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    assert result.user_id

    # the access token carries the guest claim so the client can tell after reload
    claims = decode_token(result.access_token)
    assert claims["is_guest"] is True
    assert claims["sub"] == result.user_id


async def test_guest_user_persisted_as_is_guest(db_session):
    result = await guest(db=db_session)
    user = await UserRepository(db_session).get(result.user_id)
    assert user is not None
    assert user.is_guest is True


async def test_two_guests_get_distinct_ids(db_session):
    a = await guest(db=db_session)
    b = await guest(db=db_session)
    assert a.user_id != b.user_id


async def test_upgrade_promotes_same_user_and_preserves_keys(db_session):
    g = await guest(db=db_session)
    guest_user = await UserRepository(db_session).get(g.user_id)
    # give the guest a BYOK key — it must survive the upgrade
    await LLMKeyRepository(db_session).upsert(
        user_id=guest_user.id, provider="gemini", ciphertext="cipher-keep"
    )

    out = await upgrade(
        UpgradeIn(email="claimed@example.com", username="claimeduser", password="password123"),
        current_user=guest_user,
        db=db_session,
    )

    # same user_id, now a registered account
    assert str(out.id) == g.user_id
    assert out.email == "claimed@example.com"
    assert out.username == "claimeduser"

    refreshed = await UserRepository(db_session).get(g.user_id)
    assert refreshed.is_guest is False
    # key preserved
    key = await LLMKeyRepository(db_session).get(user_id=refreshed.id, provider="gemini")
    assert key is not None and key.ciphertext == "cipher-keep"


async def test_upgrade_rejects_already_registered_user(db_session):
    real = await UserRepository(db_session).create(
        email="already@example.com",
        username="alreadyuser",
        hashed_password=hash_password("password123"),
    )
    await db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        await upgrade(
            UpgradeIn(email="new@example.com", username="newname", password="password123"),
            current_user=real,
            db=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_upgrade_rejects_taken_email(db_session):
    await UserRepository(db_session).create(
        email="taken@example.com",
        username="takenowner",
        hashed_password=hash_password("password123"),
    )
    g = await guest(db=db_session)
    guest_user = await UserRepository(db_session).get(g.user_id)
    with pytest.raises(HTTPException) as exc_info:
        await upgrade(
            UpgradeIn(email="taken@example.com", username="freshname", password="password123"),
            current_user=guest_user,
            db=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_upgrade_rejects_taken_username(db_session):
    await UserRepository(db_session).create(
        email="owner2@example.com",
        username="takenname",
        hashed_password=hash_password("password123"),
    )
    g = await guest(db=db_session)
    guest_user = await UserRepository(db_session).get(g.user_id)
    with pytest.raises(HTTPException) as exc_info:
        await upgrade(
            UpgradeIn(email="fresh2@example.com", username="takenname", password="password123"),
            current_user=guest_user,
            db=db_session,
        )
    assert exc_info.value.status_code == 409
