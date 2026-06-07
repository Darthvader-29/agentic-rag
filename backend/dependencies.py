"""FastAPI dependency provider functions.

Each function reads a client from app.state (set during lifespan startup) and
returns it to any endpoint that declares it via Depends().
"""

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from database.db_manager import PineconeClient
from integrations.duckduckgo.client import DuckDuckGoClient
from integrations.huggingface.client import HuggingFaceClient
from integrations.s3.client import S3Client


def get_graph(request: Request):
    """Return the compiled LangGraph (built once in lifespan, shared on app.state)."""
    return request.app.state.graph


def get_pinecone_client(request: Request) -> PineconeClient:
    return request.app.state.pinecone


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


def get_s3_client(request: Request) -> S3Client:
    return request.app.state.s3


def get_embedding_client(request: Request) -> HuggingFaceClient:
    return request.app.state.embedder


def get_web_search_client(request: Request) -> DuckDuckGoClient:
    return request.app.state.web


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Per-request AsyncSession: commits on success, rolls back on error."""
    factory = request.app.state.db_sessionmaker
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_db_sessionmaker(request: Request):
    """Return the session factory for background tasks that cannot use Depends."""
    return request.app.state.db_sessionmaker


def get_markdown_memory(request: Request):
    """Phase 7: the per-session markdown memory store (built once in lifespan)."""
    return request.app.state.markdown_memory


def get_knowledge_graph(request: Request):
    """Phase 7: the per-session knowledge-graph store (built once in lifespan)."""
    return request.app.state.knowledge_graph
