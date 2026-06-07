"""Phase 7: hybrid retriever — merge vector + knowledge-graph + markdown-memory signals.

Deterministic and model-free: each layer's hits are normalised to [0,1], scaled by a configurable
weight (``Settings.HYBRID_WEIGHTS_*``), merged by a stable key (dedup → keep the max score), and
ranked. It feeds the synthesis node so the answer is grounded in all three memory layers.
Reciprocal-rank fusion is a drop-in alternative behind the same interface (docs/08 Appendix B).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from observability.tracing import get_tracer


@dataclass
class Hit:
    key: str
    text: str
    score: float
    source: str  # "vector" | "graph" | "memory"


def _normalise(hits: list[Hit]) -> list[Hit]:
    """Scale scores into [0,1] by the layer's own max, so layers are comparable before weighting."""
    if not hits:
        return hits
    hi = max((h.score for h in hits), default=0.0) or 1.0
    return [Hit(h.key, h.text, h.score / hi, h.source) for h in hits]


def query_terms(query: str) -> list[str]:
    """Candidate seed entities from the query.

    The graph neighbour lookup only matches nodes that actually exist, so over-generating seeds is
    harmless — non-entities simply find no neighbours (MVP; smarter linking is future work).
    """
    words = [w.strip(".,!?;:\"'()[]").strip() for w in query.split()]
    return sorted({w for w in words if len(w) > 2})


class HybridRetriever:
    """Merge per-layer hits into a single ranked context list (normalise → weight → dedup → sort)."""

    def __init__(self, graph: Any, markdown: Any, weights: dict[str, float]) -> None:
        self._graph = graph  # KnowledgeGraph | None
        self._markdown = markdown  # MarkdownMemory | None
        self._w = weights  # {"vector": .., "graph": .., "markdown": ..}

    async def retrieve(
        self,
        query: str,
        session_id: str,
        *,
        vector_hits: list[tuple[str, float]],
        seed_entities: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Hit]:
        """Merge vector hits ``(text, score)`` with graph neighbours + markdown notes for a session."""
        with get_tracer().start_as_current_span("memory.hybrid.retrieve") as span:
            vec = _normalise(
                [
                    Hit(key=f"v:{i}", text=t, score=s, source="vector")
                    for i, (t, s) in enumerate(vector_hits)
                ]
            )

            grh: list[Hit] = []
            if self._graph is not None:
                seeds = seed_entities if seed_entities is not None else query_terms(query)
                neighbours = await self._graph.neighbors(session_id, seeds, hops=1)
                if neighbours:
                    grh = _normalise(
                        [
                            Hit(
                                key="graph",
                                text="Related entities (knowledge graph): " + ", ".join(neighbours),
                                score=1.0,
                                source="graph",
                            )
                        ]
                    )

            mkd: list[Hit] = []
            if self._markdown is not None:
                notes = await self._markdown.read(session_id)
                if notes.strip():
                    mkd = _normalise([Hit(key="memory", text=notes, score=1.0, source="memory")])

            merged: dict[str, Hit] = {}
            for hits, weight in (
                (vec, self._w["vector"]),
                (grh, self._w["graph"]),
                (mkd, self._w["markdown"]),
            ):
                for h in hits:
                    weighted = h.score * weight
                    cur = merged.get(h.key)
                    if cur is None or weighted > cur.score:  # dedup by key, keep max
                        merged[h.key] = Hit(h.key, h.text, weighted, h.source)

            ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
            span.set_attribute("hybrid.vector", len(vec))
            span.set_attribute("hybrid.graph", len(grh))
            span.set_attribute("hybrid.markdown", len(mkd))
            return ranked[:top_k]
