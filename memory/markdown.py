"""Phase 7: per-session markdown memory — a bounded running notes document.

Persisted in Postgres (``session_memory``), never held in-process (Phase 5 statelessness). The
synthesis node appends one ``Q:/A:`` note per turn; the hybrid retriever (BE-4) reads it back per
session. Each call opens its OWN short-lived session from the injected factory — during SSE
streaming the request-scoped session is already closing, so memory writes must use a fresh
sessionmaker (the same pattern ``app.py`` uses to persist turns mid-stream).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from database.models import SessionMemory
from observability.tracing import get_tracer


class MarkdownMemory:
    """Bounded per-session running notes; persisted, opens its own session per call."""

    def __init__(self, session_factory: Any, max_chars: int) -> None:
        self._session_factory = session_factory
        self._max_chars = max_chars

    async def read(self, session_id: str) -> str:
        with get_tracer().start_as_current_span("memory.markdown.read"):
            async with self._session_factory() as db:
                row = (
                    await db.execute(
                        select(SessionMemory).where(SessionMemory.session_id == session_id)
                    )
                ).scalar_one_or_none()
                return row.content if row else ""

    async def append(self, session_id: str, note: str) -> None:
        """Append a note under a row lock, keeping only the last ``max_chars`` (bounded summary)."""
        with get_tracer().start_as_current_span("memory.markdown.append"):
            async with self._session_factory() as db:
                row = (
                    await db.execute(
                        select(SessionMemory)
                        .where(SessionMemory.session_id == session_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                content = (row.content + "\n\n" + note) if row and row.content else note
                content = content[-self._max_chars :]
                if row:
                    row.content = content
                else:
                    db.add(SessionMemory(session_id=session_id, content=content))
                await db.commit()
