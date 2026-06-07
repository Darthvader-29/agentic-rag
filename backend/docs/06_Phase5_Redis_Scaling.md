# Phase 5 — Redis, Rate Limiting, Queue-Based Ingestion, Horizontal Scale

> **For the implementer:** execute the tasks **in order**; each ends with a verification that must
> pass before its commit. The tree stays releasable after every task. Companion to
> [`00_Master_Upgrade_Roadmap.md`](./00_Master_Upgrade_Roadmap.md) §4 (Phase 5) and follows
> [`05_Phase4_Multi_Provider_LLM_Abstraction.md`](./05_Phase4_Multi_Provider_LLM_Abstraction.md).
>
> **Doc numbering:** `00` = master roadmap; `01` = Phase 0; `02` = Phase 1; …; `06` = this Phase 5 detail doc.
>
> **Ordering gate (roadmap §3, constraint 3):** **state must be off Pinecone (Phase 2) before this
> phase.** Horizontal scale means multiple stateless instances behind a load balancer; the
> dummy-vector state queries (`has_session_documents`, `list_s3_keys_for_session`) break under that
> topology. Confirm Phase 2 landed (state served from Postgres, Pinecone is pure vector search)
> **before** starting Phase 5.

## 1. Objective & scope

Make the backend **genuinely horizontal-scale ready**: multiple identical, **stateless** FastAPI
instances behind a load balancer, where **any** instance can serve **any** request, and long-running
ingestion **survives an instance restart** because it runs in a separate worker pool rather than in
the API process. Phase 5 does not change the product surface (same routes, same RAG flow); it changes
**where work runs** and **how requests are throttled and load-balanced**.

**In scope:**
- **Redis** as the process-external store for cache, shared ephemeral state, and rate-limit counters.
  Built **once per process in `lifespan`**, stored on `app.state`, and exposed through a `Depends`
  provider (extends the Phase 1 DI seam in `dependencies.py`). New `redis` service in
  `docker-compose.yml`; new `REDIS_URL` in `Settings`.
- **Rate limiting with `slowapi`** (Redis-backed storage), keyed on the **authenticated user**
  (Phase 3) and falling back to **client IP** for anonymous traffic. Wired as middleware + per-route
  limits on `/api/chat` and `/api/upload` (and the new confirm endpoint). `/health` exempt.
- **Queue-based ingestion** replacing FastAPI `BackgroundTasks`: a new **`worker/` package** holding a
  **Celery** app (Redis broker) and tasks. `process_file_pipeline` (currently invoked via
  `background_tasks.add_task(...)` at `app.py:154-159`, which dies on instance shutdown) becomes a
  **Celery task**; ingestion status is tracked in **Postgres** (the Phase 2 documents / ingestion-job
  table). The worker runs as a separate process and survives API restarts.
- **S3 presigned uploads:** clients upload **directly to S3/MinIO**. `/api/upload` issues a presigned
  **PUT** URL; a second **confirm** endpoint verifies the object landed (`head_object`) and enqueues
  the Celery ingestion task. This removes large-file passthrough (`upload_fileobj_to_s3(file.file, …)`
  at `app.py:152`) from the API process. Add a `generate_presigned_url`-style method to `S3Client`.
- **Confirmed statelessness:** no in-process request state; the only per-process objects are
  connection pools (Redis, S3, Pinecone, DB) rebuilt on every instance via `lifespan`.

**Explicitly deferred** (do **not** do here):
- **LangGraph multi-agent orchestration + SSE streaming** to the client — **Phase 6**. Phase 5 keeps
  the linear `route → relevance → retrieve → generate` flow and the JSON `/api/chat` response.
- **3-layer memory + OpenTelemetry tracing** — **Phase 7**. No entity extraction is added to the
  ingestion task here; it stays the existing download → parse → chunk → embed → upsert pipeline.
- **Autoscaling policy, load-balancer config, and splitting Redis** (broker vs cache vs limiter onto
  separate instances). A single Redis serves broker + cache + limiter in this phase; the split is
  noted as future work (roadmap §7).

## 2. Decisions & rationale

| Decision | Rationale |
|---|---|
| **Single Redis (built in `lifespan`, injected via `Depends`)** for cache + shared state + limiter + Celery broker | One dependency, one container, one connection-pool lifecycle. Reuses the exact Phase 1 seam (`app.state.* ` + provider function). Splitting Redis per-concern is premature; revisit only if contention shows (roadmap §7). |
| **`slowapi`** for rate limiting (roadmap §6 dependency) | Mature Starlette/FastAPI integration: per-route `@limiter.limit(...)` decorators + middleware + pluggable Redis storage. Hand-rolling a token bucket is more code and untested; nginx-only limits cannot key per authenticated user. |
| **Limiter keyed on user id, fall back to IP** | Per-tenant fairness — one user (or one NAT'd office) cannot exhaust another's budget. IP fallback still protects the anonymous/auth surface. Reads the Phase 3 authenticated user. |
| **Celery + Redis broker** for ingestion (roadmap §4) | Durable, retryable, observable, survives API restarts; standard ops story. `BackgroundTasks` runs *in* the API process and is lost on shutdown/redeploy. RQ has fewer features; ad-hoc threads have no durability. |
| **The Celery task builds its own clients from `Settings`** | A worker has **no FastAPI request scope** — `Depends` does not exist there. `process_file_pipeline` already takes plain args and (post-Phase-1) constructs `S3Client`/embedder/`PineconeClient`; the task is a thin durable wrapper around it. Only **JSON-serializable** args cross the broker. |
| **S3 presigned PUT + confirm/enqueue (two-step)** | Removes file bytes (and the memory/bandwidth) from the API process; the client talks to S3 directly. The confirm step lets us `head_object` before enqueuing, closing the presign/ingest race. Presigned **POST** adds form-field surface we do not need. |
| **Ingestion status in Postgres, not Redis** | Postgres is the Phase 2 source of truth any instance can poll (`/api/documents/{id}`). Redis is a cache/broker, not the system of record; a flush must not lose ingestion state. |
| **Celery eager mode in tests** | `task_always_eager=True` runs `delay()` inline and synchronously, so the suite never blocks waiting on a real worker/broker. A real-worker job is optional/out-of-band. |
| **`fakeredis` (or a Redis fixture) in tests** | Keeps the suite offline and deterministic (Phase 0 invariant). `slowapi` storage points at the fake/fixtured Redis so limiter counters are exercised without a container. |
| **New deps: `redis`, `celery`, `slowapi`** (roadmap §6) | The only three additions; everything else (Postgres, S3/MinIO, DI seam, auth) is already present from Phases 1–4. |

## 3. Current-state snapshot (verified)

> Snapshot at the **start of Phase 5, assuming Phases 1–4 are complete**. Verify each line against the
> repo before trusting it; the tree may have advanced.

- **DI seam exists (Phase 1).** Clients are built in `lifespan` on `app.state` and injected via
  `Depends` provider functions in `dependencies.py`; `app.py` declares them as `Depends(...)` params.
  `grep -rn "Depends(" app.py dependencies.py` confirms. Redis and the limiter wire in the same way.
- **Postgres is the source of truth (Phase 2),** including a `documents` row (s3_key, filename,
  status, session_id) and ingestion-job status. `process_file_pipeline` updates that status; any
  instance can read it. No dummy-vector state queries remain.
- **Vectors live in Pinecone (Phase 2),** used for pure vector search only — the precondition for
  multi-instance (roadmap §3.3).
- **Auth + per-user keys exist (Phase 3).** A `get_current_user` dependency protects `/api/chat`,
  `/api/upload`, `/api/cleanup`; `session_id`/document ownership is bound to the authenticated user.
  CORS is locked to known origins (no longer `*` with credentials).
- **Per-request multi-provider LLM (Phase 4).** The chat path builds a provider client per request
  from the user's decrypted key; it is already stateless per request. Phase 5 only **rate-limits** it.
- **Ingestion still runs via FastAPI `BackgroundTasks`.** `app.py:142` injects `BackgroundTasks`;
  `app.py:154-159` does `background_tasks.add_task(process_file_pipeline, s3_key, filename,
  session_id, …)`. **This dies on instance shutdown/redeploy** — the work to replace.
- **Uploads still pass through the API process.** `app.py:152` calls
  `upload_fileobj_to_s3(file.file, filename)` (post-Phase-1: `await s3.upload_fileobj(file.file, …)`),
  so the whole file streams through the API. To remove.
- **`process_file_pipeline` already takes plain args and builds its own clients** (post-Phase-1
  signature `(file_key, filename, session_id, s3, embedder, pinecone)`; the body is the existing
  download → parse → chunk → embed → upsert flow in `components/preprocessing.py`). Easy lift into a
  Celery task — pass `document_id` + `s3_key`, let the task construct clients from `Settings`.
- **`S3Client` (post-Phase-1) exposes `upload_fileobj` / `download_to_temp` / `delete_objects`** built
  from `Settings.from_settings(...)` with the MinIO `endpoint_url` switch. **No presigned-URL method
  yet** — to add.
- **`Settings`** (`config.py`) is a single `pydantic-settings` object with **UPPERCASE** field names
  (`AWS_REGION`, `S3_BUCKET_NAME`, …) and a module-level `settings` singleton. **No `REDIS_URL` and no
  rate-limit settings yet** — to add (same UPPERCASE convention).
- **`docker-compose.yml`** exists (Phase 1 MinIO; Phase 2 Postgres). **No `redis` and no `worker`
  service yet** — to add.
- **CI is Jenkins** (`Jenkinsfile`), with a coverage floor recorded in `pyproject.toml` + the
  `Jenkinsfile`, **ratcheted upward only** each phase. Tests are offline (`conftest.py` dummies);
  `pytest-asyncio` `asyncio_mode = "auto"`.

## 4. Risks & gotchas (with resolutions)

1. **Celery worker cannot use FastAPI `Depends`.** There is no request/response cycle in a worker, so
   `Depends` resolution never runs. **Resolution:** the task constructs `Settings()` and builds its
   own `S3Client` / embedder / `PineconeClient` exactly as `process_file_pipeline` already does. Pass
   only **plain, JSON-serializable** args across the broker (`document_id: str`, `s3_key: str`) — never
   a client object, a DB session, or a `Depends`-resolved instance.

2. **Presigned-upload / ingestion race.** If we enqueue before the client finishes the PUT, the worker
   downloads a missing or partial object. **Resolution:** a two-step flow — presign → client PUTs →
   **confirm** endpoint does `s3.object_exists(key)` (`head_object`) **before** `task.delay(...)`. If
   the head check fails, set the document status `failed` and return `409`; never enqueue.

3. **Rate-limit keying.** IP-only keying lets one NAT/tenant starve others; user-only keying breaks
   anonymous/health traffic that has no user. **Resolution:** the key function returns
   `f"user:{user.id}"` when authenticated, else `get_remote_address(request)`. The authenticated user
   must be visible to the limiter **before** the route body runs — see gotcha 4. `/health` carries no
   decorator (or `@limiter.exempt`) so liveness/readiness probes never `429`.

4. **`slowapi` needs the user on `request.state`, not only via `Depends`.** The limiter key function
   runs in middleware, **before** the route's `Depends(get_current_user)` resolves. **Resolution:** add
   a tiny dependency/middleware that decodes the token and stashes the user on `request.state.user`
   (idempotent with the Phase 3 auth dependency), so the key function can read it. If no user is
   present (anonymous/`/health`), fall back to IP.

5. **`slowapi` defaults to in-memory storage → limits not shared across instances.** With multiple
   instances each would keep its own counters. **Resolution:** construct the `Limiter` with
   `storage_uri=settings.REDIS_URL`. A test asserts a second limiter instance pointed at the same
   (fake/fixtured) Redis sees the counter — proving shared, not per-process, throttling.

6. **No in-process request state may sneak in.** Any module-level mutable cache diverges across
   instances and breaks "upload on A, chat on B". **Resolution:** forbid module-level mutable state;
   all shared state goes to Redis / Postgres / Pinecone. The multi-instance integration test (Task 7)
   is the guard.

7. **Redis connection lifecycle.** A client built per request exhausts connections; one built at
   import time cannot be closed cleanly and breaks offline test collection. **Resolution:** build one
   pooled `redis.asyncio` client in **`lifespan` startup**, stash it on `app.state.redis`, `aclose()`
   it on shutdown; the `Depends` provider only **reads** it from `app.state` (mirrors the Phase 1
   client pattern). No network at construction — `from_url` is lazy, so `conftest.py` dummies + offline
   tests still pass.

8. **Celery + pytest hangs on a real broker.** A test calling `delay()` blocks waiting on a worker.
   **Resolution:** force **eager mode** in test config (`task_always_eager=True`,
   `task_eager_propagates=True`) so `delay()` runs inline and exceptions surface. No broker/worker
   container in CI.

9. **Lost error path in ingestion.** A failure must not leave status stuck at `pending`.
   **Resolution:** the task wraps the pipeline; on exception it sets status `failed`, logs with
   `exc_info=True`, and re-raises so Celery's `autoretry_for` / `max_retries` / `retry_backoff` apply.
   `process_file_pipeline` already sets `failed` on its own error path; the task re-asserts it in case
   the failure occurred before that point.

10. **`task_acks_late` + restart redelivery.** With late acks a task interrupted by a worker restart is
    redelivered. Ingestion must be **idempotent** (re-upserting the same `document_id`/chunk ids is a
    no-op overwrite in Pinecone, and the status write is last-writer-wins). **Resolution:** keep the
    deterministic chunk-id scheme (`{session}_{filename}_{i:04d}`) so re-runs overwrite rather than
    duplicate; document this as the idempotency guarantee.

11. **Coverage dips when adding code.** New `worker/` + endpoints without tests drop the ratio below
    the Jenkins floor. **Resolution:** add tests in the **same** PR as the code; ratchet the floor
    **last** (Task 8), upward only.

## 5. Tasks (ordered)

> Conventional-commit message per task. `uv run <cmd>` runs inside the project venv. Each task leaves
> the suite green and the tree releasable. TDD (RED/GREEN) is called out where it is natural.

### Task 1 — Deps + Settings: `REDIS_URL` + rate-limit config (TDD)
**Files:** `pyproject.toml` (add deps); `config.py`; `test/test_config.py`; `conftest.py`; `.env.example`.

Add the three Phase 5 dependencies:
```bash
uv add redis celery slowapi
uv lock
uv export --no-hashes --no-dev -o requirements.txt
```

**RED** — extend `test/test_config.py` (delenv the new keys in `_fresh` first):
```python
def test_redis_url_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED); assert c.settings.REDIS_URL == "redis://localhost:6379/0"
def test_rate_limit_defaults(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.RATE_LIMIT_CHAT == "30/minute"
    assert c.settings.RATE_LIMIT_UPLOAD == "10/minute"
def test_celery_broker_falls_back_to_redis(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.celery_broker_url == c.settings.REDIS_URL
```

**GREEN** — add to `Settings` in `config.py` (keep the UPPERCASE convention; `celery_broker_url`
property defaults to `REDIS_URL`):
```python
    # --- Phase 5: Redis / scaling ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str | None = None          # falls back to REDIS_URL
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_UPLOAD: str = "10/minute"
    RATE_LIMIT_DEFAULT: str = "120/minute"

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL
```
Add `"REDIS_URL": "redis://localhost:6379/0"` to `conftest.py` `_DUMMY` (lazy `from_url`, no network).
Append to `.env.example`:
```dotenv
# Phase 5
REDIS_URL=redis://localhost:6379/0
# CELERY_BROKER_URL=        # optional; defaults to REDIS_URL
RATE_LIMIT_CHAT=30/minute
RATE_LIMIT_UPLOAD=10/minute
RATE_LIMIT_DEFAULT=120/minute
```
**Verify:** `uv run pytest test/test_config.py -q` green.
**Commit:** `feat(config): add REDIS_URL + rate-limit settings; add redis/celery/slowapi deps`

### Task 2 — `redis` service in `docker-compose.yml` + Redis client in `lifespan` + DI
**Files:** `docker-compose.yml`; `app.py` (lifespan); `dependencies.py`; `test/test_dependencies.py`.

Add the `redis` service and wire `REDIS_URL` into the API service (full compose in Appendix A):
```yaml
  redis:
    image: redis:7
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

In `app.py` `lifespan` startup, build one pooled client; close it on shutdown:
```python
import redis.asyncio as aioredis
...
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("redis_initialized", url=settings.REDIS_URL)
    yield
    await app.state.redis.aclose()
```

In `dependencies.py`, add the provider (read from `app.state`, never build per request):
```python
import redis.asyncio as aioredis

def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis
```

**Verify:** `docker compose config` parses; `uv run pytest -q` green. A DI-override test sets/gets a
key through the dependency (use `fakeredis.aioredis.FakeRedis` in the fixture) and asserts a value
written via one call is read via another.
**Commit:** `feat(app): build pooled Redis client in lifespan; expose via Depends`

### Task 3 — `slowapi` limiter wired (per-user key) on endpoints (TDD)
**Files:** `app.py` (limiter, handler, middleware, decorators); `auth/*` or `dependencies.py` (stash
user on `request.state`); `test/test_rate_limit.py`.

**RED** — `test/test_rate_limit.py` (point the limiter at a fake Redis via override):
```python
def test_chat_rate_limited_per_user(client, auth_headers):
    last = None
    for _ in range(40):                       # RATE_LIMIT_CHAT = 30/minute
        last = client.post("/api/chat", json={"message": "hi"}, headers=auth_headers)
    assert last.status_code == 429

def test_two_users_dont_share_a_bucket(client, auth_headers_a, auth_headers_b):
    for _ in range(30):
        client.post("/api/chat", json={"message": "x"}, headers=auth_headers_a)
    # user B still has a full bucket
    assert client.post("/api/chat", json={"message": "x"}, headers=auth_headers_b).status_code != 429

def test_health_is_exempt(client):
    for _ in range(200):
        assert client.get("/health").status_code == 200
```

**GREEN** — in `app.py`, build the limiter, register the handler + middleware, decorate routes:
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

def _rate_limit_key(request) -> str:
    user = getattr(request.state, "user", None)
    return f"user:{user.id}" if user else get_remote_address(request)

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=settings.REDIS_URL,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
```
```python
@app.post("/api/chat")
@limiter.limit(settings.RATE_LIMIT_CHAT)
async def chat(request: Request, body: ChatRequest, user=Depends(get_current_user), ...):
    ...
```
Add (or extend) a dependency/middleware so the authenticated user is on `request.state.user` before
the limiter key function runs (gotcha 4). Leave `/health` undecorated (or `@limiter.exempt`).
**Verify:** `uv run pytest test/test_rate_limit.py -q` green.
**Commit:** `feat(api): per-user Redis-backed rate limiting (slowapi) on chat/upload`

### Task 4 — `worker/` package: Celery app + broker config
**Files:** create `worker/__init__.py`, `worker/celery_app.py`; `conftest.py` (eager fixture);
`test/test_celery_app.py`; `pyproject.toml` (coverage `source`/mypy targets include `worker`).

`worker/celery_app.py`:
```python
from celery import Celery
from config import settings

broker = settings.celery_broker_url

celery_app = Celery(
    "rag",
    broker=broker,
    backend=broker,
    include=["worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
```

In `conftest.py`, force eager mode so `delay()` runs inline (gotcha 8):
```python
import pytest

@pytest.fixture(autouse=True)
def _celery_eager():
    from worker.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
```

**RED/GREEN** — `test/test_celery_app.py` asserts the app loads and the task is registered after
Task 5:
```python
def test_celery_app_registers_ingest_task():
    from worker.celery_app import celery_app
    assert "worker.tasks.ingest_document" in celery_app.tasks
```
**Verify:** `uv run pytest test/test_celery_app.py -q` green (after Task 5); `uv run mypy worker` clean.
**Commit:** `feat(worker): add Celery app with Redis broker + eager test config`

### Task 5 — Move `process_file_pipeline` into a Celery task (status → Postgres)
**Files:** create `worker/tasks.py`; `components/preprocessing.py` (ensure it accepts plain args +
sets status); `test/test_ingest_task.py`.

The task **builds its own clients from `Settings`** (no `Depends`, gotcha 1) and wraps the existing
pipeline; status transitions stay in Postgres (gotcha 9):
```python
import structlog
from worker.celery_app import celery_app
from config import Settings

logger = structlog.get_logger(__name__)

@celery_app.task(
    name="worker.tasks.ingest_document",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
)
def ingest_document(self, *, document_id: str, s3_key: str, filename: str, session_id: str) -> None:
    settings = Settings()                          # the task owns its config + clients
    s3 = S3Client.from_settings(settings)
    embedder = HuggingFaceClient.from_settings(settings)
    pinecone = PineconeClient.from_settings(settings)
    try:
        _set_status(document_id, "processing", settings)
        process_file_pipeline(s3_key, filename, session_id, s3, embedder, pinecone)
        _set_status(document_id, "complete", settings)
    except Exception:
        logger.error("ingest_task_failed", document_id=document_id, exc_info=True)
        _set_status(document_id, "failed", settings)
        raise                                      # let autoretry_for / max_retries apply
```
> `process_file_pipeline` is `async` today; call it from the (sync) task via
> `asyncio.run(process_file_pipeline(...))`, **or** add a thin sync entrypoint. `_set_status` opens its
> own short-lived DB session (the task is sync; do not reuse a request-scoped session). Keep the
> deterministic chunk-id scheme for idempotent re-runs (gotcha 10). The pipeline remains the **single**
> ingestion implementation — the task is a durable wrapper.

**RED/GREEN** — `test/test_ingest_task.py` (eager mode): a successful run ends `complete`; a forced
pipeline error ends `failed` and exhausts retries:
```python
def test_ingest_marks_complete(monkeypatch, fake_repo):
    monkeypatch.setattr("worker.tasks.process_file_pipeline", lambda *a, **k: None)
    ingest_document.delay(document_id="d1", s3_key="uploads/u/x.pdf",
                          filename="x.pdf", session_id="s1").get()
    assert fake_repo.status("d1") == "complete"

def test_ingest_marks_failed(monkeypatch, fake_repo):
    def boom(*a, **k): raise RuntimeError("parse error")
    monkeypatch.setattr("worker.tasks.process_file_pipeline", boom)
    with pytest.raises(RuntimeError):
        ingest_document.delay(document_id="d2", s3_key="k", filename="x", session_id="s").get()
    assert fake_repo.status("d2") == "failed"
```
**Verify:** `uv run pytest test/test_ingest_task.py -q` green; `uv run mypy worker` clean.
**Commit:** `feat(worker): Celery ingest task building own clients; status -> Postgres + retries`

### Task 6 — `S3Client.generate_presigned_url` + rewrite `/api/upload` (presigned + enqueue)
**Files:** `integrations/s3/client.py` (presign + `object_exists`); `app.py` (two-step upload, **drop
`BackgroundTasks`**); `test/test_upload.py`.

Add to `S3Client` (sync method offloaded via `to_thread`, mirroring the Phase 1 pattern):
```python
@retry(**_RETRY)
def _presign_put_sync(self, key: str, expires_in: int) -> str:
    return self._client.generate_presigned_url(
        "put_object",
        Params={"Bucket": self._bucket, "Key": key},
        ExpiresIn=expires_in,
    )

async def generate_presigned_url(self, key: str, *, expires_in: int = 900) -> str:
    return await asyncio.to_thread(self._presign_put_sync, key, expires_in)

def _head_sync(self, key: str) -> bool:
    try:
        self._client.head_object(Bucket=self._bucket, Key=key)
        return True
    except self._client.exceptions.ClientError:
        return False

async def object_exists(self, key: str) -> bool:
    return await asyncio.to_thread(self._head_sync, key)
```

Rewrite `app.py` upload as **request → confirm**, removing `BackgroundTasks` and the passthrough:
```python
class UploadInit(BaseModel):
    filename: str
    content_type: str | None = None

class UploadConfirm(BaseModel):
    document_id: str
    s3_key: str

@app.post("/api/upload")                                   # step 1: issue presigned URL
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload(request: Request, body: UploadInit,
                 user=Depends(get_current_user),
                 s3: S3Client = Depends(get_s3_client),
                 repo=Depends(get_document_repository)):
    key = f"uploads/{user.id}/{uuid.uuid4()}_{body.filename}"
    url = await s3.generate_presigned_url(key)
    doc = await repo.create_document(user_id=user.id, filename=body.filename,
                                     s3_key=key, status="pending")
    return {"document_id": doc.id, "upload_url": url, "s3_key": key}

@app.post("/api/upload/confirm")                           # step 2: verify + enqueue
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def confirm_upload(request: Request, body: UploadConfirm,
                         user=Depends(get_current_user),
                         s3: S3Client = Depends(get_s3_client),
                         repo=Depends(get_document_repository)):
    doc = await repo.get_document(body.document_id, user_id=user.id)
    if doc is None:
        raise AppException(status_code=404, detail="document not found")
    if not await s3.object_exists(body.s3_key):            # close the presign/ingest race
        await repo.set_status(doc.id, "failed")
        raise AppException(status_code=409, detail="object not uploaded")
    from worker.tasks import ingest_document
    ingest_document.delay(document_id=doc.id, s3_key=body.s3_key,
                          filename=doc.filename, session_id=doc.session_id)
    return {"document_id": doc.id, "status": "queued"}
```
> Remove the `BackgroundTasks` import + parameter, the `upload_fileobj_to_s3(file.file, …)` /
> `await s3.upload_fileobj(...)` passthrough, and `background_tasks.add_task(process_file_pipeline,
> …)`. The API never touches file bytes now: client → presigned PUT → S3, then confirm →
> `head_object` → `delay()`.

**RED/GREEN** — `test/test_upload.py`: init returns `upload_url` + `document_id`; confirm with a
missing object → `409` and status `failed`; confirm with a present object enqueues (eager) and ends
`queued`/`complete`; cross-user confirm → `404`.
**Verify:** `uv run pytest test/test_upload.py -q` green; `grep -rn "BackgroundTasks" app.py` → 0.
**Commit:** `feat(api): presigned-PUT upload + confirm/enqueue; drop BackgroundTasks passthrough`

### Task 7 — Statelessness / multi-instance integration + presigned + rate-limit tests
**Files:** `test/test_multi_instance.py`; `test/test_statelessness.py`.

The roadmap §5 P5 gate: build **two** app instances sharing the same Postgres + Redis + (fake) S3;
ingest via instance **A**, then poll/chat via instance **B**:
```python
def test_upload_on_a_chat_on_b(app_a, app_b, shared_backends, auth_headers):
    ca, cb = TestClient(app_a), TestClient(app_b)
    init = ca.post("/api/upload", json={"filename": "x.pdf"}, headers=auth_headers).json()
    # ... client PUTs bytes to (fake) S3 at init["upload_url"] ...
    ca.post("/api/upload/confirm",
            json={"document_id": init["document_id"], "s3_key": init["s3_key"]},
            headers=auth_headers)
    # instance B sees the same durable state (Postgres) + vectors (Pinecone)
    doc = cb.get(f"/api/documents/{init['document_id']}", headers=auth_headers).json()
    assert doc["status"] in {"queued", "complete"}
    assert cb.post("/api/chat", json={"message": "what is in x?"},
                   headers=auth_headers).status_code == 200
```
Also assert **ingestion survives an instance restart**: enqueue, drop `app_a`, and confirm the
task result/status is still reachable from `app_b` (the worker is a separate process; eager mode
stands in for it in CI). A statelessness test asserts there is **no module-level mutable request
state** (limiter counters and ingestion status live in Redis/Postgres, not in the process).
**Verify:** `uv run pytest test/test_multi_instance.py test/test_statelessness.py -q` green.
**Commit:** `test: multi-instance (upload A / chat B) + restart-survival + statelessness`

### Task 8 — Coverage ratchet, mypy, lock & docs
**Files:** `pyproject.toml` (coverage floor + `source`/mypy targets include `worker`); `Jenkinsfile`
(floor + mypy targets + optional broker note); this doc; `README.md`.
```bash
uv run pytest --cov --cov-report=term-missing
```
Read `TOTAL`; raise `--cov-fail-under` to `floor(new TOTAL)` (**upward only**); record the integer
here and in `Jenkinsfile`. Add `worker` to `[tool.coverage.run] source` and to the mypy target list.
Run `uv run mypy app.py config.py dependencies.py worker components database integrations` and fix
what it flags. Re-lock if `pyproject.toml` changed (`uv lock`; regenerate `requirements*.txt`). Update
`README.md` with the `docker compose up -d redis worker` dev workflow.
**Verify:** `uv run ruff check . && uv run ruff format --check . && uv run mypy <targets> && uv run
pytest --cov --cov-fail-under=<new>` → all exit 0.
**Commit:** `test: ratchet coverage gate for Phase 5; mypy clean; document worker/redis workflow`

## 6. Exit criteria (checkable)

1. **Upload on A + chat on B passes an integration test** (roadmap §5 P5 gate): two instances sharing
   Postgres + Redis + S3; ingest on A, poll/chat on B succeeds (Task 7).
2. **Ingestion survives an instance restart:** it runs in the Celery worker (a separate process), not
   in the API; status lives in Postgres and is readable from any instance. `grep -rn "BackgroundTasks"
   app.py` → **0**.
3. **Per-user rate limits enforced** via Redis-backed `slowapi`: exceeding a user's limit returns
   `429`; two users do not share a bucket; `/health` is never limited.
4. **No file passthrough through the API process:** uploads use a presigned **PUT** to S3/MinIO;
   `/api/upload` issues the URL and `/api/upload/confirm` `head_object`-verifies and enqueues.
   `grep -rn "upload_fileobj\|file.read()" app.py` → **0**.
5. **Redis client built once in `lifespan`, closed on shutdown, injected via `Depends`;** no
   per-request client construction; **no module-level mutable request state** (statelessness test).
6. **Celery task test (eager mode), rate-limit test, and presigned-upload test all green** (roadmap §5
   P5: "Celery task + rate-limit + presigned-upload tests").
7. **All Phase 0–4 gates still pass;** coverage floor **raised** (ratchet recorded in `pyproject.toml`
   + `Jenkinsfile`); `mypy` clean (incl. `worker`); offline imports clean under conftest dummies.
8. **New deps only:** `redis`, `celery`, `slowapi` added to `pyproject.toml` + `uv.lock` (roadmap §6).

## Appendix A — `docker-compose.yml` services added (redis + worker)

The `redis` service backs cache, limiter storage, and the Celery broker. The `worker` runs the Celery
pool against the **same image** and the **same `Settings`** as the API.

```yaml
services:
  api:
    # … existing (Phase 1 MinIO, Phase 2 Postgres) …
    environment:
      - REDIS_URL=redis://redis:6379/0
      # … existing env …
    depends_on:
      - postgres
      - minio
      - redis

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  worker:
    build: .
    command: uv run celery -A worker.celery_app.celery_app worker --loglevel=info
    environment:
      - REDIS_URL=redis://redis:6379/0
      - ENVIRONMENT=development
      - S3_ENDPOINT_URL=http://minio:9000
      # … same DATABASE_URL / AWS_* / PINECONE_* / HUGGINGFACE_TOKEN as `api` …
    depends_on:
      - postgres
      - minio
      - redis
```
> The `worker` carries the same Postgres / S3 / Pinecone / HF env as `api` because the **task builds
> its own clients** — it inherits nothing from the API process. Scale the pool with
> `docker compose up --scale worker=3`.

## Appendix B — Upload flow: before → after

**Before (Phase ≤ 4): passthrough through the API process.**
```
Client ──multipart file──▶ API /api/upload
                              │ await s3.upload_fileobj(file.file, …)   (whole file via API)
                              │ repo.create_document(status="pending")
                              └ background_tasks.add_task(process_file_pipeline, …)  ✗ dies on restart
```

**After (Phase 5): presigned PUT + confirm/enqueue.**
```
1. Client ──{filename}──▶ API /api/upload
                            │ await s3.generate_presigned_url("put_object", key)
                            │ repo.create_document(status="pending")
                            └─▶ {document_id, upload_url, s3_key}

2. Client ──PUT bytes──▶ S3 / MinIO            (no API involvement; no API memory)

3. Client ──{document_id, s3_key}──▶ API /api/upload/confirm
                            │ await s3.object_exists(s3_key)   (head_object; closes the race)
                            └ ingest_document.delay(document_id, s3_key, …)  ─▶ Redis broker

4. Celery worker (separate process) ─▶ process_file_pipeline (builds own clients)
                            └ status → "complete" | "failed" in Postgres   (survives API restart)
```
Any API instance can serve steps 1 and 3 and the later `/api/documents/{id}` poll, because all state
(document row, status, vectors, broker queue) is external.

## Appendix C — Rate-limit policy & env reference

**Policy table** (defaults; override per environment):

| Route | Limit (setting) | Key | Notes |
|---|---|---|---|
| `/api/chat` | `RATE_LIMIT_CHAT` (`30/minute`) | `user:{id}` else IP | Protects per-tenant LLM spend |
| `/api/upload` | `RATE_LIMIT_UPLOAD` (`10/minute`) | `user:{id}` else IP | Caps presign issuance |
| `/api/upload/confirm` | `RATE_LIMIT_UPLOAD` (`10/minute`) | `user:{id}` else IP | Caps enqueue rate |
| `/api/cleanup` | `RATE_LIMIT_DEFAULT` (`120/minute`) | `user:{id}` else IP | Coarse default |
| all other routes | `RATE_LIMIT_DEFAULT` (`120/minute`) | `user:{id}` else IP | `default_limits` |
| `/health` | **exempt** | — | Liveness/readiness probes must never `429` |

**Env / `Settings` additions (Phase 5):**
```dotenv
REDIS_URL=redis://redis:6379/0
# CELERY_BROKER_URL=        # optional; defaults to REDIS_URL
RATE_LIMIT_CHAT=30/minute
RATE_LIMIT_UPLOAD=10/minute
RATE_LIMIT_DEFAULT=120/minute
```

**Dependencies added (roadmap §6):** `redis`, `celery`, `slowapi`. A **single Redis** serves broker +
cache + limiter in this phase; splitting them onto separate instances is future work (roadmap §7).
