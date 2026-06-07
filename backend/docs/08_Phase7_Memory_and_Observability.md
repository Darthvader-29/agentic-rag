# Phase 7 — 3-Layer Memory + Observability/Tracing

> **For implementers.** This document is a self-contained, execution-ready plan for Phase 7 — the final phase of the upgrade. It assumes you have read [00_Master_Upgrade_Roadmap.md](./00_Master_Upgrade_Roadmap.md) (see §4 Phase 7) and have completed [07_Phase6_LangGraph_and_Streaming.md](./07_Phase6_LangGraph_and_Streaming.md). By the end of this phase the synthesis agent is fed by a **3-layer memory** (per-session markdown notes, a `networkx` knowledge graph, and the existing Pinecone vectors), all merged and ranked by a hybrid retriever; and the whole request path emits **OpenTelemetry** traces — API → agents → providers → DB/Redis — with trace context propagated across the Celery worker boundary and LangSmith capturing agent-level traces. This is the last document in the series (`08` = Phase 7); everything beyond it is future work.

## 1. Objective & scope

### In scope
- A new **`memory/` package** implementing three persisted memory layers:
  1. **Per-session markdown memory** (`memory/markdown.py`) — a running conversation/notes document keyed by session, persisted to Postgres (or S3), never held in-process.
  2. **Knowledge graph** (`memory/graph.py`) — a `networkx` MVP graph built from an **entity-extraction pass** added to the Celery ingestion task, serialized and persisted so every API instance and worker shares one graph.
  3. **Existing vectors** — Pinecone, unchanged from prior phases.
- A **hybrid retriever** (`memory/hybrid.py`) that queries all three layers, then merges and ranks the results into a single context block.
- Wiring the hybrid retriever into the **synthesis/retrieval nodes** of the Phase 6 LangGraph orchestrator (`agents/nodes.py`).
- An **entity-extraction pass** added to the Phase 5 ingestion task (`worker/tasks.py`) that emits graph nodes/edges alongside the existing chunk-and-upsert flow.
- **OpenTelemetry** distributed tracing: a bootstrap module (`observability/tracing.py`), FastAPI/SQLAlchemy/Redis/Celery instrumentation, explicit spans on the chat path, and **trace-context propagation** from the API into the Celery worker.
- **LangSmith** enabled for agent-level traces (the dependency is already installed).
- `config.py` additions (OTEL endpoint/sampling, LangSmith) and an Alembic migration if memory/graph tables are added.

### Explicitly deferred
- **Neo4j migration of the knowledge graph.** The MVP uses `networkx` serialized to Postgres/S3. Migrating to a real graph database (Neo4j or similar) is post-MVP/future work and is out of scope here.
- Graph-based ranking sophistication beyond a simple weighted merge (e.g. learned re-rankers, PageRank-weighted edges) — future work.
- Cross-session / long-term semantic memory consolidation beyond per-session markdown — future work.
- A metrics/logs backend beyond traces (Prometheus dashboards, log aggregation) — future work; this phase ships traces only.
- Anything not enumerated under **In scope** above is future work; Phase 7 closes the planned roadmap.

## 2. Decisions & rationale

| Decision | Rationale | Alternatives considered |
| --- | --- | --- |
| `networkx` for the knowledge-graph MVP | Already a dependency; in-memory graph ops are trivial; serializable to JSON/pickle for shared persistence | Neo4j (operational overhead, new infra — deferred to post-MVP); RDF stores (heavier, niche) |
| Persist every memory layer (Postgres/S3), never in-process | Phase 5 made the app stateless/horizontally scaled; an in-process graph or dict would diverge per replica and per worker | In-process `networkx.Graph` global (breaks statelessness); local file (not shared across pods) |
| Markdown memory in Postgres (S3 as alternative) | Small, per-session, transactional; co-located with existing relational data; easy to query/update | S3 (fine for larger docs, eventual consistency); Redis (volatile, wrong durability profile) |
| Entity extraction inside the Celery ingestion task | Ingestion is already async/batched (Phase 5); extraction is the natural place to amortise its LLM cost off the request path | Extraction at query time (latency + cost on every chat); separate service (over-engineered for MVP) |
| OpenTelemetry SDK + OTLP exporter | Vendor-neutral, standard context propagation across process boundaries, rich auto-instrumentation for FastAPI/SQLAlchemy/Redis/Celery | Vendor-specific SDKs (lock-in); hand-rolled timing (no propagation, no ecosystem) |
| Propagate trace context into Celery via headers | A trace must span API → worker; OTEL's `propagate.inject/extract` carries `traceparent` through task headers | Per-process traces (worker work appears disconnected); no tracing in worker (blind spot) |
| LangSmith for agent traces | Already installed; purpose-built for LLM/agent call trees, complements OTEL infra spans | Only OTEL (loses prompt/response detail); only LangSmith (no infra/DB/Redis spans) |
| Hybrid merge = normalise + weighted-sum + dedup | Deterministic, testable, no model dependency; scores from each layer normalised before combining | Naive concatenation (no ranking); reciprocal-rank fusion (good — left as a tunable variant) |

## 3. Current-state snapshot (verified)

The following reflects the repository **as of the start of Phase 7**, assuming Phases 1–6 are complete and merged.

- **Phase 1 (DI/Settings).** All configuration flows through a typed `Settings` object; components receive collaborators via constructor injection; FastAPI dependency overrides are the test seam. No module-level client globals.
- **Phase 2 (Postgres/Auth).** Postgres + SQLAlchemy + Alembic are in place; authentication is wired; migrations apply cleanly via testcontainers in CI.
- **Phase 3 (Multi-provider LLM).** A per-request, provider-agnostic LLM abstraction exists; the chat model/provider can be selected per request.
- **Phase 5 (Async/Cache/Rate-limit).** Redis + Celery are deployed; ingestion runs as a Celery task in `worker/tasks.py`; presigned-URL upload, caching, and rate limiting are live. **The app is stateless and horizontally scaled** — this is the load-bearing constraint for this phase.
- **Phase 6 (LangGraph/SSE).** The chat path runs through an `agents/` LangGraph `StateGraph` with typed state (`agents/state.py`), retrieval and synthesis nodes (`agents/nodes.py`), and SSE token streaming from `app.py`.
- **Retrieval is currently vector-only.** The retrieval node calls the Pinecone-backed retriever; there is no graph or markdown memory feeding synthesis.
- **No distributed tracing yet.** There is no `observability/` package, no OTEL bootstrap, and trace context does not cross the API→worker boundary. LangSmith is installed but not enabled on the agent path.

> **Verify before you start:** `git log --oneline -8` shows the Phase 6 merge; `uv run pytest -q` is green; `uv run mypy .` and `uv run ruff check .` pass; the SSE chat endpoint streams through the LangGraph orchestrator; `uv run celery -A worker inspect ping` reaches a worker.

## 4. Risks & gotchas (with resolutions)

| Risk / gotcha | Resolution |
| --- | --- |
| A `networkx` graph held in-process diverges across replicas/workers (breaks Phase 5 statelessness) | Treat the graph as **shared persisted state**: load → mutate → serialise back to Postgres/S3 within a transaction/lock. Never keep an authoritative graph in a module global; an in-process copy is a short-lived cache only. |
| Concurrent ingestion tasks racing on the persisted graph (lost updates) | Serialise graph writes with a row-level lock (`SELECT … FOR UPDATE`) or a Redis lock around load-merge-save; merge node/edge deltas rather than overwriting the whole graph. |
| Entity extraction adds LLM cost/latency to ingestion | Keep extraction in the **async Celery task** (off the request path), batch per document, cache by content hash, and make the model/threshold configurable so it can be tuned or disabled. |
| OTEL context not propagated API → Celery worker → providers (disconnected traces) | Inject `traceparent` into Celery task headers at enqueue (`propagate.inject`), extract it in a worker signal/`before_task_publish`/`task_prerun` hook (`propagate.extract`), and use auto-instrumentation for SQLAlchemy/Redis/HTTP clients so provider/DB calls nest under the request span. |
| Hybrid merge produces inconsistent ranking (different score scales per layer) | Normalise each layer's scores to `[0,1]` before combining; apply explicit, configurable weights; deduplicate overlapping content by a stable key (chunk id / entity id) keeping the max score. |
| Double-counting tokens / cost across OTEL spans and LangSmith | Record token usage **once** at the provider boundary (the LLM abstraction); attach counts as span attributes there only — do not also tally them in nodes or the worker. |
| LangSmith API key leakage / accidental enablement in CI | Store the key as `SecretStr`; gate tracing behind a `langsmith_tracing` flag that defaults **off** in tests/CI; never log the settings object wholesale. |
| Markdown memory growing unbounded per session | Cap stored size (truncate/rollup oldest notes) and store as a single row/object per session; treat it as a bounded running summary, not an append-only log. |
| Migration drift if memory/graph tables are added | Add an Alembic migration in the same PR as the model; CI applies it cleanly against a fresh testcontainer DB (Phase 2 gate carries forward). |
| OTLP exporter unreachable blocks startup/requests | Use the batch span processor (async export) and a no-op/console exporter fallback when `otel_exporter_endpoint` is unset, so a missing collector never fails requests. |

## 5. Tasks (ordered)

Each task is a small, reviewable commit. Use conventional-commit messages. Write the test first where it is natural to do so.

### Task 1 — Add OpenTelemetry deps and extend `Settings` (TDD)

Add the dependencies and lock (`networkx` and `langsmith` are already present):

```bash
uv add opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp \
       opentelemetry-instrumentation-fastapi \
       opentelemetry-instrumentation-sqlalchemy \
       opentelemetry-instrumentation-redis \
       opentelemetry-instrumentation-celery
uv lock
```

Write the settings test first:

```python
# tests/test_settings_phase7.py
from config import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PINECONE_API_KEY", "pc-test")
    monkeypatch.setenv("PINECONE_INDEX", "idx")


def test_otel_defaults_off(monkeypatch):
    _base_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.otel_enabled is False
    assert s.langsmith_tracing is False
    assert s.otel_service_name == "agentic-rag"


def test_otel_reads_endpoint(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_ENDPOINT", "http://collector:4317")
    s = Settings(_env_file=None)
    assert s.otel_enabled is True
    assert s.otel_exporter_endpoint == "http://collector:4317"
```

Extend `config.py`:

```python
# config.py (additions)
from pydantic import SecretStr


class Settings(BaseSettings):
    # ... existing fields ...

    # --- Observability ---
    otel_enabled: bool = False
    otel_service_name: str = "agentic-rag"
    otel_exporter_endpoint: str | None = None  # OTLP gRPC/HTTP; None => console
    otel_sample_ratio: float = 1.0
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "agentic-rag"

    # --- Memory ---
    memory_markdown_max_chars: int = 8_000
    graph_storage: str = "postgres"           # "postgres" | "s3"
    entity_extraction_model: str = "gpt-4o-mini"
    hybrid_weights_vector: float = 0.6
    hybrid_weights_graph: float = 0.25
    hybrid_weights_markdown: float = 0.15
```

Commit: `feat(config): add OpenTelemetry, LangSmith and memory settings`.

### Task 2 — Tracing bootstrap + FastAPI instrumentation + a span on the chat path

Create `observability/tracing.py`:

```python
# observability/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from config import Settings


def init_tracing(settings: Settings) -> None:
    if not settings.otel_enabled:
        return
    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.otel_sample_ratio),
    )
    if settings.otel_exporter_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)
    else:
        exporter = ConsoleSpanExporter()  # safe fallback; never blocks requests
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer(name: str = "agentic-rag"):
    return trace.get_tracer(name)
```

Instrument FastAPI and DB/Redis at startup in `app.py`:

```python
# app.py (additions)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from observability.tracing import init_tracing, get_tracer

settings = get_settings()
init_tracing(settings)
FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)
RedisInstrumentor().instrument()
```

Add an explicit span around the chat handler so the orchestrator work is named:

```python
# app.py — chat endpoint
@app.post("/chat")
async def chat(payload: ChatRequest, ...):
    with get_tracer().start_as_current_span("chat.request") as span:
        span.set_attribute("session.id", payload.session_id)
        span.set_attribute("user.id", current_user.id)
        return await run_graph(payload, ...)
```

Write a trace-emission test using an in-memory exporter:

```python
# tests/test_tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_chat_emits_span(client):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    client.post("/chat", json={"session_id": "s1", "message": "hi"})
    names = {s.name for s in exporter.get_finished_spans()}
    assert "chat.request" in names
```

Commit: `feat(observability): OTEL bootstrap + FastAPI instrumentation + chat span`.

### Task 3 — Propagate trace context into the Celery worker + instrument the task

Inject the active context into task headers at enqueue, extract it in the worker, and instrument Celery:

```python
# worker/tracing.py
from opentelemetry import propagate, trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from celery.signals import before_task_publish, task_prerun, worker_process_init
from observability.tracing import init_tracing, get_tracer
from config import get_settings


@worker_process_init.connect
def _init_worker_tracing(**_):
    init_tracing(get_settings())
    CeleryInstrumentor().instrument()


@before_task_publish.connect
def _inject_context(headers=None, **_):
    headers = headers if headers is not None else {}
    propagate.inject(headers)  # writes traceparent into task headers


@task_prerun.connect
def _extract_context(task=None, **_):
    ctx = propagate.extract(getattr(task.request, "headers", {}) or {})
    # attach extracted context so the task's spans nest under the request
    task.request._otel_ctx = ctx
```

Wrap the ingestion task body in a span that uses the extracted context:

```python
# worker/tasks.py (additions)
from opentelemetry import context as otel_context
from observability.tracing import get_tracer

@app.task(bind=True)
def ingest_document(self, doc_id: str, text: str):
    parent = getattr(self.request, "_otel_ctx", None)
    token = otel_context.attach(parent) if parent else None
    try:
        with get_tracer().start_as_current_span("ingest.document") as span:
            span.set_attribute("doc.id", doc_id)
            n = embed_and_upsert(doc_id, text)         # existing vector path
            # entity extraction added in Task 5
            return {"chunks": n}
    finally:
        if token:
            otel_context.detach(token)
```

Test the propagation contract (header carries `traceparent`, worker extracts a valid context):

```python
# tests/test_worker_tracing.py
from worker.tracing import _inject_context, _extract_context
from opentelemetry import propagate


def test_inject_then_extract_roundtrips():
    headers = {}
    _inject_context(headers=headers)
    assert "traceparent" in headers
    ctx = propagate.extract(headers)
    assert ctx is not None
```

Commit: `feat(observability): propagate trace context into Celery worker`.

### Task 4 — Per-session markdown memory store (persisted) + wire into chat flow

Add a persisted store. Model (Postgres backend; S3 is an alternative behind the same interface):

```python
# memory/markdown.py
from sqlalchemy import select
from observability.tracing import get_tracer


class MarkdownMemory:
    """Per-session running markdown notes; persisted, never in-process."""

    def __init__(self, session_factory, max_chars: int):
        self._session_factory = session_factory
        self._max_chars = max_chars

    def read(self, session_id: str) -> str:
        with get_tracer().start_as_current_span("memory.markdown.read"):
            with self._session_factory() as db:
                row = db.execute(
                    select(SessionMemory).where(SessionMemory.session_id == session_id)
                ).scalar_one_or_none()
                return row.content if row else ""

    def append(self, session_id: str, note: str) -> None:
        with get_tracer().start_as_current_span("memory.markdown.append"):
            with self._session_factory() as db:
                row = db.execute(
                    select(SessionMemory)
                    .where(SessionMemory.session_id == session_id)
                    .with_for_update()
                ).scalar_one_or_none()
                content = (row.content + "\n\n" + note) if row else note
                content = content[-self._max_chars:]  # bounded running summary
                if row:
                    row.content = content
                else:
                    db.add(SessionMemory(session_id=session_id, content=content))
                db.commit()
```

Add the model + an Alembic migration:

```python
# models.py (addition)
class SessionMemory(Base):
    __tablename__ = "session_memory"
    session_id = Column(String, primary_key=True)
    content = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
```

```bash
uv run alembic revision --autogenerate -m "add session_memory table"
uv run alembic upgrade head
```

Read markdown before synthesis and append the turn after; write the test first:

```python
# tests/test_markdown_memory.py
def test_append_then_read(markdown_memory):
    markdown_memory.append("s1", "User asked about X.")
    assert "User asked about X." in markdown_memory.read("s1")


def test_append_is_bounded(markdown_memory_small):  # max_chars=20
    markdown_memory_small.append("s1", "x" * 50)
    assert len(markdown_memory_small.read("s1")) <= 20
```

Commit: `feat(memory): persisted per-session markdown memory`.

### Task 5 — Entity-extraction pass in ingestion → persisted networkx graph

Add the graph layer. The graph is **loaded from storage, mutated, and saved back** — never an authoritative in-process global:

```python
# memory/graph.py
import json
import networkx as nx
from observability.tracing import get_tracer


class KnowledgeGraph:
    """networkx MVP graph, persisted/shared (Postgres or S3). Neo4j is future."""

    def __init__(self, store):           # store: load()/save() of serialized graph
        self._store = store

    def _load(self) -> nx.DiGraph:
        raw = self._store.load()
        return nx.node_link_graph(json.loads(raw)) if raw else nx.DiGraph()

    def _save(self, g: nx.DiGraph) -> None:
        self._store.save(json.dumps(nx.node_link_data(g)))

    def add_entities(self, doc_id: str, triples: list[tuple[str, str, str]]) -> None:
        """Merge (subject, relation, object) deltas under a write lock."""
        with get_tracer().start_as_current_span("memory.graph.add_entities") as span:
            span.set_attribute("doc.id", doc_id)
            with self._store.lock():           # row lock / Redis lock: no lost updates
                g = self._load()
                for s, rel, o in triples:
                    g.add_node(s); g.add_node(o)
                    g.add_edge(s, o, relation=rel, doc_id=doc_id)
                self._save(g)

    def neighbors(self, entities: list[str], hops: int = 1) -> list[str]:
        g = self._load()
        seen: set[str] = set()
        for e in entities:
            if e in g:
                seen |= set(nx.single_source_shortest_path_length(g, e, cutoff=hops))
        return sorted(seen)
```

Add the extraction pass to the ingestion task (off the request path, batched, cached by content hash):

```python
# worker/tasks.py — inside ingest_document, after embed_and_upsert
from memory.extract import extract_triples

triples = extract_triples(text, model=settings.entity_extraction_model)
knowledge_graph.add_entities(doc_id, triples)
span.set_attribute("graph.triples", len(triples))
```

```python
# memory/extract.py
import json
from observability.tracing import get_tracer


def extract_triples(text, model, llm=...):
    """LLM entity/relation extraction → list of (subject, relation, object)."""
    with get_tracer().start_as_current_span("memory.extract"):
        resp = llm.complete(model=model, prompt=_EXTRACT_PROMPT.format(text=text))
        return [tuple(t) for t in json.loads(resp)]
```

Tests (fake store + fake LLM; assert merge, no overwrite, neighbour lookup):

```python
# tests/test_graph_memory.py
def test_add_and_neighbors(graph):
    graph.add_entities("d1", [("Ada", "wrote", "Notes")])
    assert "Notes" in graph.neighbors(["Ada"], hops=1)


def test_merge_does_not_overwrite(graph):
    graph.add_entities("d1", [("A", "rel", "B")])
    graph.add_entities("d2", [("C", "rel", "D")])
    assert set(graph.neighbors(["A", "C"], hops=1)) >= {"A", "B", "C", "D"}
```

Commit: `feat(memory): entity-extraction ingestion pass + persisted networkx graph`.

### Task 6 — Hybrid retrieval (merge vector + graph + markdown) → synthesis node

Implement the merge/rank, then wire it into the LangGraph retrieval/synthesis nodes:

```python
# memory/hybrid.py
from dataclasses import dataclass
from observability.tracing import get_tracer


@dataclass
class Hit:
    key: str
    text: str
    score: float
    source: str  # "vector" | "graph" | "markdown"


def _normalise(hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    hi = max(h.score for h in hits) or 1.0
    return [Hit(h.key, h.text, h.score / hi, h.source) for h in hits]


class HybridRetriever:
    def __init__(self, vector_retriever, graph, markdown, weights):
        self._vector = vector_retriever
        self._graph = graph
        self._markdown = markdown
        self._w = weights  # {"vector":..., "graph":..., "markdown":...}

    def retrieve(self, query: str, session_id: str, top_k: int = 8) -> list[str]:
        with get_tracer().start_as_current_span("memory.hybrid.retrieve") as span:
            vec = _normalise(self._vector_hits(query, top_k))
            grh = _normalise(self._graph_hits(query, top_k))
            mkd = _normalise(self._markdown_hits(session_id))

            merged: dict[str, Hit] = {}
            for hits, w in ((vec, self._w["vector"]),
                            (grh, self._w["graph"]),
                            (mkd, self._w["markdown"])):
                for h in hits:
                    weighted = h.score * w
                    cur = merged.get(h.key)
                    if cur is None or weighted > cur.score:   # dedup, keep max
                        merged[h.key] = Hit(h.key, h.text, weighted, h.source)

            ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
            span.set_attribute("hybrid.vector", len(vec))
            span.set_attribute("hybrid.graph", len(grh))
            span.set_attribute("hybrid.markdown", len(mkd))
            return [h.text for h in ranked[:top_k]]
```

Wire into `agents/nodes.py` so the retrieval node feeds synthesis with all three layers:

```python
# agents/nodes.py (retrieval node — was vector-only)
def retrieval_node(state: GraphState, hybrid: HybridRetriever) -> GraphState:
    with get_tracer().start_as_current_span("agent.retrieval"):
        context = hybrid.retrieve(state["query"], state["session_id"])
        return {**state, "context": context}


def synthesis_node(state: GraphState, llm) -> GraphState:
    with get_tracer().start_as_current_span("agent.synthesis"):
        answer = llm.generate(state["query"], state["context"])
        markdown_memory.append(state["session_id"], f"Q: {state['query']}\nA: {answer}")
        return {**state, "answer": answer}
```

Enable LangSmith for agent traces (gated, key as `SecretStr`):

```python
# observability/langsmith.py
import os
from config import Settings


def init_langsmith(settings: Settings) -> None:
    if not settings.langsmith_tracing or settings.langsmith_api_key is None:
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
```

Commit: `feat(memory): hybrid retrieval feeding synthesis + LangSmith agent traces`.

### Task 7 — Hybrid + trace-emission tests, coverage ratchet, mypy, lock

Add the CI-gate tests: hybrid retrieval feeds synthesis from all three layers, and the key path emits spans.

```python
# tests/test_hybrid_feeds_synthesis.py
def test_all_three_layers_contribute(hybrid_with_fakes):
    out = hybrid_with_fakes.retrieve("q", session_id="s1", top_k=6)
    # fakes return distinct sentinel texts per layer
    assert any("VEC::" in t for t in out)
    assert any("GRAPH::" in t for t in out)
    assert any("MKD::" in t for t in out)


def test_dedup_keeps_max_score(hybrid_dup):
    out = hybrid_dup.retrieve("q", session_id="s1", top_k=3)
    assert len(out) == len(set(out))  # no duplicate text


# tests/test_trace_path.py
def test_key_path_spans_emitted(client, in_memory_exporter):
    client.post("/chat", json={"session_id": "s1", "message": "hi"})
    names = {s.name for s in in_memory_exporter.get_finished_spans()}
    assert {"chat.request", "agent.retrieval",
            "memory.hybrid.retrieve", "agent.synthesis"} <= names
```

Run the full gate and ratchet the coverage floor:

```bash
uv run pytest -q --cov=. --cov-report=term-missing
uv run mypy .
uv run ruff check .
uv lock
```

Bump `--cov-fail-under` in `pyproject.toml` if coverage rose; types and lint must be clean.

Commit: `test(phase7): hybrid-retrieval + trace-emission gates; ratchet coverage`.

> **Roadmap complete.** With Phase 7 merged, the planned upgrade is finished: DI/Settings (P1), Postgres/Auth (P2), multi-provider LLM (P3), async/cache/rate-limit (P5), LangGraph/SSE (P6), and now 3-layer memory + distributed tracing (P7). Remaining items — Neo4j migration, richer ranking, metrics/logs backends — are explicitly future work.

## 6. Exit criteria

Restating the roadmap §5 gate for Phase 7, this phase is done when:

- **Hybrid retrieval feeds synthesis.** The synthesis agent receives context merged from **vector + graph + markdown** memory; tests prove all three layers contribute and that merge/dedup/ranking behave deterministically.
- **Memory is persisted and shared.** Markdown memory lives in Postgres (or S3); the `networkx` knowledge graph is serialised to shared storage and built by the Celery ingestion task. No authoritative memory state is held in-process — Phase 5 statelessness is preserved.
- **Distributed traces are visible per request/user.** Spans are emitted across API → agents → providers → DB/Redis, with trace context propagated into the Celery worker; trace-emission tests assert the key-path spans. LangSmith captures agent-level traces when enabled.
- **CI is green and ratcheted.** `uv run pytest`, `uv run mypy .`, `uv run ruff check .` all pass; the coverage floor is held or raised; any new migration applies cleanly against a fresh testcontainer DB; `uv.lock` is updated.

## Appendix A — 3-layer memory architecture

| Layer | Module | What it stores | Where persisted | Built / updated by | Read by |
| --- | --- | --- | --- | --- | --- |
| Markdown memory | `memory/markdown.py` | Per-session running conversation/notes (bounded summary) | Postgres `session_memory` (S3 alternative) | Synthesis node appends each turn | Hybrid retriever (per `session_id`) |
| Knowledge graph | `memory/graph.py` | Entities + relations as `networkx` nodes/edges | Serialized JSON in Postgres/S3, written under a lock | Entity-extraction pass in `worker/tasks.py` | Hybrid retriever (neighbour lookup) |
| Vectors | existing Pinecone retriever | Chunk embeddings + chunk text/metadata | Pinecone (unchanged) | Existing ingestion `embed_and_upsert` | Hybrid retriever (semantic search) |

Key invariant: **all three layers are persisted/shared**; an API replica or Celery worker may hold a short-lived in-process copy only as a cache, never as the source of truth.

## Appendix B — Hybrid-retrieval merge flow

```
                 query, session_id
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   vector search   graph neighbour   markdown read
   (Pinecone)      lookup (networkx) (Postgres/S3)
        │               │                │
   normalise[0,1]  normalise[0,1]   normalise[0,1]
        │               │                │
        × w_vector      × w_graph        × w_markdown
        └───────────────┼────────────────┘
                        ▼
            merge by stable key (dedup → keep max score)
                        ▼
                sort by score, take top_k
                        ▼
              context list → synthesis node
```

Weights come from `Settings` (`hybrid_weights_vector/graph/markdown`) and are tunable; reciprocal-rank fusion is a drop-in alternative to weighted-sum behind the same interface.

## Appendix C — Tracing span map

| Span | Emitted in | Nested under | Notable attributes |
| --- | --- | --- | --- |
| `chat.request` | `app.py` chat handler | (root, via FastAPIInstrumentor) | `session.id`, `user.id` |
| `agent.retrieval` | `agents/nodes.py` retrieval node | `chat.request` | — |
| `memory.hybrid.retrieve` | `memory/hybrid.py` | `agent.retrieval` | `hybrid.vector/graph/markdown` counts |
| `memory.markdown.read` / `.append` | `memory/markdown.py` | hybrid / synthesis | — |
| `agent.synthesis` | `agents/nodes.py` synthesis node | `chat.request` | — |
| LLM/provider call | LLM abstraction (P3) | `agent.synthesis` | token usage (counted once here) |
| SQLAlchemy spans | `SQLAlchemyInstrumentor` | nearest active span | SQL statement |
| Redis spans | `RedisInstrumentor` | nearest active span | command |
| `ingest.document` | `worker/tasks.py` | extracted request context (cross-boundary) | `doc.id`, `graph.triples` |
| `memory.extract` / `memory.graph.add_entities` | `memory/extract.py`, `memory/graph.py` | `ingest.document` | `doc.id` |

The cross-boundary link is the load-bearing detail: `propagate.inject` writes `traceparent` into the Celery task headers at enqueue, and the worker's `task_prerun` hook calls `propagate.extract` so `ingest.document` and its children appear under the originating request's trace rather than as an orphan.
