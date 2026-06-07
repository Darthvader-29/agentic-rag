# Python-Agentic-RAG-Backend

VectorDB: Pinecone | Object storage: Backblaze B2 / MinIO (S3-compatible) | LLM: Gemini / Claude / OpenAI (BYOK) | Queue: Celery + Redis | Deployment: Render

## Quick start

```bash
cp .env.example .env   # fill in required values (see Phase 3 section below)
uv sync
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

## Auth flow (Phase 3)

All three RAG endpoints (`/api/chat`, `/api/upload`, `/api/cleanup`) require a valid **JWT access token**
in the `Authorization: Bearer <token>` header.

### Register / login / refresh

```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","username":"you","password":"yourpass"}'

# Login — returns access + refresh tokens
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpass"}'

# Refresh access token
curl -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<your-refresh-token>"}'
```

### Store a BYOK LLM key

```bash
curl -X POST http://localhost:8000/api/keys \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"provider":"gemini","api_key":"AIza..."}'
```

Keys are encrypted at rest with Fernet. The plaintext never touches the database or logs.

## Phase 3 environment variables

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET` | **yes** | JWT signing secret — keep strong, never commit |
| `LLM_KEY_ENCRYPTION_KEY` | **yes** | Fernet key (base64, 32 bytes) for BYOK key encryption |
| `CORS_ALLOWED_ORIGINS` | **yes** | JSON list of allowed CORS origins, e.g. `["http://localhost:3000"]` |
| `JWT_ALGORITHM` | optional | Default: `HS256` |
| `ACCESS_TOKEN_TTL_MINUTES` | optional | Default: `15` |
| `REFRESH_TOKEN_TTL_DAYS` | optional | Default: `7` |

Generate a Fernet key:
```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Object storage (Backblaze B2 / MinIO)

Storage is **S3-compatible, not AWS-specific** — the `AWS_*` env names are kept because the client
speaks the S3 API via boto3. Production uses **Backblaze B2**, local dev uses **MinIO**:

| Variable | Production (B2) | Dev (MinIO) |
|---|---|---|
| `S3_ENDPOINT_URL` | `https://s3.<region>.backblazeb2.com` | blank → `http://localhost:9000` |
| `AWS_REGION` | the B2 bucket region, e.g. `us-west-004` | `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | B2 keyID / applicationKey | `minioadmin` / `minioadmin` |
| `S3_BUCKET_NAME` | your **private** B2 bucket | `rag-documents` |

Use a private bucket (no credit card needed on B2). `S3Client` pins
`request_checksum_calculation="when_required"` so botocore ≥ 1.36's default CRC32 trailers aren't
rejected by B2/MinIO.

## Phase 5 — scaling, rate limiting, queue-based ingestion

The backend is **horizontally scalable**: identical stateless instances behind a load balancer, with
shared state in Redis (rate-limit counters, Celery broker) and Postgres (ingestion status). Ingestion
runs in a separate **Celery worker**, so it survives an API restart.

```bash
# Local dev: bring up MinIO + Redis, then run the API and a worker
docker compose up -d minio createbuckets redis
uv run uvicorn app:app --host 0.0.0.0 --port 8000
uv run celery -A worker.celery_app.celery_app worker --loglevel=info   # add --pool=solo on Windows
```

**Rate limiting** (`slowapi`, Redis-backed, keyed per authenticated user → client IP): `/api/chat`
(`RATE_LIMIT_CHAT`, default 30/min), `/api/upload` + `/api/upload/confirm` (`RATE_LIMIT_UPLOAD`,
10/min), `/api/cleanup` (`RATE_LIMIT_DEFAULT`, 120/min). `/health` is never limited.

**Uploads** — `/api/upload` serves two transports (the frontend picks via its M8 flag):
- `multipart/form-data` → legacy passthrough (bytes via the API); ingestion enqueued to Celery.
- `application/json` `{filename, content_type?, session_id?}` → **presigned PUT**: returns
  `{document_id, upload_url, s3_key, session_id}`. The client PUTs bytes straight to storage, then
  calls `POST /api/upload/confirm {document_id, s3_key}` (head-checks + enqueues). Poll
  `GET /api/documents/{id}` for status (`pending|processing|ready|failed`).

> **B2 CORS:** browser-direct presigned PUTs require a CORS rule on the B2 bucket allowing `PUT` from
> your frontend origin with `Content-Type` — configure this before enabling the frontend's presigned flag.

| Variable | Required | Description |
|---|---|---|
| `REDIS_URL` | optional | Default `redis://localhost:6379/0` (cache + limiter + Celery broker) |
| `CELERY_BROKER_URL` | optional | Defaults to `REDIS_URL` |
| `RATE_LIMIT_CHAT` / `RATE_LIMIT_UPLOAD` / `RATE_LIMIT_DEFAULT` | optional | `30/minute` / `10/minute` / `120/minute` |

## Running tests

```bash
uv run pytest                   # all tests (local coverage gate: 78% — requires TEST_DATABASE_URL)
uv run pytest --cov             # with coverage report
```

Tests that hit the real DB require `TEST_DATABASE_URL` in the environment; they are skipped otherwise
(CI runs without it at a 72% floor). All other external calls are mocked.

## Commands

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
uv run celery -A worker.celery_app.celery_app worker --loglevel=info
uv run pytest --cov
uv run ruff format . && uv run ruff check . --fix
uv run mypy app.py config.py exceptions.py dependencies.py components database integrations auth llm worker
uv run alembic upgrade head
uv run alembic downgrade -1
```
