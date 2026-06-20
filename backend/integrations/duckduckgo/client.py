"""Async DuckDuckGo search client for WEB routing.

Sync DDGS call runs via asyncio.to_thread; tenacity retries on transient errors.

A bounded DDGS ``timeout`` stops a hung upstream from pinning the worker thread.
The blocking call deliberately lets its exception propagate so the ``@retry``
decorator can see (and retry) transient failures; only after the retries are
exhausted does the error surface. Fail-soft for genuine errors is the caller's
job (``components/retrieval.py`` logs and returns ``[]``), which keeps a real
failure distinct from an honestly empty result set — swallowing the error here
made the ``@retry`` dead code and turned every failure into a silent "no results".
"""

import asyncio

import structlog
from duckduckgo_search import DDGS
from tenacity import retry

from integrations._retry import READ_TIMEOUT, RETRY_KW

logger = structlog.get_logger(__name__)


class DuckDuckGoClient:
    @retry(**RETRY_KW)
    def _search_sync(self, query: str, max_results: int) -> list[dict[str, str]]:
        # No try/except here: a swallowed exception would (a) defeat @retry, which only
        # retries when it sees one raised, and (b) make a transient failure look exactly
        # like an empty result set. Let it propagate; @retry reraises after the last try.
        with DDGS(timeout=int(READ_TIMEOUT)) as ddgs:
            results = ddgs.text(query, max_results=max_results)
            return [{"title": r["title"], "snippet": r["body"]} for r in results]

    async def search_web(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        try:
            return await asyncio.to_thread(self._search_sync, query, max_results)
        except Exception:
            # Reached only after @retry has exhausted its attempts. Logged here so a real
            # outage is observable even though the caller fail-softs to []/web fallback.
            logger.error("duckduckgo_search_error", query=query, exc_info=True)
            raise
