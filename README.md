# Agentic RAG — Monorepo

A production-style Agentic RAG application: a FastAPI backend (LangGraph agents, multi-provider
LLMs, per-session memory + knowledge graph, observability) and a Next.js frontend (streaming chat,
BYOK, rich components, insights panels).

```
agentic-rag/
├── backend/    # FastAPI + LangGraph + Celery (Python, uv)        — see backend/CLAUDE.md
└── frontend/   # Next.js + TanStack Query + Zustand + Zod (npm)
```

Both projects retain their full original git history (merged via `git subtree` under `backend/`
and `frontend/`).

## Prerequisites
- Docker Desktop (local MinIO + Redis)
- Python + [uv](https://docs.astral.sh/uv/) (backend)
- Node.js + npm (frontend)
- A Postgres database (the project uses NeonDB) and the API keys listed in `backend/.env.example`

## Environment
- **Backend:** copy `backend/.env.example` → `backend/.env` and fill it in (see `backend/CLAUDE.md`
  for the full variable list). Object storage is **S3-compatible** — local dev uses MinIO
  (auto-targeted at `http://localhost:9000` when `ENVIRONMENT=development`), production uses
  Backblaze B2 (`S3_ENDPOINT_URL` selects the backend).
- **Frontend:** copy `frontend/.env.example` → `frontend/.env.local`. Set
  `NEXT_PUBLIC_API_URL=http://localhost:8000/api` and the feature flags you want.
- For local cross-origin calls, set the backend `CORS_ALLOWED_ORIGINS=["http://localhost:3000"]`.

## Run it locally
```bash
# 1. Infra: MinIO (S3 :9000 / console :9001) + Redis (:6379)
cd backend && docker compose up -d

# 2. Backend API  →  http://localhost:8000
uv sync
uv run uvicorn app:app --host 0.0.0.0 --port 8000

# 3. Celery worker (uploads → ingestion). On Windows add --pool=solo
uv run celery -A worker.celery_app worker --loglevel=info

# 4. Frontend  →  http://localhost:3000
cd ../frontend
npm install
npm run dev
```

Ports: frontend `3000`, backend `8000`, MinIO `9000`/`9001`, Redis `6379` (all distinct).

## Tests / quality gates
```bash
# Backend
cd backend && uv run pytest && uv run ruff check . && uv run mypy .

# Frontend
cd frontend && npm run typecheck && npm run lint && npm test
```

See `backend/CLAUDE.md` for backend architecture and the phase-by-phase upgrade history.
