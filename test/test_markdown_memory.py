"""Phase 7: per-session markdown memory store (DB-gated — skips without TEST_DATABASE_URL).

MarkdownMemory opens its own session per call, so these use a real sessionmaker on the test engine
(not the rolled-back db_session fixture). A parent ``sessions`` row is created first to satisfy the
FK; unique session ids per test keep them independent, and each cleans up after itself.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from database.models import Session
from memory.markdown import MarkdownMemory


@pytest_asyncio.fixture
async def factory(_engine):
    return async_sessionmaker(_engine, expire_on_commit=False)


async def _make_session(factory, sid: str) -> None:
    async with factory() as db:
        db.add(Session(id=sid))
        await db.commit()


async def _cleanup(factory, sid: str) -> None:
    async with factory() as db:
        await db.execute(delete(Session).where(Session.id == sid))
        await db.commit()


@pytest.mark.asyncio
async def test_append_then_read(factory):
    sid = "mem-append-read"
    await _make_session(factory, sid)
    mem = MarkdownMemory(factory, max_chars=8000)
    try:
        await mem.append(sid, "User asked about X.")
        assert "User asked about X." in await mem.read(sid)
        await mem.append(sid, "Then about Y.")
        content = await mem.read(sid)
        assert "User asked about X." in content and "Then about Y." in content
    finally:
        await _cleanup(factory, sid)


@pytest.mark.asyncio
async def test_append_is_bounded(factory):
    sid = "mem-bounded"
    await _make_session(factory, sid)
    mem = MarkdownMemory(factory, max_chars=20)
    try:
        await mem.append(sid, "x" * 50)
        assert len(await mem.read(sid)) <= 20
    finally:
        await _cleanup(factory, sid)


@pytest.mark.asyncio
async def test_read_missing_returns_empty(factory):
    mem = MarkdownMemory(factory, max_chars=8000)
    assert await mem.read("mem-does-not-exist") == ""
