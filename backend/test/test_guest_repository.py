"""Repository-layer tests for guest create + upgrade (Phase 6).

Requires TEST_DATABASE_URL — skipped offline (db_session fixture skips). These assert the
DB-level contract: a guest User has is_guest=True, upgrade flips it to False while preserving
the same primary key and the user's existing BYOK keys + sessions.
"""

import uuid

import pytest

from auth.security import hash_password
from database import repository as repo
from database.models import User, UserLLMKey
from database.repository import LLMKeyRepository, UserRepository

pytestmark = pytest.mark.asyncio


async def test_create_guest_sets_is_guest_true(db_session):
    user = await UserRepository(db_session).create_guest(
        email="guest-a@guest.local",
        username="guest-a",
        hashed_password=hash_password("random"),
    )
    assert user.is_guest is True
    assert user.email == "guest-a@guest.local"


async def test_registered_user_defaults_is_guest_false(db_session):
    user = await UserRepository(db_session).create(
        email="real@example.com",
        username="realuser",
        hashed_password=hash_password("password123"),
    )
    await db_session.flush()
    assert user.is_guest is False


async def test_upgrade_guest_preserves_id_and_flips_flag(db_session):
    repo_users = UserRepository(db_session)
    guest = await repo_users.create_guest(
        email="guest-b@guest.local",
        username="guest-b",
        hashed_password=hash_password("rand"),
    )
    guest_id = guest.id

    upgraded = await repo_users.upgrade_guest(
        guest,
        email="claimed@example.com",
        username="claimed",
        hashed_password=hash_password("password123"),
    )

    assert upgraded.id == guest_id  # SAME user row
    assert upgraded.is_guest is False
    assert upgraded.email == "claimed@example.com"
    assert upgraded.username == "claimed"


async def test_upgrade_guest_preserves_byok_keys(db_session):
    """Upgrading must not touch the user's stored LLM keys — they belong to the same user_id."""
    repo_users = UserRepository(db_session)
    guest = await repo_users.create_guest(
        email="guest-c@guest.local",
        username="guest-c",
        hashed_password=hash_password("rand"),
    )
    await LLMKeyRepository(db_session).upsert(
        user_id=guest.id, provider="gemini", ciphertext="cipher-xyz"
    )

    await repo_users.upgrade_guest(
        guest,
        email="claimed-c@example.com",
        username="claimed-c",
        hashed_password=hash_password("password123"),
    )

    key = await LLMKeyRepository(db_session).get(user_id=guest.id, provider="gemini")
    assert key is not None
    assert key.ciphertext == "cipher-xyz"
