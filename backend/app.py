import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from agents.graph import build_graph
from agents.state import GraphState, Turn
from auth.dependencies import get_current_user
from auth.keys_router import router as keys_router
from auth.router import router as auth_router
from auth.security import decode_token
from config import settings
from database import repository as repo
from database.db_manager import PineconeClient
from database.models import DocumentStatus, User
from database.session import build_engine, build_sessionmaker
from dependencies import (
    get_db_session,
    get_embedding_client,
    get_graph,
    get_knowledge_graph,
    get_markdown_memory,
    get_pinecone_client,
    get_s3_client,
    get_web_search_client,
)
from exceptions import AppException, app_exception_handler
from integrations.duckduckgo.client import DuckDuckGoClient
from integrations.huggingface.client import HuggingFaceClient
from integrations.s3.client import S3Client
from llm.base import LLMProvider
from llm.dependencies import get_llm_provider
from logging_config import configure_logging
from memory.graph import KnowledgeGraph
from memory.hybrid import HybridRetriever
from memory.markdown import MarkdownMemory
from observability.langfuse import init_langfuse
from observability.tracing import get_tracer, init_tracing
from sse import sse_event
from worker.tasks import ingest_document

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.pinecone = PineconeClient.from_settings(settings)
    await app.state.pinecone.ensure_index()
    app.state.s3 = S3Client.from_settings(settings)
    app.state.embedder = HuggingFaceClient.from_settings(settings)
    app.state.web = DuckDuckGoClient()
    app.state.db_engine = build_engine(settings)
    app.state.db_sessionmaker = build_sessionmaker(app.state.db_engine)
    if settings.OTEL_ENABLED:
        # SQLAlchemy spans need the engine; instrument the sync engine behind the async one.
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=app.state.db_engine.sync_engine)
    # Phase 7: per-session markdown memory store (wraps the fresh sessionmaker — safe mid-stream)
    app.state.markdown_memory = MarkdownMemory(
        app.state.db_sessionmaker, settings.MEMORY_MARKDOWN_MAX_CHARS
    )
    # Phase 5: one pooled Redis client per process (lazy from_url — no network here)
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    # Phase 7: knowledge graph (Redis-locked) + hybrid retriever (vector+graph+markdown) + Langfuse
    app.state.knowledge_graph = KnowledgeGraph(app.state.db_sessionmaker, app.state.redis)
    app.state.hybrid_retriever = HybridRetriever(
        app.state.knowledge_graph,
        app.state.markdown_memory,
        {
            "vector": settings.HYBRID_WEIGHTS_VECTOR,
            "graph": settings.HYBRID_WEIGHTS_GRAPH,
            "markdown": settings.HYBRID_WEIGHTS_MARKDOWN,
        },
    )
    init_langfuse(settings)
    # Phase 6: compile the agentic chat graph ONCE per process (pure + stateless — shared)
    app.state.graph = build_graph()
    logger.info("clients_initialized", environment=settings.ENVIRONMENT)
    yield
    await app.state.redis.aclose()
    await app.state.db_engine.dispose()


app = FastAPI(
    title="Dynamic Knowledge RAG Engine",
    version="1.0.0",
    description="Multi-agent RAG with Pinecone, S3, and Gemini",
    lifespan=lifespan,
)

app.add_exception_handler(AppException, app_exception_handler)

# ── Phase 7: OpenTelemetry (gated). Auto-instrumentation + OTLP export only when OTEL_ENABLED;
# the explicit chat.request / agent.* / memory.* / ingest.document spans are emitted
# unconditionally (a no-op tracer when disabled) so trace-emission tests can capture them. The
# SSE path is covered by the FastAPI ASGI span (it stays open until the stream finishes), so the
# explicit chat.request span lives on the JSON path only — avoiding a span that straddles an async
# generator's yields.
init_tracing(settings)
if settings.OTEL_ENABLED:
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    CeleryInstrumentor().instrument()  # API side: inject trace context into enqueued tasks

# Phase 3: explicit allow-list — "*" + allow_credentials is rejected by browsers
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Phase 5: per-user rate limiting (Redis-backed in prod, memory:// in tests) ──
def _rate_limit_key(request: Request) -> str:
    """Throttle per authenticated user so one tenant can't drain another's budget;
    fall back to client IP for anonymous traffic. The bearer is decoded best-effort —
    an invalid/expired token falls through to IP (the route's auth dependency still 401s)."""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            sub = decode_token(auth[7:]).get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)


# storage_uri resolves to REDIS_URL in prod (shared across instances) and "memory://"
# in tests. Per-route @limiter.limit decorators below; no global default → /health is exempt.
limiter = Limiter(key_func=_rate_limit_key, storage_uri=settings.rate_limit_storage_uri)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Phase 3: auth + key management routers
app.include_router(auth_router)
app.include_router(keys_router)


# ========= MODELS =========


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    web_search_allowed: bool = True
    # M7: optional per-conversation provider/model pick from the chat picker. Honored only for a
    # BYOK user who holds a key for the chosen provider (model → synthesis model); ignored for the
    # free tier. Resolution lives in llm/dependencies.get_llm_provider → resolve_provider.
    provider: str | None = None
    model: str | None = None


class CleanupRequest(BaseModel):
    session_id: str


class UploadResponse(BaseModel):
    status: str
    message: str
    session_id: str
    s3_key: str


# ── Phase 5: presigned upload (frontend M8 contract) ──
class PresignRequest(BaseModel):
    filename: str
    content_type: str | None = None
    session_id: str | None = None  # optional; a new one is created and returned if absent


class PresignResponse(BaseModel):
    document_id: str
    upload_url: str
    s3_key: str
    session_id: str


class ConfirmRequest(BaseModel):
    document_id: str


class DocumentStatusResponse(BaseModel):
    id: str
    filename: str
    status: str  # pending | processing | ready | failed
    s3_key: str
    session_id: str
    error: str | None = None


# ========= HELPERS FOR COMBINED ROUTING =========


RAG_THRESHOLD = 0.4


async def check_docs_relevant(
    query: str,
    session_id: str,
    pinecone: PineconeClient,
    embedder: HuggingFaceClient,
) -> tuple[bool, bool]:
    """Returns (has_documents, docs_relevant)."""
    try:
        q_emb = await embedder.embed_single(query)
        results = await pinecone.search_vectors(q_emb, top_k=3, session_id=session_id)
        if not results:
            return False, False
        top_score = results[0]["score"]
        docs_relevant = top_score >= RAG_THRESHOLD
        logger.info(
            "pinecone_relevance_check",
            top_score=round(top_score, 3),
            docs_relevant=docs_relevant,
        )
        return True, docs_relevant
    except Exception:
        logger.error("doc_relevance_check_failed", exc_info=True)
        return False, False


def decide_combined_route(
    base_route: str,
    has_documents: bool,
    docs_relevant: bool,
    web_allowed: bool,
) -> str:
    """Combine base route (RAG/WEB/DIRECT) with doc relevance into a final route label."""
    base = base_route.upper()

    if has_documents and docs_relevant:
        if base == "WEB" and web_allowed:
            return "WEB+RAG"
        if base == "DIRECT":
            return "DIRECT+RAG"
        return "RAG"

    if web_allowed:
        return "DIRECT+WEB"

    return "DIRECT"


# ========= UPLOAD + INGEST =========


def _session_accessible(session, current_user: User) -> bool:
    """The shared ownership predicate: the session exists and is owned by the caller.

    Unowned (user_id IS NULL) sessions are intentionally NOT accessible. Every session is created
    with an owner (``_resolve_session`` / ``create_session``), so a NULL owner can only be legacy
    pre-auth data — we refuse it rather than letting any caller read or silently claim it
    (tenant-isolation fix). Callers layer their own outward behavior (return bool / 404 / 403).
    """
    return session is not None and session.user_id == current_user.id


async def _resolve_session(db: AsyncSession, session_id: str | None, current_user: User) -> str:
    """Create or verify a session owned by the current user; return the session_id."""
    sid = session_id or str(uuid.uuid4())
    existing = await repo.get_session(db, sid)
    if existing is None:
        await repo.create_session(db, sid, current_user.id)
    elif existing.user_id != current_user.id:
        # Includes the legacy NULL-owner case: we refuse it (403) instead of auto-claiming, so a
        # guessed/leaked session_id can never be adopted by another tenant (tenant-isolation fix).
        raise HTTPException(403, "session does not belong to the current user")
    return sid


async def _owns_document(db: AsyncSession, doc, current_user: User) -> bool:
    """A document is the caller's if its session is unowned or owned by the caller."""
    session = await repo.get_session(db, doc.session_id)
    return _session_accessible(session, current_user)


async def _upload_multipart(request: Request, current_user: User, s3: S3Client, db: AsyncSession):
    """Legacy flag-OFF path: bytes pass through the API, then ingestion is enqueued."""
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "filename"):
        raise HTTPException(422, "missing file")
    session_id = await _resolve_session(db, form.get("session_id"), current_user)
    filename = file.filename or "upload"
    s3_key = await s3.upload_fileobj(file.file, filename)
    doc = await repo.create_document(db, session_id=session_id, s3_key=s3_key, filename=filename)
    await db.commit()  # persist before enqueue so a separate worker can read the row
    ingest_document.delay(
        document_id=doc.id,
        s3_key=s3_key,
        filename=filename,
        session_id=session_id,
        user_id=str(current_user.id),
    )
    return UploadResponse(
        status="processing",
        message=f"{filename} uploaded and ingestion started.",
        session_id=session_id,
        s3_key=s3_key,
    )


async def _upload_presign(request: Request, current_user: User, s3: S3Client, db: AsyncSession):
    """Presigned flag-ON path: issue a PUT URL; the client uploads direct to storage."""
    payload = PresignRequest.model_validate(await request.json())
    session_id = await _resolve_session(db, payload.session_id, current_user)
    s3_key = s3.make_user_key(current_user.id, payload.filename)
    upload_url = await s3.generate_presigned_url(s3_key)
    doc = await repo.create_document(
        db, session_id=session_id, s3_key=s3_key, filename=payload.filename
    )
    await db.commit()
    return PresignResponse(
        document_id=doc.id, upload_url=upload_url, s3_key=s3_key, session_id=session_id
    )


@app.post("/api/upload")
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload(
    request: Request,
    current_user: User = Depends(get_current_user),
    s3: S3Client = Depends(get_s3_client),
    db: AsyncSession = Depends(get_db_session),
):
    """Two transports on one path (the frontend's M8 flag picks one):

    - ``multipart/form-data`` → legacy passthrough upload (bytes via the API), then enqueue.
    - ``application/json``     → presigned PUT: ``{document_id, upload_url, s3_key, session_id}``.

    Both record a Postgres ``documents`` row and route ingestion through the Celery worker.
    """
    try:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            return await _upload_multipart(request, current_user, s3, db)
        return await _upload_presign(request, current_user, s3, db)
    except HTTPException:
        raise
    except Exception as exc:
        raise AppException(status_code=500, detail="Upload failed unexpectedly.") from exc


@app.post("/api/upload/confirm")
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def confirm_upload(
    request: Request,
    payload: ConfirmRequest,
    current_user: User = Depends(get_current_user),
    s3: S3Client = Depends(get_s3_client),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify the presigned object landed (head_object), then enqueue ingestion (M8 step 3)."""
    try:
        doc = await repo.get_document(db, payload.document_id)
        if doc is None or not await _owns_document(db, doc, current_user):
            raise HTTPException(404, "document not found")
        # Derive the S3 key from the OWNED document — never trust a client-supplied key. Trusting
        # payload.s3_key let a caller probe/ingest another tenant's object and flip an arbitrary
        # document's status by its globally-unique s3_key (tenant-isolation fix).
        if not await s3.object_exists(doc.s3_key):
            await repo.set_document_status_by_id(
                db, document_id=doc.id, status=DocumentStatus.FAILED
            )
            # Commit the FAILED status BEFORE raising. The 409 unwinds through get_db_session's
            # ``except: rollback()``, which would otherwise erase this UPDATE and leave the document
            # stuck ``pending`` forever (status pollers never see ``failed``).
            await db.commit()
            raise HTTPException(409, "object not uploaded")
        ingest_document.delay(
            document_id=doc.id,
            s3_key=doc.s3_key,
            filename=doc.filename,
            session_id=doc.session_id,
            user_id=str(current_user.id),
        )
        return {"document_id": doc.id, "status": "queued"}
    except (AppException, HTTPException):
        raise
    except Exception as exc:
        raise AppException(status_code=500, detail="Confirm failed unexpectedly.") from exc


@app.get("/api/documents/{document_id}", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Poll a document's ingestion status (M8 step 4). status ∈ pending|processing|ready|failed."""
    doc = await repo.get_document(db, document_id)
    if doc is None or not await _owns_document(db, doc, current_user):
        raise HTTPException(404, "document not found")
    return DocumentStatusResponse(
        id=doc.id,
        filename=doc.filename,
        status=doc.status.value,
        s3_key=doc.s3_key,
        session_id=doc.session_id,
    )


# ========= PHASE 7: MEMORY + GRAPH (read-only, frontend Insights panels) =========


async def _require_owned_session(db: AsyncSession, session_id: str, current_user: User) -> None:
    """404 unless the session exists and belongs to the caller (or is unowned)."""
    session = await repo.get_session(db, session_id)
    if not _session_accessible(session, current_user):
        raise HTTPException(404, "session not found")


@app.get("/api/sessions/{session_id}/memory")
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def get_session_memory(
    request: Request,
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    markdown: MarkdownMemory = Depends(get_markdown_memory),
):
    """Phase 7: per-session markdown memory (running notes). 404 if not the caller's session."""
    await _require_owned_session(db, session_id, current_user)
    content, updated_at = await markdown.read_with_updated(session_id)
    return {"session_id": session_id, "content": content, "updated_at": updated_at}


@app.get("/api/sessions/{session_id}/graph")
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def get_session_graph(
    request: Request,
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    graph_store: KnowledgeGraph = Depends(get_knowledge_graph),
):
    """Phase 7: per-session knowledge graph as networkx node-link JSON (``{nodes, links}``)."""
    await _require_owned_session(db, session_id, current_user)
    return await graph_store.export(session_id)


# ========= CHAT =========


def _node_stage(node: str) -> str | None:
    """Map a graph node to the coarse SSE ``status`` stage the frontend renders (None to skip).

    A function (not a module-level dict) keeps app.py free of mutable module state — the
    horizontal-scale invariant in test_statelessness: any instance can serve any request.
    """
    return {
        "supervisor": "routing",
        "vector": "retrieving",
        "web": "searching web",
        "synthesis": "synthesizing",
    }.get(node)


def _count_context_chunks(state: dict) -> int:
    """Best-effort count of retrieved chunks for the JSON response (0 when no context).

    ``format_context`` prefixes each chunk with ``CONTEXT N:``; counting those markers across the
    document context and web result yields the chunk count without re-running retrieval. Both keys
    are empty strings when their branch was skipped or found nothing relevant.
    """
    merged = (state.get("context") or "") + "\n" + (state.get("web_result") or "")
    return merged.count("CONTEXT ")


async def _build_graph_state(
    payload: ChatRequest,
    session_id: str,
    current_user: User,
    provider: LLMProvider,
    pinecone: PineconeClient,
    embedder: HuggingFaceClient,
    web: DuckDuckGoClient,
    db: AsyncSession,
) -> GraphState:
    """Assemble the per-request initial GraphState (history + has_documents from Postgres)."""
    has_documents = await repo.session_has_documents(db, session_id)
    rows = await repo.load_recent_messages(
        db, session_id=session_id, limit=settings.HISTORY_MAX_TURNS
    )
    history: list[Turn] = [{"role": r.role, "content": r.content} for r in rows]  # type: ignore[misc]
    return GraphState(
        query=payload.message,
        session_id=session_id,
        user_id=str(current_user.id),
        provider=provider,
        pinecone=pinecone,
        embedder=embedder,
        web=web,
        history=history,
        has_documents=has_documents,
        web_search_allowed=payload.web_search_allowed,
    )


async def _persist_turn(sessionmaker, session_id: str, user_msg: str, answer: str) -> None:
    """Persist the user turn (always) + the assistant turn (only if non-empty) in a fresh session.

    A StreamingResponse generator runs AFTER the endpoint returns, so the request-scoped db may
    already be closing; the streaming path passes ``app.state.db_sessionmaker`` to open its own.
    """
    async with sessionmaker() as session:
        await repo.save_message(session, session_id=session_id, role="user", content=user_msg)
        if answer:
            await repo.save_message(
                session, session_id=session_id, role="assistant", content=answer
            )
        await session.commit()


@app.post("/api/chat")
@limiter.limit(settings.RATE_LIMIT_CHAT)
async def chat(
    request: Request,
    payload: ChatRequest,
    provider: LLMProvider = Depends(get_llm_provider),
    current_user: User = Depends(get_current_user),
    pinecone: PineconeClient = Depends(get_pinecone_client),
    embedder: HuggingFaceClient = Depends(get_embedding_client),
    web: DuckDuckGoClient = Depends(get_web_search_client),
    db: AsyncSession = Depends(get_db_session),
    graph: CompiledStateGraph = Depends(get_graph),
):
    """Dual-transport agentic chat (Phase 6).

    Auth + per-user rate limiting gate every request *before* any work or stream opens. The query
    runs through the compiled LangGraph (supervisor → retrieval → synthesis):

    - ``Accept: text/event-stream`` → token-by-token SSE (``status`` / ``token`` / ``component`` /
      ``done`` / ``error`` events) via ``graph.astream``.
    - otherwise → a single JSON object ``{answer, route, context_count, session_id}`` via
      ``graph.ainvoke``.
    """
    session_id = payload.session_id or str(uuid.uuid4())
    logger.info(
        "chat_request",
        message_preview=payload.message[:50],
        web_search_allowed=payload.web_search_allowed,
        session_id=session_id[:8],
        streaming="text/event-stream" in request.headers.get("accept", ""),
    )

    # Phase 3: ownership check — create session with owner or verify existing ownership.
    session_id = await _resolve_session(db, session_id, current_user)

    state = await _build_graph_state(
        payload, session_id, current_user, provider, pinecone, embedder, web, db
    )
    # Phase 7: thread the memory collaborators into the graph (None-safe for tests)
    state["markdown_memory"] = getattr(request.app.state, "markdown_memory", None)
    state["hybrid_retriever"] = getattr(request.app.state, "hybrid_retriever", None)

    if "text/event-stream" in request.headers.get("accept", ""):
        sessionmaker = request.app.state.db_sessionmaker

        # Commit the resolved session row BEFORE streaming begins. The generator below runs after
        # this endpoint returns, and its turn/markdown writes open their OWN sessions (see
        # _persist_turn / _persist_markdown). If the request-scoped db only flushed a brand-new
        # session row, those fresh sessions can't see it (it commits on dependency teardown, which
        # for a StreamingResponse runs AFTER the body finishes) → the first turn of every new
        # session FK-violates and is silently lost. Committing here makes the row durable first.
        await db.commit()

        async def event_stream():
            seen_stages: set[str] = set()
            tokens: list[str] = []
            route: str | None = None
            layers: list[str] = []
            answered = False
            try:
                async for mode, chunk in graph.astream(
                    state,
                    stream_mode=["updates", "custom"],
                    config={"configurable": {"stream": True}},
                ):
                    if await request.is_disconnected():
                        break
                    if mode == "custom":
                        kind = chunk.get("kind")
                        if kind == "token":
                            tokens.append(chunk["text"])
                            yield sse_event("token", {"text": chunk["text"]})
                        elif kind == "component":
                            yield sse_event("component", chunk["data"])
                    elif mode == "updates":
                        for node, partial in chunk.items():
                            stage = _node_stage(node)
                            if stage and stage not in seen_stages:
                                seen_stages.add(stage)
                                yield sse_event("status", {"stage": stage})
                            if node == "supervisor" and isinstance(partial, dict):
                                route = partial.get("route", route)
                            if node == "synthesis" and isinstance(partial, dict):
                                layers = partial.get("layers", layers)
                final_answer = "".join(tokens).strip()
                answered = bool(final_answer)
                yield sse_event("done", {"answer": final_answer, "route": route, "layers": layers})
            except Exception as exc:
                logger.error("chat_stream_failed", exc_info=True)
                err: dict = {"detail": str(exc)}
                if isinstance(exc, AppException) and getattr(exc, "code", None):
                    err["code"] = exc.code
                yield sse_event("error", err)
                return
            finally:
                # Persist the turn from a FRESH session (the request db is closing by now).
                # Skip if the client disconnected before any answer streamed.
                if not await request.is_disconnected() or answered:
                    try:
                        await _persist_turn(
                            sessionmaker, session_id, payload.message, "".join(tokens).strip()
                        )
                    except Exception:
                        logger.error("chat_stream_persist_failed", exc_info=True)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    # JSON path: run the graph to completion and persist on the request-scoped session.
    with get_tracer().start_as_current_span("chat.request") as span:
        span.set_attribute("session.id", session_id)
        span.set_attribute("user.id", str(current_user.id))
        span.set_attribute("transport", "json")
        try:
            result = await graph.ainvoke(state)
        except (AppException, HTTPException):
            raise
        except Exception as e:
            logger.error("chat_failed", exc_info=True)
            raise AppException(
                status_code=500, detail="free tier Limit Reached for API please try again later"
            ) from e

        answer = result.get("answer", "")
        # The graph emits the FLAT route enum (RAG|WEB|BOTH|DIRECT). The frontend's blocking
        # routeTypeSchema is the combined form and has no "BOTH" — the SSE path maps it client-side,
        # so the JSON path must map it here too (BOTH → WEB+RAG), else an otherwise-successful
        # answer fails schema validation and surfaces as an error turn.
        route = result.get("route")
        if route == "BOTH":
            route = "WEB+RAG"
        await repo.save_message(db, session_id=session_id, role="user", content=payload.message)
        if answer:
            await repo.save_message(db, session_id=session_id, role="assistant", content=answer)
        return {
            "answer": answer,
            "route": route,
            "context_count": _count_context_chunks(result),
            "session_id": session_id,
            "layers": result.get("layers", []),
            # Carry the parsed rich components (table/chart/citation/code/callout/media) on the
            # blocking path too — the SSE path emits them as `component` events, but the JSON path
            # previously dropped them, so flipping streaming OFF lost every rich block.
            "components": result.get("components", []),
        }


# ========= CLEANUP =========


@app.post("/api/cleanup")
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def cleanup_session(
    request: Request,
    payload: CleanupRequest,
    current_user: User = Depends(get_current_user),
    s3: S3Client = Depends(get_s3_client),
    pinecone: PineconeClient = Depends(get_pinecone_client),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete Pinecone vectors, S3 objects, and Postgres state for a session."""
    try:
        logger.info("cleanup_request", session_id=payload.session_id)

        # Phase 3: ownership check — 404 if the session is missing, 403 if it's another user's.
        session = await repo.get_session(db, payload.session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if not _session_accessible(session, current_user):
            raise HTTPException(403, "session does not belong to the current user")

        keys = await repo.list_s3_keys_for_session(db, payload.session_id)
        await pinecone.delete_vectors_by_session(payload.session_id)
        if keys:
            await s3.delete_objects(keys)
        await repo.delete_session(db, payload.session_id)

        return {
            "status": "cleaned",
            "session_id": payload.session_id,
            "deleted_files": len(keys),
        }
    except (AppException, HTTPException):
        raise
    except Exception as e:
        logger.error("cleanup_failed", exc_info=True)
        raise AppException(status_code=500, detail="Cleanup failed unexpectedly.") from e


# ========= FRONTEND + HEALTH =========


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
