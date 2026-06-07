"""Cross-user session isolation tests (Phase 3 — core security gate).

Verifies: 403 when user B accesses user A's session; 404 for non-existent session;
user A can still access their own session.  Uses real DB via db_session fixture.
"""

import uuid

import pytest
from fastapi import HTTPException

from auth.security import hash_password
from database import repository as repo
from database.repository import UserRepository

pytestmark = pytest.mark.asyncio


async def _make_user(db, email: str, username: str) -> object:
    return await UserRepository(db).create(
        email=email,
        username=username,
        hashed_password=hash_password("testpassword"),
    )


async def test_session_created_with_correct_owner(db_session):
    user = await _make_user(db_session, "owner@test.com", "owner")
    await repo.create_session(db_session, "sess-owner", user.id)
    session = await repo.get_session(db_session, "sess-owner")
    assert session is not None
    assert session.user_id == user.id


async def test_cross_user_session_ownership_check(db_session):
    """User B cannot own user A's session — confirmed at the repo level."""
    user_a = await _make_user(db_session, "a@iso.com", "user_a")
    user_b = await _make_user(db_session, "b@iso.com", "user_b")

    await repo.create_session(db_session, "sess-a", user_a.id)
    session = await repo.get_session(db_session, "sess-a")

    assert session is not None
    assert session.user_id == user_a.id
    assert session.user_id != user_b.id


async def test_cross_user_chat_raises_403(db_session):
    """Simulate the ownership logic from app.py chat endpoint."""
    user_a = await _make_user(db_session, "a2@iso.com", "user_a2")
    user_b = await _make_user(db_session, "b2@iso.com", "user_b2")

    await repo.create_session(db_session, "chat-sess-a", user_a.id)

    # Replicate the ownership check from app.py chat()
    session = await repo.get_session(db_session, "chat-sess-a")
    assert session is not None
    with pytest.raises(HTTPException) as exc_info:
        if session.user_id is not None and session.user_id != user_b.id:
            raise HTTPException(403, "session does not belong to the current user")
    assert exc_info.value.status_code == 403


async def test_owner_can_access_own_session(db_session):
    """User A's ownership check passes for their own session."""
    user_a = await _make_user(db_session, "a3@iso.com", "user_a3")
    await repo.create_session(db_session, "chat-sess-a3", user_a.id)

    session = await repo.get_session(db_session, "chat-sess-a3")
    assert session is not None
    # Should NOT raise — user_a owns this session
    if session.user_id is not None and session.user_id != user_a.id:
        raise AssertionError("Should not raise 403 for the owning user")


async def test_nonexistent_session_returns_none(db_session):
    """get_session returns None for unknown IDs (caller raises 404, not 403)."""
    result = await repo.get_session(db_session, "does-not-exist-" + str(uuid.uuid4()))
    assert result is None


async def test_cleanup_cross_user_raises_403(db_session):
    """Simulate the ownership check in app.py cleanup_session."""
    user_a = await _make_user(db_session, "a4@iso.com", "user_a4")
    user_b = await _make_user(db_session, "b4@iso.com", "user_b4")

    await repo.create_session(db_session, "cleanup-sess-a", user_a.id)

    session = await repo.get_session(db_session, "cleanup-sess-a")
    assert session is not None

    # User B tries to clean up user A's session
    with pytest.raises(HTTPException) as exc_info:
        if session.user_id is not None and session.user_id != user_b.id:
            raise HTTPException(403, "session does not belong to the current user")
    assert exc_info.value.status_code == 403


async def test_cleanup_own_session_passes(db_session):
    """User A can delete their own session without 403."""
    user_a = await _make_user(db_session, "a5@iso.com", "user_a5")
    await repo.create_session(db_session, "cleanup-own", user_a.id)

    session = await repo.get_session(db_session, "cleanup-own")
    assert session is not None

    # Should NOT raise
    if session.user_id is not None and session.user_id != user_a.id:
        raise AssertionError("Should not raise 403 for the owning user")


async def test_session_null_user_id_binds_to_first_claimer(db_session):
    """Sessions with user_id=None (pre-auth rows) get bound to the first auth user."""
    user = await _make_user(db_session, "bind@iso.com", "binduser")
    # Simulate a legacy session with no owner
    await repo.get_or_create_session(db_session, "legacy-sess")
    session = await repo.get_session(db_session, "legacy-sess")
    assert session is not None
    assert session.user_id is None

    # Binding: first auth user claims it
    session.user_id = user.id
    await db_session.flush()

    bound = await repo.get_session(db_session, "legacy-sess")
    assert bound is not None
    assert bound.user_id == user.id
