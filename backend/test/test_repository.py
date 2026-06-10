"""Repository-layer tests against the real NeonDB TestDB.

Requires TEST_DATABASE_URL in the environment. The conftest.py _engine fixture
creates/drops tables around the session; each test rolls back its own transaction.
"""

import uuid

import pytest
import pytest_asyncio

from auth.security import hash_password
from database import repository as repo
from database.models import DocumentStatus
from database.repository import LLMKeyRepository, UserRepository

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def owner(db_session):
    """A persisted user to own sessions created in these tests (sessions.user_id is NOT NULL)."""
    return await UserRepository(db_session).create(
        email=f"owner-{uuid.uuid4().hex}@t.com",
        username=f"owner_{uuid.uuid4().hex[:12]}",
        hashed_password=hash_password("pw"),
    )


async def test_session_has_documents_false_when_empty(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    assert await repo.session_has_documents(db_session, "s1") is False


async def test_create_document_and_has_documents(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/a", filename="a.pdf")
    assert await repo.session_has_documents(db_session, "s1") is True
    assert await repo.list_s3_keys_for_session(db_session, "s1") == ["uploads/a"]


async def test_set_document_status_to_ready(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/a", filename="a.pdf")
    await repo.set_document_status(db_session, s3_key="uploads/a", status=DocumentStatus.READY)
    keys = await repo.list_s3_keys_for_session(db_session, "s1")
    assert keys == ["uploads/a"]


async def test_set_document_status_to_failed(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/b", filename="b.pdf")
    await repo.set_document_status(db_session, s3_key="uploads/b", status=DocumentStatus.FAILED)
    keys = await repo.list_s3_keys_for_session(db_session, "s1")
    assert keys == ["uploads/b"]


async def test_list_s3_keys_multiple_docs(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/c", filename="c.pdf")
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/d", filename="d.pdf")
    keys = await repo.list_s3_keys_for_session(db_session, "s1")
    assert sorted(keys) == ["uploads/c", "uploads/d"]


async def test_delete_session_cascades_documents(db_session, owner):
    await repo.get_or_create_session(db_session, "s1", owner.id)
    await repo.create_document(db_session, session_id="s1", s3_key="uploads/a", filename="a.pdf")
    await repo.delete_session(db_session, "s1")
    assert await repo.session_has_documents(db_session, "s1") is False
    assert await repo.list_s3_keys_for_session(db_session, "s1") == []


async def test_get_or_create_session_is_idempotent(db_session, owner):
    """Calling get_or_create_session twice must not raise."""
    await repo.get_or_create_session(db_session, "idempotent-session", owner.id)
    await repo.get_or_create_session(db_session, "idempotent-session", owner.id)
    assert await repo.session_has_documents(db_session, "idempotent-session") is False


# ── Phase 6: conversation history (save_message / load_recent_messages) ────────


async def test_load_recent_messages_empty_session(db_session, owner):
    await repo.get_or_create_session(db_session, "hist-empty", owner.id)
    rows = await repo.load_recent_messages(db_session, session_id="hist-empty", limit=6)
    assert rows == []


async def test_save_and_load_messages_roundtrip(db_session, owner):
    """save_message persists and load_recent_messages returns every turn for the session.

    NOTE: ordering within a single transaction is timestamp-driven; turns saved in the same
    microsecond may tie (id tiebreak is a random UUID), so this asserts the SET of turns, not
    intra-tie order. Cross-turn ordering (distinct created_at) is exercised by the app-level
    memory-wiring tests.
    """
    await repo.get_or_create_session(db_session, "hist-1", owner.id)
    await repo.save_message(db_session, session_id="hist-1", role="user", content="q1")
    await repo.save_message(db_session, session_id="hist-1", role="assistant", content="a1")
    await repo.save_message(db_session, session_id="hist-1", role="user", content="q2")

    rows = await repo.load_recent_messages(db_session, session_id="hist-1", limit=6)
    assert {(r.role, r.content) for r in rows} == {
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
    }


async def test_load_recent_messages_limit_caps_count(db_session, owner):
    """The limit caps how many turns come back (the newest window)."""
    await repo.get_or_create_session(db_session, "hist-2", owner.id)
    for i in range(5):
        await repo.save_message(db_session, session_id="hist-2", role="user", content=f"m{i}")
    rows = await repo.load_recent_messages(db_session, session_id="hist-2", limit=2)
    assert len(rows) == 2
    # all returned turns are real saved turns
    assert all(r.content.startswith("m") for r in rows)


# ── Phase 4/6: get_user_llm_key (first stored key) ────────────────────────────


async def test_get_user_llm_key_none_when_no_keys(db_session):
    user = await UserRepository(db_session).create(
        email="nokey@test.com", username="nokeyuser", hashed_password=hash_password("pass")
    )
    assert await repo.get_user_llm_key(db_session, user_id=user.id) is None


async def test_get_user_llm_key_returns_stored_row(db_session):
    user = await UserRepository(db_session).create(
        email="haskey@test.com", username="haskeyuser", hashed_password=hash_password("pass")
    )
    await LLMKeyRepository(db_session).upsert(user_id=user.id, provider="gemini", ciphertext="ct")
    row = await repo.get_user_llm_key(db_session, user_id=user.id)
    assert row is not None
    assert row.provider == "gemini"


async def test_user_repository_get_invalid_uuid_returns_none(db_session):
    """A non-UUID subject string must resolve to None, not raise."""
    assert await UserRepository(db_session).get("not-a-uuid") is None
    # a well-formed but absent UUID also returns None
    assert await UserRepository(db_session).get(str(uuid.uuid4())) is None
