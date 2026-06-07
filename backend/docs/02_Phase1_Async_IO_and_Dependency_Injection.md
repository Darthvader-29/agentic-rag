# Phase 1 — Async I/O Refactor + Dependency Injection

> **For the implementer:** execute the tasks **in order**; each ends with a verification that must
> pass before its commit. The tree stays releasable after every task. Companion to
> [`00_Master_Upgrade_Roadmap.md`](./00_Master_Upgrade_Roadmap.md) §4 (Phase 1) and follows
> [`01_Phase0_Quality_and_Safety_Foundation.md`](./01_Phase0_Quality_and_Safety_Foundation.md).
>
> **Doc numbering:** `00` = master roadmap; `01` = Phase 0; `02` = this Phase 1 detail doc.

## 1. Objective & scope

Make I/O truly non-blocking and replace import-time singletons with **injectable, lifecycle-managed
client objects** — the dependency-injection seam every later phase hangs on. Phase 1 changes the
*plumbing*, not the product behavior: the same routes, the same `route → relevance → retrieve →
generate` flow, the same responses. What changes is **how** clients are built (in a FastAPI
`lifespan`, injected via `Depends`) and **how** their blocking calls run (off the event loop via
`asyncio.to_thread`, with `tenacity` retries).

**In scope:**
- A FastAPI `lifespan` that constructs four **class-based clients** — Pinecone, S3, HuggingFace
  embeddings, DuckDuckGo web search — stores them on `app.state`, and exposes them through `Depends`
  provider functions (new `dependencies.py`).
- Wrap every blocking SDK call in `asyncio.to_thread`, decorated with `tenacity` retry/backoff
  (already installed). **No native async libraries** (`aioboto3`, async Pinecone, etc.) — deferred.
- **Drop cloud AWS S3 for local development.** Add a **MinIO** container (S3-compatible) to a new
  `docker-compose.yml`; in `development` the sync `boto3` client's `endpoint_url` points at
  `http://localhost:9000`. Production keeps real S3 (no `endpoint_url`).
- Refactor the component functions (`router`, `retrieval`, `preprocessing`, and the `app.py` helpers)
  to **accept injected clients as parameters** instead of importing module-level singletons.
- DI-override test fixtures + async-path tests proving the seam works.

**Explicitly deferred** (do **not** do here):
- **The Gemini/LLM client stays a module-level global** (`genai.configure()` +
  `GenerativeModel(...)` at `components/router.py:15-23` and `components/generation.py:17-27`). Per-user
  BYOK keys need the provider abstraction; that is **Phase 4**. `generation.py` is therefore **not**
  reworked for DI in Phase 1 — only its callers' wiring is left intact.
- Native async clients / `aioboto3` (deferred per decision; revisit if `to_thread` proves insufficient).
- Removing the dummy-vector state queries `has_session_documents`
  (`components/router.py:136-155`) and `list_s3_keys_for_session` (`database/db_manager.py:97-121`).
  They are **wrapped into the client class as-is** in Phase 1; **Phase 2** moves state to Postgres.
- Postgres, Alembic, auth, CORS tightening, rate-limiting, presigned uploads, removing `uploadthing`.

## 2. Decisions & rationale

| Decision | Rationale |
|---|---|
| **`asyncio.to_thread` only; no `aioboto3`/async SDKs** | Smallest, lowest-risk way to stop serializing the event loop. The sync SDKs (boto3, pinecone, huggingface_hub, duckduckgo) are battle-tested; offloading them to the default thread-pool unblocks concurrency now. Native async libs are a later optimization, not a Phase 1 dependency. |
| **Local MinIO via `docker-compose`; dev `endpoint_url=http://localhost:9000`** | AWS free tier expired. MinIO is API-compatible with S3, so **the same `boto3` code path** serves both: development talks to MinIO, production talks to real S3, switched purely by `endpoint_url`. No code branches in business logic. |
| **Class-based client objects, built in `lifespan`, injected via `Depends`** | Converts import-time singletons into lifecycle-managed instances that tests can override per-request (`app.dependency_overrides`). Wrapping each integration in a class with methods (not free functions) also pre-stages the Phase 4 provider abstraction. |
| **Gemini stays module-level global (deferred to Phase 4)** | `genai.configure()` is process-global; making it per-request is unsafe and pointless until the multi-provider abstraction + per-user keys land (roadmap §3 ordering constraint 2). Touching it now would be churn we rewrite in Phase 4. |
| **`tenacity` retry on the *sync* method, inside `to_thread`** | Backoff/sleep happens in the worker thread, never on the event loop. `tenacity` is already a dependency; no new package. |
| **DI-override unit assertions for the concurrency exit (no timing test)** | Prove the seam structurally: handlers resolve clients via `Depends` (overridable), and blocking calls are offloaded (`to_thread` invoked / method is a coroutine). Avoids flaky wall-clock timing tests in CI. |
| **No new Python dependencies** | `tenacity`, `boto3`, `httpx` already present; MinIO ships as a container image (`minio/minio`, `minio/mc`), not a pip package. `pyproject.toml`/`requirements*.txt` are unchanged except possibly re-locking. |

## 3. Current-state snapshot (verified)

- **Four blocking SDKs are called from `async def` paths**, serializing the loop:
  - **Pinecone** (`database/db_manager.py`): `get_index`/`save_vectors`/`search_vectors`/
    `delete_vectors_by_session`/`list_s3_keys_for_session` — all sync; `get_index` even does a
    blocking `time.sleep(10)` (`db_manager.py:31-33`) after creating an index.
  - **boto3 S3** (`integrations/s3/client.py`): module-level `s3 = boto3.client(...)` (`:10-15`);
    sync `upload_fileobj`/`download_fileobj`/`delete_objects`.
  - **HuggingFace** (`integrations/huggingface/client.py`): module-level `InferenceClient(...)`
    (`:13-16`); sync `feature_extraction`.
  - **DuckDuckGo** (`integrations/duckduckgo/client.py`): sync `DDGS().text(...)`.
- **Import-time singletons:** `pc = Pinecone(...)` (`db_manager.py:7`), `s3 = boto3.client(...)`
  (`s3/client.py:10`), `client = InferenceClient(...)` (`huggingface/client.py:13`). All four are
  built at import and imported as free functions by `app.py`, `components/{router,retrieval,
  preprocessing}.py`.
- **Gemini globals** (out of scope, leave): `genai.configure(...)` + `GenerativeModel(...)` at
  `components/router.py:15-23` and `components/generation.py:17-27`. `generation.py` self-configures
  (Phase 0 fixed the free-ride).
- **`app.py`** has **no `lifespan`** and **no `Depends`**: endpoints import the free functions
  directly (`app.py:10-22`). Ingestion runs via `BackgroundTasks.add_task(process_file_pipeline, ...)`
  (`app.py:154-159`) — background tasks **cannot use `Depends`**, so clients must be passed in
  explicitly by the upload handler.
- **`app.py` helpers** `get_query_embedding` (`:69-75`) and `check_docs_relevant` (`:78-101`) are
  **sync** and call `embed_batch`/`search_vectors` directly.
- **Config** (`config.py`): single `pydantic-settings` `Settings`; 7 required + 3 optional vars. **No
  `ENVIRONMENT` and no `S3_ENDPOINT_URL` yet.** `AWS_*` are still required (we reuse them as MinIO
  creds in dev).
- **Tests:** 7 offline tests. `test/test_router.py` patches `database.db_manager.get_index` and
  `components.router.gemini_model.generate_content_async`; `test/test_retrieval.py` patches the
  module-level free functions. These **will break** when functions become methods → must be updated.
  `conftest.py` injects dummy secrets before import.
- **No `docker-compose.yml`.** `Dockerfile` (python:3.12.6-slim) present; CI is Jenkins
  (`Jenkinsfile`), coverage floor **36** (`pyproject.toml:74`).

## 4. Risks & gotchas (with resolutions)

1. **Background tasks can't use `Depends`.** `process_file_pipeline` runs *after* the response via
   `BackgroundTasks`. **Resolution:** the upload handler resolves the S3 / embedding / Pinecone
   clients via `Depends`, then passes those instances into `background_tasks.add_task(...)`. The
   clients are `app.state` singletons that live for the whole app lifetime, so this is safe.

2. **Thread-safety of shared singleton clients under `to_thread`.** One client instance is now used
   concurrently across thread-pool workers. **boto3 clients are documented thread-safe**; Pinecone v8
   and `huggingface_hub.InferenceClient` are likewise safe for concurrent read calls. **Resolution:**
   share a single instance per client (do **not** rebuild per request); note any per-call mutable
   state as future tech debt. Do **not** share the low-level boto3 *resource* objects (we use the
   client only).

3. **`genai.configure()` is still global and import-time.** Keeping it (decision) means
   `GOOGLE_API_KEY` is still required at import (pytest collection, `import app`). **Resolution:**
   unchanged from Phase 0 — `conftest.py` dummies + Jenkins `environment{}` already cover it. Do not
   move Gemini into `lifespan`.

4. **MinIO needs path-style addressing + explicit `endpoint_url`.** Default boto3 uses virtual-host
   style (`bucket.s3.amazonaws.com`), which MinIO at `localhost:9000` does not serve.
   **Resolution:** build the dev client with
   `botocore.config.Config(signature_version="s3v4", s3={"addressing_style": "path"})` and
   `endpoint_url="http://localhost:9000"`. Production passes `endpoint_url=None` → standard S3.

5. **The MinIO bucket must exist before first upload.** A fresh MinIO has no buckets.
   **Resolution:** a one-shot `createbuckets` service (`minio/mc`) in `docker-compose.yml` creates
   `S3_BUCKET_NAME` on startup; document the manual `mc mb` fallback.

6. **`tenacity` on a coroutine vs. a sync function.** Decorating an `async def` with `@retry` retries
   the coroutine but its `wait_*` sleeps would run on the loop. **Resolution:** decorate the **sync**
   `_*_sync` method with `@retry`, and call it through `await asyncio.to_thread(...)` so both the call
   and its backoff sleeps run in the worker thread. Use `reraise=True` so the original exception (not
   `RetryError`) surfaces to the existing error handlers.

7. **Existing tests patch module paths that disappear.** Converting free functions to methods breaks
   `@patch("database.db_manager.get_index")` etc. **Resolution:** rewrite the affected tests to inject
   a fake client (a `MagicMock`/`AsyncMock`) as a parameter or via `app.dependency_overrides`. This is
   expected churn, done in the same task that introduces each client (keeps the suite green per commit).

8. **`asyncio.to_thread` requires Python ≥3.9.** We are on 3.12 — fine. Methods that wrap it must be
   `async` and every caller must `await`. **Resolution:** make `get_query_embedding` and
   `check_docs_relevant` (`app.py`) `async`; the four component entrypoints are already `async`.

9. **Index-ensure should not run per request.** `get_index` lists indexes (and maybe `sleep(10)`) on
   *every* call. **Resolution:** `PineconeClient.ensure_index()` runs **once in `lifespan` startup**
   (via `to_thread`); subsequent methods reuse a cached `Index` handle. This both removes per-call
   blocking and is a clean behavior-preserving improvement.

## 5. Tasks (ordered)

> Conventional-commit message per task. `uv run <cmd>` runs inside the project venv. Each task leaves
> the suite green and the tree releasable.

### Task 1 — Config: add `ENVIRONMENT` + `S3_ENDPOINT_URL` (TDD)
**Files:** `config.py`; `test/test_config.py`; `conftest.py`; `.env.example`.

**RED** — extend `test/test_config.py`:
```python
def test_environment_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED); assert c.settings.ENVIRONMENT == "development"
def test_s3_endpoint_optional(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED); assert c.settings.S3_ENDPOINT_URL is None
```
(Add `ENVIRONMENT` and `S3_ENDPOINT_URL` to the `delenv` list in `_fresh`.)

**GREEN** — add to `Settings` in `config.py`:
```python
from typing import Literal
...
    ENVIRONMENT: Literal["development", "production"] = "development"
    S3_ENDPOINT_URL: str | None = None   # set for MinIO/dev; None → real AWS S3
```
Add to `conftest.py` `_DUMMY`: `"ENVIRONMENT": "development"` (leave `S3_ENDPOINT_URL` unset so tests
exercise the dev-derived default). Append to `.env.example`:
```dotenv
# Phase 1
ENVIRONMENT=development
# Leave blank in production (real AWS S3); dev auto-uses MinIO at http://localhost:9000
S3_ENDPOINT_URL=
```
**Verify:** `uv run pytest test/test_config.py -q` green.
**Commit:** `feat(config): add ENVIRONMENT and S3_ENDPOINT_URL for MinIO/dev switching`

### Task 2 — `docker-compose.yml` with MinIO (+ bucket bootstrap)
**Files:** create `docker-compose.yml`; update `.env.example` (dev MinIO creds note); `README.md` (run steps).
```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"      # S3 API
      - "9001:9001"      # web console
    environment:
      MINIO_ROOT_USER: ${AWS_ACCESS_KEY_ID:-minioadmin}
      MINIO_ROOT_PASSWORD: ${AWS_SECRET_ACCESS_KEY:-minioadmin}
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 5

  createbuckets:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${AWS_ACCESS_KEY_ID:-minioadmin} ${AWS_SECRET_ACCESS_KEY:-minioadmin};
      mc mb -p local/${S3_BUCKET_NAME:-rag-documents};
      exit 0;"

volumes:
  minio_data:
```
Document in `.env.example` that **for development** the AWS creds double as MinIO creds:
```dotenv
# Dev MinIO defaults (must match docker-compose). In prod use real AWS values.
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET_NAME=rag-documents
AWS_REGION=us-east-1
```
**Verify:** `docker compose config` parses (no apply needed in CI); `docker compose up -d minio
createbuckets` then console reachable at `localhost:9001` and the bucket exists (local manual check).
**Commit:** `build: add MinIO docker-compose for local S3-compatible storage`

### Task 3 — `PineconeClient` class (async + tenacity), update call sites & tests
**Files:** rewrite `database/db_manager.py`; update `components/router.py`, `components/retrieval.py`,
`components/preprocessing.py`, `app.py`; update `test/test_router.py`, `test/test_retrieval.py`.

Convert the module into a class. Pattern (abbreviated):
```python
import asyncio
import structlog
from pinecone import Pinecone, ServerlessSpec
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)
_RETRY = dict(stop=stop_after_attempt(3),
              wait=wait_exponential(multiplier=0.5, max=8), reraise=True)


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
    def _ensure_index_sync(self):
        names = [i.name for i in self._pc.list_indexes()]
        if self._index_name not in names:
            logger.info("pinecone_create_index", index_name=self._index_name)
            self._pc.create_index(name=self._index_name, dimension=self._dimension, metric="cosine",
                                  spec=ServerlessSpec(cloud="aws", region="us-east-1"))
            import time; time.sleep(10)
        self._index = self._pc.Index(self._index_name)

    async def ensure_index(self) -> None:          # called once in lifespan
        await asyncio.to_thread(self._ensure_index_sync)

    def _index_or_raise(self):
        if self._index is None:
            self._ensure_index_sync()
        return self._index

    # save_vectors / search_vectors / delete_vectors_by_session / has_session_documents /
    # list_s3_keys_for_session: each keeps its existing body verbatim inside a private _*_sync
    # method decorated with @retry, exposed as an `async def` that does
    #   return await asyncio.to_thread(self._*_sync, ...)
```
- Move `has_session_documents` (currently in `router.py:136-155`) onto this class as
  `async def has_session_documents(self, session_id) -> bool`; delete it from `router.py`.
- `route_query` signature becomes `async def route_query(query, session_id, web_search_allowed,
  pinecone: PineconeClient)`; it calls `await pinecone.has_session_documents(session_id)`. **Gemini
  stays global** in `router.py` — only the Pinecone access changes.
- `retrieve_context` and `process_file_pipeline` and the `app.py` helpers call the injected client's
  `await search_vectors(...)` / `await save_vectors(...)` / `await delete_vectors_by_session(...)`.
- **Tests:** rewrite `test_router.py` to pass an `AsyncMock` Pinecone client (replacing the
  `@patch("database.db_manager.get_index")` approach); keep the `gemini_model` patch as-is.

**Verify:** `uv run pytest -q` green; `grep -rn "from database.db_manager import" app.py components/`
shows no free-function imports remain.
**Commit:** `refactor(pinecone): wrap client in async class (to_thread + tenacity); inject not import`

### Task 4 — `S3Client` class (async + tenacity + MinIO endpoint)
**Files:** rewrite `integrations/s3/client.py`; update `app.py`, `components/preprocessing.py`.
```python
import asyncio, os, uuid
import boto3
from botocore.config import Config
from tenacity import retry, stop_after_attempt, wait_exponential

_RETRY = dict(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)


class S3Client:
    def __init__(self, *, bucket, region, access_key, secret_key, endpoint_url=None):
        self._bucket = bucket
        self._client = boto3.client(
            "s3", region_name=region, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, endpoint_url=endpoint_url,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @classmethod
    def from_settings(cls, settings) -> "S3Client":
        endpoint = settings.S3_ENDPOINT_URL
        if endpoint is None and settings.ENVIRONMENT == "development":
            endpoint = "http://localhost:9000"           # MinIO
        return cls(bucket=settings.S3_BUCKET_NAME, region=settings.AWS_REGION,
                   access_key=settings.AWS_ACCESS_KEY_ID, secret_key=settings.AWS_SECRET_ACCESS_KEY,
                   endpoint_url=endpoint)

    @staticmethod
    def _key(filename): return f"uploads/{uuid.uuid4()}_{filename}"

    @retry(**_RETRY)
    def _upload_sync(self, file_obj, key): self._client.upload_fileobj(file_obj, self._bucket, key)
    async def upload_fileobj(self, file_obj, filename) -> str:
        key = self._key(filename); await asyncio.to_thread(self._upload_sync, file_obj, key); return key
    # download_to_temp(key) -> str  and  delete_objects(keys) follow the same _sync + to_thread shape
```
**Verify:** `uv run pytest -q` green; a unit test asserts `from_settings` yields
`endpoint_url=http://localhost:9000` when `ENVIRONMENT=development`, and `None` when `production`.
**Commit:** `refactor(s3): class-based async S3 client; dev endpoint targets MinIO`

### Task 5 — `HuggingFaceClient` (embeddings) class (async + tenacity)
**Files:** rewrite `integrations/huggingface/client.py`; update `app.py`, `components/retrieval.py`,
`components/preprocessing.py`.
- Class holds the `InferenceClient`; `from_settings` reads `HUGGINGFACE_TOKEN`.
- `_embed_batch_sync` keeps the existing batching/`np.ndarray.tolist()` body, decorated `@retry`.
- Expose `async def embed_batch(texts, batch_size=32)` and `async def embed_single(text)` via
  `to_thread`.
- `retrieve_context` calls `await embedder.embed_single(query)`; `process_file_pipeline` and the
  `app.py` `get_query_embedding` helper call `await embedder.embed_batch(...)`.
**Verify:** `uv run pytest -q` green (update `test/test_embeddings_gemini.py` to the method form).
**Commit:** `refactor(hf): class-based async embedding client (to_thread + tenacity)`

### Task 6 — `DuckDuckGoClient` (web search) class (async + tenacity)
**Files:** rewrite `integrations/duckduckgo/client.py`; update `components/retrieval.py`.
- `_search_sync(query, max_results)` keeps the existing `with DDGS() as ddgs` body + error swallow.
- Expose `async def search_web(query, max_results=5)` via `to_thread`.
- `retrieve_context` calls `await web.search_web(query, max_results=5)`.
**Verify:** `uv run pytest -q` green.
**Commit:** `refactor(websearch): class-based async DuckDuckGo client`

### Task 7 — `lifespan` + `dependencies.py` + wire endpoints (the seam)
**Files:** create `dependencies.py`; rewrite `app.py` (lifespan, `Depends`, async helpers, background
task wiring).

`dependencies.py`:
```python
from fastapi import Request
from database.db_manager import PineconeClient
from integrations.s3.client import S3Client
from integrations.huggingface.client import HuggingFaceClient
from integrations.duckduckgo.client import DuckDuckGoClient

def get_pinecone_client(request: Request) -> PineconeClient: return request.app.state.pinecone
def get_s3_client(request: Request) -> S3Client:             return request.app.state.s3
def get_embedding_client(request: Request) -> HuggingFaceClient: return request.app.state.embedder
def get_web_search_client(request: Request) -> DuckDuckGoClient: return request.app.state.web
```
`app.py` lifespan + wiring:
```python
from contextlib import asynccontextmanager
from fastapi import Depends
from config import settings
from dependencies import (get_pinecone_client, get_s3_client,
                          get_embedding_client, get_web_search_client)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.pinecone = PineconeClient.from_settings(settings)
    await app.state.pinecone.ensure_index()          # once, off the loop
    app.state.s3 = S3Client.from_settings(settings)
    app.state.embedder = HuggingFaceClient.from_settings(settings)
    app.state.web = DuckDuckGoClient()
    logger.info("clients_initialized", environment=settings.ENVIRONMENT)
    yield
    # no async resources to close in Phase 1 (sync SDKs); hook reserved for later phases

app = FastAPI(title=..., version="1.0.0", description=..., lifespan=lifespan)
```
- Endpoints declare the clients they need as `Depends(...)` params and pass them down:
  - `chat(...)` → `pinecone`, `embedder`, `web`; calls `await route_query(..., pinecone)`,
    `await check_docs_relevant(..., pinecone, embedder)`, `await retrieve_context(..., pinecone,
    embedder, web)`, `await generate_final_response(...)` (**unchanged** — Gemini global).
  - `upload(...)` → `s3`, `embedder`, `pinecone`; `s3_key = await s3.upload_fileobj(...)`; passes the
    three clients into `background_tasks.add_task(process_file_pipeline, s3_key, filename, session_id,
    s3, embedder, pinecone)` (gotcha 1).
  - `cleanup_session(...)` → `s3`, `pinecone`; `await pinecone.delete_vectors_by_session(...)`,
    `await s3.delete_objects(...)`, and (Phase-1 transitional) `await
    pinecone.list_s3_keys_for_session(...)`.
- Make `get_query_embedding` and `check_docs_relevant` `async` and parameterize them on the clients;
  remove the module-level `configure_logging()` call (now inside `lifespan`).
- **Remove the top-of-file free-function imports** that no longer exist.

**Verify:** `uv run pytest -q` green; `uv run python -c "import app"` exits 0 (under conftest dummies);
`grep -rn "asyncio.to_thread\|to_thread" integrations database` confirms every blocking call is
offloaded; `grep -rn "Depends(" app.py` shows clients injected, not imported.
**Commit:** `feat(di): FastAPI lifespan + Depends wiring; inject clients into all endpoints`

### Task 8 — Async-path + DI-override tests
**Files:** create `test/test_dependencies.py`, `test/test_async_paths.py`; extend client tests.
- **DI-override:** build a `TestClient(app)` (triggers `lifespan`), then
  `app.dependency_overrides[get_pinecone_client] = lambda: fake_pinecone` (and friends with
  `AsyncMock`s). Hit `/api/chat`, `/api/upload`, `/api/cleanup`; assert the **fake methods were
  awaited** with expected args — proving every endpoint pulls clients from DI, not imports.
- **Offload assertion (concurrency proof, per decision):** for each client, patch the underlying sync
  SDK call and assert the public `async` method returns its value *and* that `asyncio.to_thread` was
  used (e.g. `monkeypatch`/`patch("asyncio.to_thread", wraps=asyncio.to_thread)` and
  `assert to_thread.called`). No wall-clock timing.
- **Retry:** make the sync SDK call raise twice then succeed; assert the public method returns and the
  SDK was called 3× (tenacity), and that a permanent failure re-raises the original exception.
- **`from_settings` endpoint:** `S3Client.from_settings` → `http://localhost:9000` for
  `development`, `None` for `production`.
**Verify:** `uv run pytest -q` green.
**Commit:** `test: DI-override fixtures + async-offload/retry assertions for Phase 1 seam`

### Task 9 — Coverage ratchet, mypy, lock & docs
**Files:** `pyproject.toml` (coverage floor), this doc, `Jenkinsfile` (floor), `README.md`.
```bash
uv run pytest --cov --cov-report=term-missing
```
Read `TOTAL`; raise `--cov-fail-under` from 36 to `floor(new TOTAL)` (ratchet **upward only**); record
the integer here and in `Jenkinsfile`. Run
`uv run mypy app.py config.py dependencies.py exceptions.py logging_config.py components database integrations`
and fix/needs-override the few flagged (new code is typed; add `[[tool.mypy.overrides]]` only as a last
resort with a tech-debt note). If `pyproject.toml` was untouched, no re-lock; otherwise `uv lock` and
regenerate `requirements*.txt`. Update `README.md` with the `docker compose up -d` dev workflow.
**Verify:** `uv run ruff check . && uv run ruff format --check . && uv run mypy <targets> && uv run
pytest --cov --cov-fail-under=<new>` → all exit 0.
**Commit:** `test: ratchet coverage gate; mypy clean; document MinIO dev workflow`

## 6. Exit criteria (checkable)

1. **No blocking call inside an async path.** Every Pinecone/S3/HF/DuckDuckGo SDK call runs through
   `asyncio.to_thread`; `grep -rn` finds no bare sync SDK call in an `async def` (clients only expose
   `async` methods).
2. **Clients resolved via DI, not imported.** `app.py` builds all four clients in `lifespan`, stores
   them on `app.state`, and every endpoint receives them via `Depends`. No module-level
   `Pinecone(...)`/`boto3.client(...)`/`InferenceClient(...)` remains (Gemini global is the one
   intentional exception, deferred to Phase 4).
3. **Concurrency no longer serialized — proven structurally.** DI-override tests assert handlers pull
   clients from DI; offload tests assert blocking calls go through `to_thread`; retry tests assert
   `tenacity` backoff. CI green.
4. **MinIO dev path works.** `docker compose up -d` brings up MinIO + creates the bucket; in
   `development` the S3 client targets `http://localhost:9000`; in `production` it targets real S3
   (`endpoint_url=None`). Switched by `ENVIRONMENT`/`S3_ENDPOINT_URL` with no business-logic branches.
5. **Behavior unchanged.** Same routes, same `route → relevance → retrieve → generate` flow, same
   response shapes; Gemini generation untouched.
6. **All Phase 0 gates still pass** and the coverage floor is **raised** (ratchet recorded in
   `pyproject.toml` + `Jenkinsfile`).
7. **No new Python dependency** introduced (MinIO is container-only); `import app` clean under conftest
   dummies.

## Appendix A — Client refactor map (free function → method)

| Old module-level free function | New class.method (all `async`, `to_thread` + `@retry`) | Callers updated |
|---|---|---|
| `db_manager.get_index` | `PineconeClient.ensure_index` (lifespan) / internal `_index_or_raise` | lifespan |
| `db_manager.save_vectors` | `PineconeClient.save_vectors` | `preprocessing` |
| `db_manager.search_vectors` | `PineconeClient.search_vectors` | `retrieval`, `app.check_docs_relevant` |
| `db_manager.delete_vectors_by_session` | `PineconeClient.delete_vectors_by_session` | `app.cleanup_session` |
| `db_manager.list_s3_keys_for_session` *(transitional; Phase 2 removes)* | `PineconeClient.list_s3_keys_for_session` | `app.cleanup_session` |
| `router.has_session_documents` *(moved off router)* | `PineconeClient.has_session_documents` | `router.route_query` |
| `s3.upload_fileobj_to_s3` | `S3Client.upload_fileobj` | `app.upload` |
| `s3.download_s3_to_temp` | `S3Client.download_to_temp` | `preprocessing` |
| `s3.delete_s3_objects` | `S3Client.delete_objects` | `app.cleanup_session` |
| `huggingface.embed_batch` | `HuggingFaceClient.embed_batch` | `preprocessing`, `app.get_query_embedding` |
| `huggingface.embed_single` | `HuggingFaceClient.embed_single` | `retrieval` |
| `duckduckgo.search_web` | `DuckDuckGoClient.search_web` | `retrieval` |
| `router` Gemini globals **(unchanged)** | — *(Phase 4)* | — |
| `generation` Gemini globals **(unchanged)** | — *(Phase 4)* | — |

## Appendix B — Component signature changes

| Function | Before | After (injected clients appended) |
|---|---|---|
| `router.route_query` | `(query, session_id, web_search_allowed)` | `(query, session_id, web_search_allowed, pinecone)` |
| `retrieval.retrieve_context` | `(query, decision, session_id, web_search_allowed=False)` | `(query, decision, session_id, web_search_allowed, pinecone, embedder, web)` |
| `preprocessing.process_file_pipeline` | `(file_key, filename, session_id)` | `(file_key, filename, session_id, s3, embedder, pinecone)` |
| `app.get_query_embedding` | `def (text)` | `async def (text, embedder)` |
| `app.check_docs_relevant` | `def (query, session_id)` | `async def (query, session_id, pinecone, embedder)` |
| `generation.generate_final_response` | `(query, context, decision)` | **unchanged** (Gemini global, Phase 4) |

## Appendix C — Local dev workflow (MinIO)

```bash
cp .env.example .env            # dev defaults: ENVIRONMENT=development, minioadmin creds
docker compose up -d minio createbuckets   # S3 API :9000, console :9001, bucket auto-created
uv run uvicorn app:app --reload            # S3 client auto-targets http://localhost:9000
```
Production: set `ENVIRONMENT=production`, real `AWS_*`, leave `S3_ENDPOINT_URL` blank → standard AWS S3.
Console login at http://localhost:9001 uses `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
