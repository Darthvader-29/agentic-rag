"""Phase 7: entity-extraction parsing (mocked LLM, no DB) + knowledge-graph store (DB-gated).

The extraction tests inject a fake completion so no Gemini call is made; the graph-store tests use
a real sessionmaker on the test engine (skip without TEST_DATABASE_URL) with redis=None (no lock
needed single-threaded), a parent ``sessions`` row for the FK, unique ids, and self-cleanup.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from config import Settings
from database.models import Session, User
from memory.extract import _parse_triples, extract_triples
from memory.graph import KnowledgeGraph

# sessions.user_id is NOT NULL; a single reusable owner backs every test session here.
_OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _settings(**overrides) -> Settings:
    return Settings(**overrides)  # required fields come from conftest's dummy env


# ── entity extraction (no DB; injected fake completion) ──────────────────────


@pytest.mark.asyncio
async def test_extract_parses_triples():
    async def fake(model, prompt):
        return '[["Ada", "wrote", "Notes"], ["Notes", "covers", "Math"]]'

    triples = await extract_triples(
        "text", _settings(ENTITY_EXTRACTION_ENABLED=True), complete=fake
    )
    assert ("Ada", "wrote", "Notes") in triples
    assert ("Notes", "covers", "Math") in triples


@pytest.mark.asyncio
async def test_extract_falls_back_on_model_failure():
    calls: list[str] = []

    async def fake(model, prompt):
        calls.append(model)
        if model == "m1":
            raise RuntimeError("boom")
        return '[["A", "r", "B"]]'

    triples = await extract_triples(
        "text",
        _settings(ENTITY_EXTRACTION_ENABLED=True, ENTITY_EXTRACTION_MODELS=["m1", "m2"]),
        complete=fake,
    )
    assert calls == ["m1", "m2"]  # tried m1, fell through to m2
    assert ("A", "r", "B") in triples


@pytest.mark.asyncio
async def test_extract_disabled_returns_empty():
    async def fake(model, prompt):
        return '[["A", "r", "B"]]'

    out = await extract_triples("text", _settings(ENTITY_EXTRACTION_ENABLED=False), complete=fake)
    assert out == []


@pytest.mark.asyncio
async def test_extract_strips_code_fence():
    async def fenced(model, prompt):
        return '```json\n[["A", "r", "B"]]\n```'

    out = await extract_triples("t", _settings(ENTITY_EXTRACTION_ENABLED=True), complete=fenced)
    assert ("A", "r", "B") in out


def test_parse_triples_rejects_malformed():
    assert _parse_triples("not json at all") == []
    assert _parse_triples('[["only", "two"]]') == []  # wrong arity
    assert _parse_triples('[["a", "", "b"]]') == []  # empty relation dropped


# ── knowledge graph store (DB-gated) ─────────────────────────────────────────


@pytest_asyncio.fixture
async def factory(_engine):
    return async_sessionmaker(_engine, expire_on_commit=False)


async def _mk_session(factory, sid: str) -> None:
    async with factory() as db:
        # Idempotent owner insert so repeated/cross-file tests share the one row.
        await db.execute(
            pg_insert(User)
            .values(
                id=_OWNER_ID,
                email="memtests@t.local",
                username="memtests_owner",
                hashed_password="x",
                is_guest=True,
            )
            .on_conflict_do_nothing()
        )
        db.add(Session(id=sid, user_id=_OWNER_ID))
        await db.commit()


async def _cleanup(factory, sid: str) -> None:
    async with factory() as db:
        await db.execute(delete(Session).where(Session.id == sid))
        await db.commit()


@pytest.mark.asyncio
async def test_graph_add_and_neighbors(factory):
    sid = "kg-add"
    await _mk_session(factory, sid)
    kg = KnowledgeGraph(factory, redis=None)
    try:
        await kg.add_entities(sid, "d1", [("Ada", "wrote", "Notes")])
        assert "Notes" in await kg.neighbors(sid, ["Ada"], hops=1)
    finally:
        await _cleanup(factory, sid)


@pytest.mark.asyncio
async def test_graph_merge_does_not_overwrite(factory):
    sid = "kg-merge"
    await _mk_session(factory, sid)
    kg = KnowledgeGraph(factory, redis=None)
    try:
        await kg.add_entities(sid, "d1", [("A", "rel", "B")])
        await kg.add_entities(sid, "d2", [("C", "rel", "D")])
        reached = set(await kg.neighbors(sid, ["A", "C"], hops=1))
        assert {"A", "B", "C", "D"} <= reached
    finally:
        await _cleanup(factory, sid)


@pytest.mark.asyncio
async def test_graph_export_is_node_link(factory):
    sid = "kg-export"
    await _mk_session(factory, sid)
    kg = KnowledgeGraph(factory, redis=None)
    try:
        await kg.add_entities(sid, "d1", [("A", "rel", "B")])
        data = await kg.export(sid)
        assert "nodes" in data and "links" in data  # frontend / react-force-graph shape
        assert {"A", "B"} <= {n["id"] for n in data["nodes"]}
    finally:
        await _cleanup(factory, sid)


@pytest.mark.asyncio
async def test_graph_neighbors_empty_for_unknown_session(factory):
    kg = KnowledgeGraph(factory, redis=None)
    assert await kg.neighbors("kg-nonexistent", ["X"]) == []


@pytest.mark.asyncio
async def test_graph_acquires_and_releases_redis_lock(factory):
    """add_entities takes the per-session Redis lock around load-merge-save (no lost updates)."""
    sid = "kg-lock"
    await _mk_session(factory, sid)
    events: list = []

    class _FakeLock:
        async def acquire(self):
            events.append("acquire")
            return True  # redis-py Lock.acquire() returns whether it was acquired

        async def release(self):
            events.append("release")

    class _FakeRedis:
        def lock(self, name, timeout=None, blocking_timeout=None):
            events.append(("lock", name))
            return _FakeLock()

    kg = KnowledgeGraph(factory, _FakeRedis())
    try:
        await kg.add_entities(sid, "d1", [("A", "r", "B")])
        assert events[0] == ("lock", f"kg:lock:{sid}")
        assert "acquire" in events and "release" in events
    finally:
        await _cleanup(factory, sid)


# ── ingestion entity-extraction pass (worker/_maybe_extract_graph; mocked) ───


@pytest.mark.asyncio
async def test_maybe_extract_graph_runs_when_enabled(monkeypatch):
    from worker.tasks import _maybe_extract_graph

    captured: dict = {}

    async def fake_extract(text, settings, **kw):
        return [("A", "r", "B")]

    class _FakeKG:
        def __init__(self, *a, **k):
            pass

        async def add_entities(self, session_id, doc_id, triples):
            captured["triples"] = triples
            return len(triples)

    class _FakeRedis:
        async def aclose(self):
            captured["closed"] = True

    monkeypatch.setattr("memory.extract.extract_triples", fake_extract)
    monkeypatch.setattr("memory.graph.KnowledgeGraph", _FakeKG)
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _FakeRedis())

    await _maybe_extract_graph(
        "d1", "s1", "some document text", object(), Settings(ENTITY_EXTRACTION_ENABLED=True)
    )
    assert captured["triples"] == [("A", "r", "B")]
    assert captured["closed"] is True  # redis client always closed


@pytest.mark.asyncio
async def test_maybe_extract_graph_skips_when_disabled():
    from worker.tasks import _maybe_extract_graph

    # disabled → returns immediately, builds no redis/graph (must not raise)
    await _maybe_extract_graph(
        "d1", "s1", "text", object(), Settings(ENTITY_EXTRACTION_ENABLED=False)
    )
