# Master Phased Upgrade Roadmap — BYOK + Industry-Standardization

> **Status:** Authoritative roadmap. This document supersedes and consolidates all earlier design
> notes (the previous `docs/01`–`05` files and `system_design_improvements.md`), which were removed
> because they predated the decisions captured here and were no longer accurate.

## 1. Context — why this overhaul

The backend is a working but **monolithic FastAPI RAG app**: Gemini 2.5 Flash for routing +
generation, Pinecone for vectors, HuggingFace for embeddings, S3 for files, DuckDuckGo for web
search. It works for basic flows but carries structural debt that blocks scaling and multi-tenancy:

- **Every external client is a module-level singleton configured at import time** —
  `genai.configure(api_key=GOOGLE_API_KEY)` + `GenerativeModel(...)` at `components/router.py:12-20`
  and `components/generation.py:17-24`; `Pinecone(api_key=...)` at `database/db_manager.py:5`.
  Nothing can be per-user or per-request until this is dismantled. This is the root blocker for BYOK.
- **`genai.configure()` mutates process-global state** — fundamentally incompatible with per-user
  keys under concurrency.
- **Blocking synchronous I/O is invoked from `async def` handlers** (Pinecone, boto3, HuggingFace,
  DuckDuckGo), serializing the event loop.
- **Pinecone is misused as the operational state store** — `has_session_documents`
  (`router.py:122-141`) and `list_s3_keys_for_session` (`db_manager.py:93-117`) reconstruct
  session/file state via dummy-vector `top_k=1000` queries. Slow, eventually-consistent, breaks under
  horizontal scale.
- **`session_id` is client-generated and unauthenticated** (`app.py:180`) — document/session
  ownership is implicit and forgeable.
- No real database, no auth, no rate limiting, `print()` logging, permissive CORS (`*` with
  credentials), ~147 lines of tests, and no CI.

**Goal:** evolve this into a multi-tenant, horizontally-scalable, industry-standard backend whose
defining new capability is **BYOK (Bring Your Own Key)** — users supply their own LLM provider key,
so inference cost and throughput scale with the user base rather than the server, and the operator
can later move inference onto self-upgraded hardware (self-hosted models) without re-architecting.

## 2. Confirmed product decisions

1. **BYOK scope:** **LLM key only.** Pinecone, S3, and HuggingFace remain server-owned.
2. **Key handling:** add **user accounts + authentication**; store each user's LLM key(s)
   **encrypted at rest**. Multi-tenant from the ground up.
3. **LLM layer:** introduce a **multi-provider abstraction** (Gemini, OpenAI, Anthropic) so the same
   BYOK mechanism works across providers and enables future self-hosted/OpenAI-compatible models.
4. **Datastore & scale:** **PostgreSQL** as the operational source of truth + **Redis** for cache,
   rate-limiting, queue, and shared state — designed to run multiple stateless instances behind a
   load balancer.

## 3. Mandatory ordering constraints

The phase order is dependency-driven. Three orderings are **mandatory, not stylistic**:

1. **Postgres (P2) before Auth (P3)** — auth needs a user table and persistent ownership.
2. **Async + DI (P1) and the provider abstraction (P4) before BYOK is actually consumed** —
   `genai.configure` is process-global; per-request keys are unsafe without the DI seam *and* the
   per-call provider clients the abstraction provides.
3. **State off Pinecone (P2) before horizontal scale (P5)** — dummy-vector state queries break across
   multiple stateless instances.

Foundational / non-deferrable: **P0, P1, P2.** Most deferrable: **P6, and especially P7.**

## 4. The phases

### Phase 0 — Quality & Safety Foundation *(non-deferrable, first)*
**Objective:** establish guardrails so every later phase is safe to merge — quality built in, not bolted on.
- `ruff` + `black` + `mypy` (`pyproject.toml`), `.pre-commit-config.yaml`, GitHub Actions CI
  (lint, type-check, pytest, coverage gate starting at today's baseline and ratcheting up).
- Replace `print()` with `structlog` structured logging.
- Centralize config into a single Pydantic `Settings` object, replacing the scattered
  `os.getenv`/`load_dotenv` calls in `config.py` and inside every integration module; fail fast on
  missing required secrets.
- Audit and prune `requirements.txt` (it currently carries unrelated packages, e.g. `nipype`,
  `nibabel`, `pyxnat`).
- **Files:** new `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`; rewrite `config.py`.
- **Exit:** CI green and enforced on PRs; no `print()` in app/components; single Settings source.

### Phase 1 — Async I/O Refactor + Dependency Injection *(foundational)*
**Objective:** make I/O truly non-blocking and replace import-time singletons with injectable,
lifecycle-managed clients — the seam every later phase hangs on.
- Create clients in a FastAPI `lifespan` and provide them via `Depends` (not at import).
- Make blocking calls non-blocking: `asyncio.to_thread` as the pragmatic first step; migrate S3/HF to
  `httpx`/`aioboto3` where it pays off. Add `tenacity` retry/backoff (already installed).
- **Files:** `app.py` (lifespan + DI wiring), all four integration clients, `database/db_manager.py`,
  `components/retrieval.py`, `components/preprocessing.py`.
- **Exit:** no blocking call inside an async path; clients resolved via DI; concurrency no longer serialized.

### Phase 2 — PostgreSQL + Migrate State Off Pinecone *(foundational)*
**Objective:** make Postgres the operational source of truth; stop abusing the vector store for state.
- Async SQLAlchemy + Alembic migrations. Tables: `sessions`, `documents` (s3_key, filename, status,
  session_id), ingestion-job status.
- Replace `has_session_documents` and `list_s3_keys_for_session` with Postgres queries; Pinecone
  reverts to pure vector search.
- **Files:** new `database/models.py`, `migrations/`; `database/db_manager.py` (strip state
  functions); `components/router.py`; `app.py` (upload/cleanup); `components/preprocessing.py`.
- **Exit:** zero dummy-vector state queries remain; session/file state served from Postgres; Alembic
  migration runs in CI.

### Phase 3 — User Accounts, Auth & Encrypted BYOK Key Storage
**Objective:** multi-tenant identity + encrypted-at-rest per-user LLM keys.
- `users` table; registration/login; JWT (or session) auth dependency protecting `/api/chat`,
  `/api/upload`, `/api/cleanup`.
- Rebind `session_id` ownership to the authenticated user; reject cross-user session/document access
  (closes the forgeable-session hole).
- `user_llm_keys` table storing keys **encrypted at rest** (`cryptography` Fernet / envelope
  encryption; encryption key from Settings/secret manager, never in the DB, never logged). Endpoints
  to add / rotate / delete keys.
- Tighten CORS (`app.py:35-41` — currently `*` with credentials, an unsafe/invalid combination).
- **Files:** new `auth/`, `database/models.py` (users, keys), `app.py`, Settings.
- **Exit:** endpoints require auth; ciphertext-at-rest verified (no plaintext in DB or logs);
  cross-user isolation enforced; CORS locked to known origins.

### Phase 4 — Multi-Provider LLM Abstraction + Per-Request BYOK Clients *(the BYOK payoff)*
**Objective:** a provider-agnostic LLM layer (Gemini, OpenAI, Anthropic) instantiated per-request
with the authenticated user's decrypted key.
- `llm/` package: an `LLMProvider` protocol (`route()`/`generate()`/`stream()`) with concrete
  Gemini / OpenAI / Anthropic adapters (`openai` already installed; add `anthropic`).
- Remove the global `genai.configure()` + module-level `GenerativeModel`. Per request: load user →
  decrypt key (in memory only) → build provider client → inject via DI.
- Generalize the Gemini-specific 403/429/503 error mapping in `router.py`/`generation.py` into a
  provider-neutral taxonomy in `exceptions.py`.
- **Files:** new `llm/`; rewrite `components/router.py` + `components/generation.py` to accept an
  injected provider; `app.py` chat path; `exceptions.py`.
- **Exit:** a user with an OpenAI key and a user with a Gemini key work concurrently with no
  cross-talk; no process-global LLM config; provider selected per user/request.

### Phase 5 — Redis, Rate Limiting, Queue-Based Ingestion, Horizontal Scale
**Objective:** become genuinely horizontal-scale ready.
- Redis for cache, shared state, and per-user / per-key rate-limit counters (`slowapi` or equivalent).
- Replace FastAPI `BackgroundTasks` ingestion (`app.py:152`, which dies on instance shutdown) with
  **Celery + Redis broker**; `process_file_pipeline` becomes a task with status tracked in Postgres.
- **S3 presigned uploads:** client uploads directly to S3; backend issues the presigned URL and
  enqueues ingestion — removes large-file passthrough from the API process.
- Confirm full statelessness: any instance behind the load balancer can serve any request.
- **Files:** new `worker/` (Celery app + tasks); `app.py` upload endpoint (presigned + enqueue);
  `integrations/s3/client.py` (presigned URL generation); rate-limit middleware; Redis via DI.
- **Exit:** upload on instance A + chat on instance B passes an integration test; ingestion survives
  instance restart; per-user rate limits enforced; no file passthrough through the API process.

### Phase 6 — LangGraph Multi-Agent Orchestrator + SSE Streaming + Rich Output
**Objective:** replace the linear `route → relevance → retrieve → generate` flow with a graph-based
orchestrator and stream results.
- LangGraph state graph: supervisor + sub-agents (web-search / vector / synthesis; `langgraph`
  already installed). Replaces the manual `decide_combined_route` logic (`app.py:103-135`).
- SSE on `/api/chat` (stream tokens + intermediate sub-agent status events).
- Structured Markdown / JSON rich output in the synthesis prompt (images via `![alt](url)`,
  interactive component schemas).
- **Files:** new `agents/`; `app.py` chat endpoint (SSE + graph invocation);
  `components/retrieval.py` / `generation.py` become agent nodes calling the **injected per-user
  provider** (not Gemini globals).
- **Exit:** graph orchestrates parallel retrieval; SSE streams to the client; each node uses the
  per-user provider.

> **Detailed agentic design:** see [`09_Phase6_Agentic_Architecture.md`](./09_Phase6_Agentic_Architecture.md) — the authoritative decision record refining Phase 6 (LangGraph nodes, freemium BYOK→free-tier provider ladder, Markdown + component-JSON output).

### Phase 7 — 3-Layer Memory + Observability/Tracing *(most deferrable, last)*
**Objective:** richer memory and end-to-end tracing.
- 3-layer memory: per-session markdown memory + knowledge graph (`networkx` MVP, Neo4j later) +
  existing vectors; entity-extraction pass in the ingestion task.
- OpenTelemetry tracing across API → agents → providers → DB/Redis; LangSmith (already installed) for
  agent traces.
- **Files:** new `memory/`; `worker/` ingestion task (entity extraction); agent nodes (hybrid
  retrieval); tracing instrumentation/middleware.
- **Exit:** hybrid retrieval (vector + graph + markdown) feeds the synthesis agent; per-request/user
  distributed traces visible.

## 5. Cross-cutting — tests + CI gate per phase

Coverage is built in incrementally; each phase adds its gate to the Phase 0 CI:

| Phase | Added test/CI gate |
|-------|--------------------|
| P0 | lint / type-check / coverage baseline gate |
| P1 | async-path tests (no event-loop blocking); DI-override fixtures |
| P2 | Alembic migration test; repository-layer tests; assert no Pinecone state queries remain |
| P3 | auth tests (401/403, cross-user isolation); **assert keys are ciphertext at rest and never logged** |
| P4 | provider-adapter contract tests (mocked SDKs); concurrent multi-user/multi-provider isolation |
| P5 | multi-instance integration test behind LB; Celery task + rate-limit + presigned-upload tests |
| P6 | graph-node unit tests; SSE streaming test |
| P7 | hybrid-retrieval tests; trace-emission assertions |

## 6. Dependencies to add (over the phases)

`redis`, `sqlalchemy`, `alembic`, `celery`, `structlog` (or `python-json-logger`), `cryptography`,
a JWT/auth lib (`pyjwt`/`python-jose` + `passlib`), `slowapi`, `anthropic`,
`ruff`/`black`/`mypy`/`pre-commit`, and the OpenTelemetry packages.

Already present: `openai`, `langgraph`, `networkx`, `httpx`, `tenacity`, `langsmith`.
(`langchain` is installed but currently only its text splitter is used.)

## 7. Ordering summary

```
P0 Quality & CI
  → P1 Async + DI
    → P2 Postgres + state off Pinecone
      → P3 Auth + encrypted BYOK key storage
        → P4 Multi-provider + per-request BYOK   ← the BYOK capability goes live here
          → P5 Redis + rate-limit + queue + presigned (scale-out)
            → P6 LangGraph orchestrator + SSE + rich output
              → P7 3-layer memory + observability
```
