"""Phase 7: per-session knowledge graph (networkx MVP), persisted + shared.

A session's graph is loaded from Postgres (``session_graph``), mutated, and saved back under a Redis
lock — never an authoritative in-process global (Phase 5 statelessness). Built by the Celery
ingestion task's entity-extraction pass; read by the hybrid retriever (neighbour lookup) and exposed
to the frontend via GET /api/sessions/{id}/graph as networkx node-link JSON (``{nodes, links}``).
Neo4j is deferred (docs/08).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import networkx as nx
import structlog

from database.models import SessionGraph
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)

Triple = tuple[str, str, str]


def _to_node_link(g: nx.DiGraph) -> dict:
    """Serialize to node-link JSON with the ``links`` key (the frontend/react-force-graph shape)."""
    try:
        return nx.node_link_data(g, edges="links")
    except TypeError:  # networkx < 3.4: "links" is already the default key name
        return nx.node_link_data(g)


def _from_node_link(data: dict) -> nx.DiGraph:
    try:
        return nx.node_link_graph(data, directed=True, edges="links")
    except TypeError:
        return nx.node_link_graph(data, directed=True)


class KnowledgeGraph:
    """Per-session networkx graph; load → merge deltas → save, under a Redis lock."""

    def __init__(self, session_factory: Any, redis: Any = None, *, lock_ttl: int = 30) -> None:
        self._session_factory = session_factory
        self._redis = redis  # redis.asyncio client; None disables locking (single-threaded tests)
        # A generous TTL so the load→merge→save critical section comfortably finishes before the
        # lock auto-expires; an expiry mid-work lets a second writer in and the slower save wins,
        # losing the faster writer's triples (the lost-update the lock exists to prevent).
        self._lock_ttl = lock_ttl

    @asynccontextmanager
    async def _lock(self, session_id: str):
        if self._redis is None:
            yield
            return
        # blocking_timeout bounds the wait so ingestion can't hang forever behind a stuck holder.
        lock = self._redis.lock(
            f"kg:lock:{session_id}", timeout=self._lock_ttl, blocking_timeout=self._lock_ttl
        )
        acquired = await lock.acquire()
        if not acquired:
            # Couldn't acquire within the window — proceed best-effort but SURFACE it rather than
            # silently racing (the merge is additive, so a rare concurrent overwrite is logged).
            logger.warning("kg_lock_not_acquired", session_id=session_id)
            yield
            return
        try:
            yield
        finally:
            try:
                await lock.release()
            except Exception:
                # Release failed → the lock expired mid-work (TTL exceeded) and may have been taken
                # by another writer, so a concurrent save could have lost this writer's triples.
                # Surface it (was silently swallowed) so the lost-update is detectable in logs.
                logger.warning("kg_lock_release_failed_possible_lost_update", session_id=session_id)

    async def _load(self, db: Any, session_id: str) -> nx.DiGraph:
        row = await db.get(SessionGraph, session_id)
        if row and row.data:
            return _from_node_link(json.loads(row.data))
        return nx.DiGraph()

    async def _save(self, db: Any, session_id: str, g: nx.DiGraph) -> None:
        data = json.dumps(_to_node_link(g))
        row = await db.get(SessionGraph, session_id)
        if row:
            row.data = data
        else:
            db.add(SessionGraph(session_id=session_id, data=data))

    async def add_entities(self, session_id: str, doc_id: str, triples: list[Triple]) -> int:
        """Merge (subject, relation, object) deltas into the session's graph (no overwrite)."""
        if not triples:
            return 0
        with get_tracer().start_as_current_span("memory.graph.add_entities") as span:
            span.set_attribute("doc.id", doc_id)
            async with self._lock(session_id):
                async with self._session_factory() as db:
                    g = await self._load(db, session_id)
                    for s, rel, o in triples:
                        g.add_node(s)
                        g.add_node(o)
                        g.add_edge(s, o, relation=rel, doc_id=doc_id)
                    await self._save(db, session_id, g)
                    await db.commit()
            span.set_attribute("graph.triples", len(triples))
            return len(triples)

    async def neighbors(self, session_id: str, entities: list[str], hops: int = 1) -> list[str]:
        """Entities within ``hops`` of any seed entity (undirected reach), sorted; ``[]`` if none."""
        with get_tracer().start_as_current_span("memory.graph.neighbors"):
            async with self._session_factory() as db:
                g = await self._load(db, session_id)
            if g.number_of_nodes() == 0:
                return []
            undirected = g.to_undirected()
            seen: set[str] = set()
            for e in entities:
                if e in undirected:
                    seen |= set(nx.single_source_shortest_path_length(undirected, e, cutoff=hops))
            return sorted(seen)

    async def export(self, session_id: str) -> dict:
        """networkx node-link JSON for GET /api/sessions/{id}/graph (``{nodes, links}``)."""
        async with self._session_factory() as db:
            g = await self._load(db, session_id)
        return _to_node_link(g)
