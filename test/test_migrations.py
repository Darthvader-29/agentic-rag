"""Alembic migration integration test.

Runs against the real NeonDB TestDB (TEST_DATABASE_URL). Starts from a clean schema,
applies all Phase 2 + Phase 3 migrations via Alembic, then verifies the expected tables
exist.

NOTE: The test drops all tables before running migrations so it is self-contained and
independent of the session-scoped _engine fixture that other DB tests use. After the
migrations run, the tables remain at head state for subsequent db_session tests.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.pool import NullPool


def test_alembic_upgrade_head_creates_schema():
    raw = os.environ.get("TEST_DATABASE_URL", "")
    if not raw:
        pytest.skip("TEST_DATABASE_URL not set — skipping migration test")

    from sqlalchemy.ext.asyncio import create_async_engine

    from database.models import Base
    from database.session import _to_asyncpg_url

    url, connect_args = _to_asyncpg_url(raw)
    cfg = Config("alembic.ini")
    os.environ["DATABASE_URL"] = raw

    try:
        # Start from a fully clean state: drop all model tables + alembic_version
        async def _clean() -> None:
            engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await engine.dispose()

        asyncio.run(_clean())

        # Run all migrations from base → head
        command.upgrade(cfg, "head")

        # Verify all expected tables are present
        async def _check() -> set:
            engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' "
                        "AND table_name IN "
                        "('sessions', 'documents', 'users', 'user_llm_keys', "
                        "'messages', 'session_memory')"
                    )
                )
                tables = {row[0] for row in result}
            await engine.dispose()
            return tables

        tables = asyncio.run(_check())
        assert {
            "sessions",
            "documents",
            "users",
            "user_llm_keys",
            "messages",
            "session_memory",
        } <= tables, f"Missing expected tables after upgrade head: {tables}"

    finally:
        # Leave DB at head state — do NOT downgrade. Subsequent db_session tests will
        # use the tables created by this migration run.
        os.environ.pop("DATABASE_URL", None)
