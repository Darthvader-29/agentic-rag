"""Phase 7: per-session markdown memory — a bounded running notes document.

Persisted in Postgres (``session_memory``), never held in-process (Phase 5 statelessness). The
synthesis node appends one ``Q:/A:`` note per turn; the hybrid retriever (BE-4) reads it back per
session. Each call opens its OWN short-lived session from the injected factory — during SSE
streaming the request-scoped session is already closing, so memory writes must use a fresh
sessionmaker (the same pattern ``app.py`` uses to persist turns mid-stream).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import SessionMemory
from observability.tracing import get_tracer


class MarkdownMemory:
    """Bounded per-session running notes; persisted, opens its own session per call."""

    def __init__(self, session_factory: Any, max_chars: int) -> None:
        self._session_factory = session_factory
        self._max_chars = max_chars

    async def read(self, session_id: str) -> str:
        # Delegate to the single query path; callers wanting just the body discard the timestamp.
        content, _ = await self.read_with_updated(session_id)
        return content

    async def read_with_updated(self, session_id: str) -> tuple[str, str | None]:
        """Return ``(content, updated_at_iso)`` for the GET /memory endpoint; ``("", None)`` if absent."""
        with get_tracer().start_as_current_span("memory.markdown.read"):
            async with self._session_factory() as db:
                row = (
                    await db.execute(
                        select(SessionMemory).where(SessionMemory.session_id == session_id)
                    )
                ).scalar_one_or_none()
                if row is None:
                    return "", None
                return row.content, (row.updated_at.isoformat() if row.updated_at else None)

    async def append(self, session_id: str, note: str) -> None:
        """Append a note, keeping only the last ``max_chars`` (bounded summary).

        Atomic INSERT ... ON CONFLICT DO UPDATE. The old SELECT ... FOR UPDATE then INSERT raced on
        the FIRST append: ``FOR UPDATE`` locks nothing when no row exists, so two concurrent first
        turns both took the INSERT branch → duplicate-PK IntegrityError → one note silently dropped.
        The upsert is race-free: concurrent inserts serialize to one INSERT + one DO UPDATE concat.
        Truncation runs in SQL (``right(...)``) so the bound holds on both the insert and the merge.
        """
        with get_tracer().start_as_current_span("memory.markdown.append"):
            async with self._session_factory() as db:
                insert_stmt = pg_insert(SessionMemory).values(
                    session_id=session_id,
                    content=func.right(note, self._max_chars),
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=["session_id"],
                    set_={
                        # existing || "\n\n" || new_note, bounded to the last max_chars
                        "content": func.right(
                            SessionMemory.content.concat("\n\n").concat(
                                insert_stmt.excluded.content
                            ),
                            self._max_chars,
                        ),
                        "updated_at": func.now(),
                    },
                )
                await db.execute(stmt)
                await db.commit()
