"""Pinecone client — async wrapper around the sync Pinecone SDK.

All blocking SDK calls run inside asyncio.to_thread; sync methods are decorated
with tenacity retry/backoff so sleep happens in the worker thread, not the loop.
"""

import asyncio
import time

import structlog
from pinecone import Pinecone, ServerlessSpec
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

_RETRY = dict(
    stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True
)


class PineconeClient:
    def __init__(self, *, api_key: str, index_name: str, dimension: int = 384):
        self._pc = Pinecone(api_key=api_key)
        self._index_name = index_name
        self._dimension = dimension
        self._index = None

    @classmethod
    def from_settings(cls, settings) -> "PineconeClient":
        return cls(api_key=settings.PINECONE_API_KEY, index_name=settings.PINECONE_INDEX_NAME)

    @retry(**_RETRY)
    def _ensure_index_sync(self) -> None:
        names = [i.name for i in self._pc.list_indexes()]
        if self._index_name not in names:
            logger.info("pinecone_create_index", index_name=self._index_name)
            self._pc.create_index(
                name=self._index_name,
                dimension=self._dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
            time.sleep(10)
        self._index = self._pc.Index(self._index_name)

    async def ensure_index(self) -> None:
        """Called once in lifespan; warms up the cached Index handle."""
        await asyncio.to_thread(self._ensure_index_sync)

    def _index_or_raise(self):
        if self._index is None:
            self._ensure_index_sync()
        return self._index

    # ── vectors ──────────────────────────────────────────────────────────────

    @retry(**_RETRY)
    def _save_vectors_sync(self, vectors: list[dict]) -> None:
        index = self._index_or_raise()
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            index.upsert(vectors=vectors[i : i + batch_size])
        logger.info("pinecone_vectors_saved", count=len(vectors))

    async def save_vectors(self, vectors: list[dict]) -> None:
        await asyncio.to_thread(self._save_vectors_sync, vectors)

    @retry(**_RETRY)
    def _search_vectors_sync(
        self,
        query_vector: list[float],
        top_k: int = 5,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        index = self._index_or_raise()
        params: dict = {"vector": query_vector, "top_k": top_k, "include_metadata": True}
        # Tenant scoping: filter by session_id AND user_id (defense-in-depth — the DB ownership
        # check already gates which session_id a caller may search). Vectors written before this
        # change carry no user_id and won't match a user_id filter; that is the accepted tradeoff.
        flt: dict = {}
        if session_id:
            flt["session_id"] = {"$eq": session_id}
        if user_id:
            flt["user_id"] = {"$eq": user_id}
        if flt:
            params["filter"] = flt
        results = index.query(**params)
        return [
            {
                "text": match.metadata["text"],
                "score": match.score,
                "source": match.metadata.get("filename"),
                "chunk_index": match.metadata.get("chunk_index"),
            }
            for match in results.matches
        ]

    async def search_vectors(
        self,
        query_vector: list[float],
        top_k: int = 5,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._search_vectors_sync, query_vector, top_k, session_id, user_id
        )

    @retry(**_RETRY)
    def _delete_vectors_sync(self, session_id: str) -> None:
        index = self._index_or_raise()
        try:
            index.delete(filter={"session_id": {"$eq": session_id}})
            logger.info("pinecone_vectors_deleted", session_id=session_id)
        except Exception:
            logger.error("pinecone_delete_error", exc_info=True)

    async def delete_vectors_by_session(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete_vectors_sync, session_id)
