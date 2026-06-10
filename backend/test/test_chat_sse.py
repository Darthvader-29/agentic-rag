"""Phase 6: SSE framing helper + dual-transport /api/chat (JSON + text/event-stream).

The streaming tests drive a FAKE compiled graph (overriding ``get_graph``) so no network or
real LLM runs; they assert the exact SSE event sequence and that concatenated ``token`` events
equal the final ``answer``. Auth + rate-limit are asserted to gate BEFORE the stream opens.
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest

from app import app
from auth.dependencies import get_current_user
from database.models import User
from dependencies import (
    get_db_session,
    get_embedding_client,
    get_graph,
    get_pinecone_client,
    get_web_search_client,
)
from llm.dependencies import get_llm_provider
from sse import sse_event

# ── sse_event framing (pure unit) ─────────────────────────────────────────────


def test_sse_event_frames_event_and_json_data():
    out = sse_event("token", {"text": "hi"})
    assert out == 'event: token\ndata: {"text": "hi"}\n\n'


def test_sse_event_terminates_with_blank_line():
    out = sse_event("done", {"answer": "a", "route": "DIRECT"})
    assert out.startswith("event: done\n")
    assert out.endswith("\n\n")
    # the data line is valid JSON
    data_line = out.split("\n", 1)[1][len("data: ") :].split("\n", 1)[0]
    assert json.loads(data_line) == {"answer": "a", "route": "DIRECT"}


# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    return u


def _fake_sessionmaker(session=None):
    """A sessionmaker() whose context manager yields an AsyncMock session (no real DB)."""
    sess = session or AsyncMock()

    class _Maker:
        def __call__(self):
            cm = AsyncMock()
            cm.__aenter__.return_value = sess
            cm.__aexit__.return_value = False
            return cm

    return _Maker()


def _override_clients():
    """The chat endpoint still resolves the client Depends even when the graph is faked;
    give them harmless AsyncMocks so ASGITransport (no lifespan → no app.state) works. Also
    installs a fake ``db_sessionmaker`` on app.state (the streaming path opens one to persist)."""
    app.dependency_overrides[get_pinecone_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embedding_client] = lambda: AsyncMock()
    app.dependency_overrides[get_web_search_client] = lambda: AsyncMock()
    if not hasattr(app.state, "db_sessionmaker"):
        app.state.db_sessionmaker = _fake_sessionmaker()


class _FakeGraph:
    """A stand-in for the compiled LangGraph.

    ``astream`` replays a fixed (mode, chunk) tuple sequence mirroring BE-1's contract:
    supervisor update (carries route) → vector update → token customs → synthesis update.
    ``ainvoke`` returns a final-state dict for the JSON path.
    """

    def __init__(self, *, route="RAG", tokens=("Hello", " world"), answer="Hello world"):
        self.route = route
        self.tokens = tokens
        self.answer = answer
        self.astream_calls: list[dict] = []
        self.ainvoke_calls: list[dict] = []

    async def astream(self, state, *, stream_mode, config):
        self.astream_calls.append({"stream_mode": stream_mode, "config": config})
        yield ("updates", {"supervisor": {"route": self.route}})
        yield ("updates", {"vector": {"vector_result": "ctx"}})
        for tok in self.tokens:
            yield ("custom", {"kind": "token", "text": tok})
        yield (
            "custom",
            {"kind": "component", "data": {"type": "callout", "level": "info", "text": "n"}},
        )
        yield ("updates", {"synthesis": {"answer": self.answer}})

    async def ainvoke(self, state):
        self.ainvoke_calls.append(state)
        return {
            "answer": self.answer,
            "route": self.route,
            "context": "some-context",
            "components": [],
        }


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse a raw SSE body into a list of (event, data-dict)."""
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            events.append((event, data))
    return events


# ── streaming transport ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_sse_emits_status_token_done_sequence():
    fake_graph = _FakeGraph(route="RAG", tokens=("Hello", " world"), answer="Hello world")
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=True),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "text/event-stream",
                    },
                )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]

        # status fires for supervisor (routing) then vector (retrieving), before tokens
        assert kinds[0] == "status"
        assert events[0][1] == {"stage": "routing"}
        assert ("status", {"stage": "retrieving"}) in events
        # tokens stream, then a component, then done
        token_events = [d["text"] for e, d in events if e == "token"]
        assert token_events == ["Hello", " world"]
        assert ("component", {"type": "callout", "level": "info", "text": "n"}) in events
        assert kinds[-1] == "done"
        done_payload = events[-1][1]
        # concatenated tokens equal the final answer
        assert done_payload["answer"] == "".join(token_events) == "Hello world"
        assert done_payload["route"] == "RAG"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_sse_persists_turn_via_fresh_session():
    """Streaming path persists user + assistant turns from a fresh sessionmaker, not request db."""
    # tokens spell the answer — the streaming path persists the concatenated tokens verbatim
    fake_graph = _FakeGraph(tokens=("persisted", " answer"), answer="persisted answer")
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()

    fresh_session = AsyncMock()
    saved_sessionmaker = getattr(app.state, "db_sessionmaker", None)
    app.state.db_sessionmaker = _fake_sessionmaker(fresh_session)
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock) as save_msg,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "text/event-stream",
                    },
                )
        assert resp.status_code == 200
        # both user and assistant turns saved on the fresh session
        roles = [c.kwargs["role"] for c in save_msg.await_args_list]
        assert roles == ["user", "assistant"]
        contents = {c.kwargs["role"]: c.kwargs["content"] for c in save_msg.await_args_list}
        assert contents["user"] == "hi"
        assert contents["assistant"] == "persisted answer"
        fresh_session.commit.assert_awaited()
    finally:
        app.dependency_overrides.clear()
        if saved_sessionmaker is not None:
            app.state.db_sessionmaker = saved_sessionmaker


@pytest.mark.asyncio
async def test_chat_sse_commits_session_before_stream_persists():
    """Regression (B01): the request-scoped session is committed BEFORE the streaming generator's
    fresh-session writes run.

    The generator persists the turn (and markdown memory) from its OWN session, opened after this
    endpoint returns. If a brand-new ``sessions`` row were only flushed (committed on dependency
    teardown, which for a StreamingResponse runs AFTER the body finishes), those fresh-session
    INSERTs would FK-violate against the uncommitted parent and the first turn would be silently
    lost. We assert the request db commit is ordered before the first ``save_message``.
    """
    order: list[str] = []

    fake_graph = _FakeGraph(tokens=("hi",), answer="hi")
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()

    # Request-scoped db: a dedicated session whose commit records its order in `order`.
    request_db = AsyncMock()

    async def _req_commit():
        order.append("request_db.commit")

    request_db.commit.side_effect = _req_commit

    async def _override_db():
        yield request_db

    app.dependency_overrides[get_db_session] = _override_db

    # Fresh persist session (opened by _persist_turn) — distinct from the request db.
    persist_session = AsyncMock()
    saved_sessionmaker = getattr(app.state, "db_sessionmaker", None)
    app.state.db_sessionmaker = _fake_sessionmaker(persist_session)

    async def _save_message(session, **kwargs):
        order.append(f"save_message:{kwargs['role']}")

    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", side_effect=_save_message),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "new-sid", "web_search_allowed": False},
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "text/event-stream",
                    },
                )
        assert resp.status_code == 200
        # the request-scoped session committed (making the new session row durable) ...
        assert "request_db.commit" in order
        # ... strictly BEFORE the fresh-session turn writes ran.
        first_commit = order.index("request_db.commit")
        first_save = next(i for i, e in enumerate(order) if e.startswith("save_message"))
        assert first_commit < first_save, f"commit must precede persistence; order={order}"
    finally:
        app.dependency_overrides.clear()
        if saved_sessionmaker is not None:
            app.state.db_sessionmaker = saved_sessionmaker


@pytest.mark.asyncio
async def test_chat_json_path_returns_components():
    """Regression (B06): the blocking JSON path carries the graph's parsed rich components
    (previously dropped, so flipping streaming OFF lost every table/chart/citation block)."""
    comps = [{"type": "callout", "level": "info", "text": "n"}, {"type": "table", "rows": []}]

    class _GraphWithComponents:
        async def ainvoke(self, state):
            return {
                "answer": "see below",
                "route": "RAG",
                "context": "",
                "components": comps,
                "layers": [],
            }

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: _GraphWithComponents()
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={"Authorization": "Bearer test-token"},  # no Accept → JSON path
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["components"] == comps
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_json_path_maps_both_route_to_web_rag():
    """Regression (B09): the graph's flat BOTH route is mapped to WEB+RAG on the JSON path so it
    satisfies the frontend routeTypeSchema (which has no BOTH) instead of failing as an error turn."""

    class _GraphBothRoute:
        async def ainvoke(self, state):
            return {"answer": "a", "route": "BOTH", "context": "", "components": [], "layers": []}

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: _GraphBothRoute()
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": True},
                    headers={"Authorization": "Bearer test-token"},  # no Accept → JSON path
                )
        assert resp.status_code == 200
        assert resp.json()["route"] == "WEB+RAG"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_rejects_overlong_session_id():
    """B16: a session_id longer than Session.id's String(64) is a clean 422, not a 500 from a DB
    StringDataRightTruncation at flush."""
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: _FakeGraph()
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/chat",
                json={"message": "hi", "session_id": "x" * 65, "web_search_allowed": False},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_json_infra_error_is_generic_not_free_tier():
    """B18: a non-quota failure (e.g. Pinecone/DB outage) on the JSON path is a generic 500 —
    NOT mislabeled 'free tier Limit Reached', which misled paid BYOK users."""

    class _BoomGraph:
        async def ainvoke(self, state):
            raise RuntimeError("pinecone unavailable")

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: _BoomGraph()
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={"Authorization": "Bearer test-token"},  # no Accept → JSON path
                )
        assert resp.status_code == 500
        assert "free tier" not in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_sse_disconnect_suppresses_done_and_still_persists():
    """B19: when the client is gone, the stream does NOT push a `done` event, and the turn is still
    persisted (the old `await` in `finally` lost it on a real disconnect)."""
    fake_graph = _FakeGraph(tokens=("hi",), answer="hi")
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()

    fresh = AsyncMock()
    saved = getattr(app.state, "db_sessionmaker", None)
    app.state.db_sessionmaker = _fake_sessionmaker(fresh)
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock) as save_msg,
            patch.object(
                StarletteRequest, "is_disconnected", new_callable=AsyncMock, return_value=True
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "text/event-stream",
                    },
                )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        # no `done` pushed at a client that already disconnected
        assert all(e != "done" for e, _ in events), events
        # ...but the turn is still persisted (user message at minimum)
        assert "user" in [c.kwargs["role"] for c in save_msg.await_args_list]
    finally:
        app.dependency_overrides.clear()
        if saved is not None:
            app.state.db_sessionmaker = saved


@pytest.mark.asyncio
async def test_spawn_persist_runs_detached():
    """B19: the cancel/close path schedules a detached persist that completes on the loop."""
    import app as app_module

    fresh = AsyncMock()
    maker = _fake_sessionmaker(fresh)
    registry: set = set()  # local ref keeps the detached task alive
    with patch("app.repo.save_message", new_callable=AsyncMock) as save_msg:
        app_module._spawn_persist(registry, maker, "s1", "hi", "answer")
        for _ in range(5):  # let the detached task run to completion
            await asyncio.sleep(0)
    assert [c.kwargs["role"] for c in save_msg.await_args_list] == ["user", "assistant"]
    fresh.commit.assert_awaited()


# ── auth + rate-limit gate BEFORE the stream opens ────────────────────────────


@pytest.mark.asyncio
async def test_chat_sse_requires_auth_before_streaming():
    """No bearer token → 401 (JSON error), never an SSE stream."""
    # get_current_user is NOT overridden → real auth dependency runs and 401s
    app.dependency_overrides[get_graph] = lambda: _FakeGraph()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/chat",
                json={"message": "hi", "web_search_allowed": False},
                headers={"Accept": "text/event-stream"},
            )
        assert resp.status_code == 401
        assert not resp.headers["content-type"].startswith("text/event-stream")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_sse_rate_limit_gates_before_stream():
    """Past RATE_LIMIT_CHAT the SSE request is rejected with 429, before any stream opens."""
    fake_graph = _FakeGraph()
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_llm_provider] = lambda: AsyncMock()
    _override_clients()
    try:
        with (
            patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
            patch("app.repo.create_session", new_callable=AsyncMock),
            patch("app.repo.session_has_documents", new_callable=AsyncMock, return_value=False),
            patch("app.repo.load_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("app.repo.save_message", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                statuses = []
                for _ in range(40):
                    r = await client.post(
                        "/api/chat",
                        json={"message": "hi", "session_id": "s1", "web_search_allowed": False},
                        headers={
                            "Authorization": "Bearer test-token",
                            "Accept": "text/event-stream",
                        },
                    )
                    statuses.append(r.status_code)
        assert 429 in statuses
    finally:
        app.dependency_overrides.clear()
