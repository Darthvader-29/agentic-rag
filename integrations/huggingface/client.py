"""Async HuggingFace Inference API client for sentence-transformers/all-MiniLM-L6-v2 embeddings.

Free tier, 384-dimensional vectors. Sync feature_extraction runs via asyncio.to_thread
so the event loop is never blocked.
"""

import asyncio

import numpy as np
from huggingface_hub import InferenceClient
from tenacity import retry

from integrations._retry import RETRY_KW

_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class HuggingFaceClient:
    def __init__(self, *, token: str):
        self._client = InferenceClient(model=_MODEL, token=token)

    @classmethod
    def from_settings(cls, settings) -> "HuggingFaceClient":
        return cls(token=settings.HUGGINGFACE_TOKEN)

    @retry(**RETRY_KW)
    def _embed_batch_sync(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_embeds = self._client.feature_extraction(batch, normalize=True)
            if isinstance(batch_embeds, np.ndarray):
                batch_embeds = batch_embeds.tolist()
            embeddings.extend(batch_embeds)
        return embeddings

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_batch_sync, texts, batch_size)

    async def embed_single(self, text: str) -> list[float]:
        results = await self.embed_batch([text], batch_size=1)
        return results[0]
