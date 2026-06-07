# Senior Code Review — agentic-rag monorepo

_Method: 18 domain finders (8 backend, 8 frontend, 2 cross-cutting) read their slice and reported issues; every finding was then re-checked against the actual code by an adversarial verifier. 184 findings raised, **182 confirmed/adjusted, 2 refuted**._

**Severity tally**

| Side | Critical | High | Medium | Low | Info |
|---|---|---|---|---|---|
| Backend | 1 | 12 | 29 | 46 | 7 |
| Frontend | 0 | 12 | 16 | 32 | 8 |
| Cross-cutting | 2 | 4 | 5 | 7 | 1 |

> Many findings recur across slices (e.g. the `confirm_upload` s3_key flaw appears in 5 slices, tokens-in-localStorage in ~7). They are **deduplicated by root cause** below.

---

## 🔴 CRITICAL (3 root causes)

### C1 — Tenant isolation hinges on a guessable, client-supplied `session_id`; unowned sessions are auto-claimed; vectors carry no `user_id`
`backend/app.py:265-284, 540-550` · `backend/database/db_manager.py:69-90` · `backend/database/models.py:77-82`

- `_session_accessible()` returns **True whenever `session.user_id is None`**, and `_resolve_session()` then **silently claims** the unowned session for the caller (`existing.user_id = current_user.id`).
- `Session.id` is a **client-supplied `String(64)` primary key**; `get_or_create_session` creates rows with `user_id = NULL`.
- Pinecone search filters **only** by `session_id` (`filter={"session_id": {"$eq": ...}}`), and stored vectors have **no `user_id` metadata** — there is no tenant dimension at the vector layer at all.
- Net: any authenticated user who supplies a known/guessed unowned `session_id` can read another tenant's **documents, vectors, chat history, markdown memory (`/api/sessions/{id}/memory`), and knowledge graph (`/api/sessions/{id}/graph`)**, and destroy it via `/api/cleanup`.

**Fix:** make `Session.user_id` NOT NULL and set it at creation; delete the `user_id is None` branch in `_session_accessible` and the silent-claim branch in `_resolve_session` (reject non-owned ids with 404/403); add `user_id` to vector metadata and to **every** Pinecone search/delete filter (or use per-user namespaces); add user-scoped repository helpers so isolation is enforced at the data layer, not ad-hoc per endpoint.

### C2 — `/api/upload/confirm` trusts a client-supplied `s3_key` independent of the owned document
`backend/app.py:356-383` · `backend/database/repository.py:59-60` · `backend/worker/tasks.py:86-95`

`confirm_upload` authorizes the **`document_id`** via `_owns_document`, but then uses the separate attacker-controlled **`payload.s3_key`** for `object_exists`, `set_document_status(s3_key=…, FAILED)`, and `ingest_document.delay(s3_key=…)`. There is no check that `payload.s3_key == doc.s3_key`. Because `set_document_status` updates `WHERE Document.s3_key == s3_key` (and `s3_key` is unique), an attacker who owns *any* document can:
- flip an **arbitrary victim document to FAILED** (denial of ingestion / integrity tampering),
- **ingest a victim's S3 object into their own session** (the worker downloads the victim key but tags chunks with the attacker's `session_id`) → cross-tenant document read,
- use `head_object` as an **existence oracle** for arbitrary bucket keys.

**Fix:** drop `s3_key` from `ConfirmRequest`; derive it server-side from the owned row (`doc.s3_key`) for `object_exists`, `set_document_status`, and the ingest enqueue; scope `set_document_status` by `document_id` (PK), not `s3_key`.

### C3 — `register` / `upgrade` return `UserOut` but the frontend validates a `TokenPair` → core auth flows 100% broken
`backend/auth/router.py:36-51, 79-105` · `frontend/features/auth/api/auth.api.ts:23-29, 51-57` · `frontend/features/auth/api/auth.schemas.ts:49-55`

Backend `POST /api/auth/register` and `/api/auth/upgrade` are `response_model=UserOut` → `{id, email, username}`. The frontend validates both responses with `TokenPairSchema` (requires `access_token`/`refresh_token`), then calls `setTokens(...)`. `http-client.request` does `schema.safeParse` and **throws** on mismatch, so **every registration and every guest→registered upgrade fails client-side** with a generic toast — even though the backend committed the account (upgrade mutates the user in place first, leaving the client holding a now-stale guest token).

**Fix:** pick one side and align both. Preferred: mint and return a `TokenPair` from `register` and `upgrade`. Add a cross-stack contract test on these response shapes.

---

## 🟠 HIGH

### Backend

- **H-B1 · Conversation history is loaded but never reaches the LLM** — `backend/agents/nodes.py:68,81-90`. `_build_graph_state` loads the last-N turns into `state['history']`, but the only consumer `_rewrite_query(query, history)` **ignores `history`** and returns the current query; supervisor routing and synthesis never see prior turns. Multi-turn chat is effectively single-turn; `HISTORY_MAX_TURNS` + the per-request load are pure overhead. _Fix: thread history into the routing/synthesis prompts (or implement real rewrite); stop advertising history in docstrings until wired._
- **H-B2 · Indirect prompt injection: untrusted web/document context injected into synthesis prompt with no isolation** — `backend/agents/nodes.py:218-249`, `backend/llm/_prompts.py:102-117`. Web snippets (attacker-controlled) and uploaded docs are concatenated verbatim under a plain label; system prompts don't mark them as data-not-instructions, and component `media.url`/`citation` destinations aren't allowlisted. _Fix: fence untrusted context, add a system guard, validate emitted URLs to https-only._
- **H-B3 · Vector ids are not per-document unique → silent overwrite + orphaned vectors** — `backend/components/preprocessing.py:95`. Id = `f"{session_id}_{filename}_{i:04d}"` (no `document_id`/uuid). Two same-named docs in one session clobber each other; a shorter re-ingest leaves stale high-index chunks that still match retrieval. _Fix: include `document_id` in the id; delete prior vectors before re-upsert._
- **H-B4 · Auth endpoints (login/register/guest/refresh) have no rate limiting; guest mint is unbounded** — `backend/auth/router.py`. No `@limiter.limit` on any auth route and **no global default limit**. Enables credential stuffing/brute force, account-enumeration spam, and unbounded guest minting (each = DB row + bcrypt). Minted guests also become distinct rate-limit buckets that bypass per-user chat/upload limits. _Fix: per-IP limits on login/guest/register/refresh + a sane global default._
- **H-B5 · Anthropic synthesis hard-capped at 1024 output tokens, silently truncating** — `backend/llm/anthropic.py:43,54,69`. `_GENERATE_MAX_TOKENS=1024` on both `create` and `stream`; `stop_reason` is never inspected. Rich markdown + a ```json component block routinely exceeds this; a half-emitted block fails `parse_components` and is dropped. OpenAI/Gemini pass no cap, so the same query is fuller there — a provider-dependent regression. _Fix: raise to ~4-8k or make configurable; inspect `stop_reason`._
- **H-B6 · No timeout/retry on any LLM client** — `backend/llm/{anthropic,openai,gemini}.py`. Clients built with only the API key; a hung upstream pins the request (and SSE generator / DB-Redis resources) indefinitely; transient 429/529 fail the user immediately. _Fix: explicit `timeout=`, bounded jittered retry honoring `Retry-After`._
- **H-B7 · Gemini streaming iterates a sync generator on the event loop → starvation** — `backend/llm/gemini.py:47-57`. Only stream *creation* is off-loaded to a thread; the `for chunk in chunks` loop runs blocking `next()` on the asyncio loop. Gemini is the **default + free-tier** provider, so one slow stream stalls every concurrent request. _Fix: use the async streaming API, or pump `next()` through `to_thread` per chunk._
- **H-B8 · Local `.env` (real secrets) is copied into the Docker image** — `backend/Dockerfile:20` (`COPY . .`, no `.dockerignore`). `.env` is gitignored (✓ not in git history) but **still in the build context**, so it lands at `/app/.env` in an image layer: Neon prod DB creds, Google/Pinecone/HF keys, `JWT_SECRET`, and the **Fernet master key that decrypts all users' BYOK keys**. _Fix: add `backend/.dockerignore` (`.env*`, `.git`, caches, `tmp_uploads/`), inject secrets at runtime, **rotate the secrets currently in `backend/.env`**._
- **H-B9 · Backend container runs as root** — `backend/Dockerfile` (no `USER`). Untrusted PDF/docx parsing (pymupdf/python-docx) as uid 0 maximizes container-escape blast radius. _Fix: create and switch to a non-root user; add a HEALTHCHECK._

### Frontend

- **H-F1 · `javascript:` citation URL reaches an anchor `href` (XSS)** — `frontend/features/chat/components/rich/component.schemas.ts:57` → `citation.tsx:17` → `sources-panel.tsx:49`. Zod `z.string().url()` validates **syntax, not protocol**, so `javascript:`/`vbscript:`/`data:` pass; the media path added an `isSafeHttpUrl` guard but the citation path didn't (and its comment wrongly claims only http(s) survive). Rich-components flag defaults **on**, so it's live. _Fix: restrict to `^https?$` in the schema and/or mirror `isSafeHttpUrl` at the citation boundary._
- **H-F2 · Access AND refresh tokens persisted in `localStorage`** — `frontend/features/auth/store/auth.store.ts:75-81`. Any XSS (incl. a compromised dep or a markdown/rich regression) reads the **long-lived refresh token** → durable, refreshable account takeover. The store header itself flags this and names the BFF/httpOnly-cookie upgrade path. _Fix: refresh token to httpOnly cookie via a BFF; at minimum keep it memory-only (don't partialize). Pair with a CSP (H-F4)._
- **H-F3 · Logout doesn't clear `rag_session_id`, provider selection, or in-memory chat → cross-user leakage on a shared device** — `frontend/features/auth/hooks/use-auth.ts:30-36`. `logout()` only does `authStore.clear()` + `qc.clear()`. The stable `rag_session_id` (which the backend binds to the user) survives, so user B inherits user A's session and can read/continue A's conversation/memory/graph. _Fix: on logout also `useChatStore.reset()`, rotate/remove `rag_session_id`, `clearSelection()` — ideally wipe all `rag_*` on identity change._
- **H-F4 · No CSP / security headers** — `frontend/next.config.ts:4-6` (no `headers()`, no middleware) and the backend-served SPA (`backend/app.py:154,693-695`) likewise. With XSS-readable tokens and model-generated markdown/images, there's no CSP `connect-src`/`script-src` to limit exfiltration, and no `frame-ancestors`/`X-Frame-Options` (clickjackable). _Fix: strict CSP + HSTS + `X-Content-Type-Options` + `Referrer-Policy` + `Permissions-Policy`._
- **H-F5 · No error boundary anywhere** — `frontend/components/chat/chat-message.tsx:126-132`. Zero `ErrorBoundary`/`componentDidCatch` in the tree. A single malformed-but-schema-valid chart/table (see Infinity finding) or a failed recharts/highlighter dynamic chunk throws during render and **unmounts the whole chat surface**. _Fix: wrap each `ComponentBlock` (and ideally each `ChatMessage`) in a boundary that degrades to `RawFallback`._
- **H-F6 · No request timeout/abort in the fetch wrapper** — `frontend/lib/api/http-client.ts:109-119`. No `AbortSignal.timeout`; a backend that accepts but never responds leaves the promise unsettled forever (React Query `retry` doesn't fire on a hang). _Fix: compose `AbortSignal.any([signal, AbortSignal.timeout(ms)])`; add a `timeout` kind to `ApiError`._
- **H-F7 · `NEXT_PUBLIC_API_URL` silently defaults to `localhost`** — `frontend/lib/env.ts:20`. The `.default("http://localhost:8000/api")` makes the "invalid env throws" path dead for a missing URL, so a prod build that forgets the var ships a client that calls localhost. _Fix: require it (drop the default in production) or `.refine()` to reject localhost when `NODE_ENV==='production'`._
- **H-F8 · CI never runs the test suite** — `frontend/.github/workflows/ci.yml`. Only lint/format/typecheck/build; `vitest run` is never invoked despite ~44 test files (auth, streaming lifecycle, http-client refresh, SSE parser, schema contracts). Regressions (e.g. the recently-fixed streaming abort race) can merge green. _Fix: add a `npm run test` step._
- **H-F9 · Sentry can leak PII/chat content (no scrubbing)** — `frontend/lib/observability/sentry.ts:48-63,87-98`. No `beforeSend`/`beforeSendBreadcrumb`/`denyUrls`; `captureError` dumps an arbitrary `context` via `setContext("extra", …)` unredacted. Default breadcrumbs (fetch URLs with session ids, console, DOM) plus any caller context can ship emails/prompts to a third party. Gated by DSN + observability flag, so dark by default. _Fix: add scrubbing, `sendDefaultPii:false`, allow-list the `context` keys._
- **H-F10 · Frontend container runs as root** — `frontend/Dockerfile:17-39` (no `USER node`, no HEALTHCHECK). _Fix: drop privileges in the runner stage._

### Cross-cutting

- **H-X1 · Blocking JSON `/api/chat` returns route `BOTH`, absent from the frontend `routeTypeSchema`** — `backend/app.py:641` vs `frontend/features/chat/api/chat.schemas.ts:4-30`. The flat graph enum includes `BOTH` (docs present + web allowed); the streaming path maps `BOTH→WEB+RAG`, but the blocking path doesn't, so `chatResponseSchema.safeParse` **rejects an otherwise-successful answer** and turns it into an error turn. _Fix: map `BOTH→WEB+RAG` in the JSON branch (or add `BOTH` to the schema + map it); add a contract test._
- **H-X2 · No refresh-token rotation/revocation** — `backend/auth/router.py:122-132`, `backend/auth/security.py:67-79`. `/refresh` is a pure stateless re-mint: no `jti`, no rotation/denylist, no logout endpoint, no `aud`/`iss`, and it doesn't re-load the user (deleted users keep minting for up to 7 days). Combined with localStorage storage + a shared HS256 secret, theft = persistent takeover with no kill switch. _Fix: server-side jti/rotating family with reuse detection; invalidate on logout/upgrade; add `aud`/`iss`._
- **H-X3 · `confirm_upload` s3_key trust** — cross-cutting restatement of **C2** (data-integrity + existence-oracle facet).
- **H-X4 · `upgrade` UserOut/TokenPair mismatch** — cross-cutting facet of **C3** (guest→registered upgrade specifically).

---

## 🟡 MEDIUM (grouped by theme)

**Tenant isolation & authz (defense-in-depth gaps under C1/C2)**
- Repository queries lack tenant scoping; ownership is enforced ad-hoc in `app.py`, not at the data layer — `repository.py`.
- Pinecone search filtered by `session_id` only, no `user_id` namespace/filter — `db_manager.py:74-76`.
- Unowned sessions readable/claimable by any user via memory/graph/chat — `app.py:265-284,408-441`.
- `_resolve_session` ownership claim can race between concurrent users (no `FOR UPDATE`) — `app.py:274-284`.
- BYOK keys router has no rate limiting — `auth/keys_router.py`.
- Freemium quota check is non-atomic across two Redis counters → bounded overshoot/false-deny under concurrency — `llm/freemium.py:55-94`.

**Error envelope / information disclosure**
- SSE error event forwards raw `str(exc)` (DSNs, paths, internals) to the client — `app.py:596-602`.
- 429 responses use slowapi's `{error:…}` envelope, not the `{detail,code}` the FE parses → users see "Backend error: 429" — `app.py:152`.
- Rate-limit key falls back to IP for invalid tokens; per-IP throttling is bypassable per-guest and inaccurate behind a proxy — `app.py:133-152`.

**Resilience / timeouts (server side)**
- External clients (S3 boto3, HuggingFace, DuckDuckGo) have no explicit connect/read timeouts — `integrations/*`.
- No Celery `task_time_limit`/`soft_time_limit`; `asyncio.run` per task — `worker/{celery_app,tasks}.py`.
- Per-task DB engine built+disposed every ingest → connection-pool churn (Neon limits) — `worker/tasks.py:39-50`.
- DuckDuckGo `@retry` is **dead code** (broad try/except swallows the exception tenacity needs to see); transient failures silently return `[]` — `integrations/duckduckgo/client.py:18-26`.
- Pinecone `match.metadata["text"]` hard subscript → `KeyError` aborts the retrieval batch — `db_manager.py:79`.
- Gemini `resp.text` can be `None` (safety block) → opaque `AttributeError` — `gemini.py:45`.

**Config / secrets / deploy**
- Secrets typed as plain `str` (not `SecretStr`): `GOOGLE_API_KEY`, AWS keys, `DATABASE_URL`, `JWT_SECRET`, Fernet key, HF token — leak via repr/log — `config.py`.
- No `JWT_SECRET` strength validation; no prod guard for insecure defaults (empty CORS, localhost Redis); rate-limit store can silently be `memory://`/per-instance — `config.py`.
- `docker-compose` publishes Redis (no password) and MinIO (`minioadmin/minioadmin`) on all interfaces; no app/worker services; no secret-scanning pre-commit hook — `docker-compose.yml`, `.pre-commit-config.yaml`.
- No container HEALTHCHECK; single-stage image ships build toolchain — `backend/Dockerfile`.
- Frontend: Dockerfile uses `npm install` not `npm ci` (ignores lockfile); no `images.remotePatterns` allowlist; Node version mismatch CI 20 vs Docker 22 with no `engines` pin — `frontend/{Dockerfile,next.config.ts}`, `ci.yml`.

**Frontend correctness / state / a11y**
- `done.answer` overwrites streamed content even when empty — `use-streaming-chat.ts:216-228`.
- Memory refresh after stream-done can target a rotated/wrong session — `use-streaming-chat.ts:231`.
- Concurrent-401 handling fires `clear()`+redirect once per request (not single-flighted) — `http-client.ts:153-194`.
- `auth:true` with a null access token sends no header → spurious refresh→logout bounce — `http-client.ts:72-81`.
- SSE chat path can't refresh a pre-stream 401 and has no idle timeout — `stream-chat.ts`.
- Persisted Zustand stores have no `version`/`migrate` → stale-shape rehydration after deploys — `lib/store/persist.ts`.
- Model picker offers providers with no stored key → persistently-broken turns, loses free tier — `model-picker.tsx`.
- Knowledge-graph panel renders force-graph with no node/edge cap → can freeze the tab — `graph-panel.tsx`.
- Refresh token in localStorage (restated under H-F2); open-redirect via `?next` (restated under H-X/F).
- Sidebar/insights toggles remove focusable controls without moving focus (WCAG 2.4.3) — `chat-screen.tsx`.

**Operability (cross-cutting)**
- `/health` is shallow liveness only — no readiness probe for DB/Redis/Pinecone/S3 — `app.py:698-700`.
- No per-request correlation id bound to logs/traces — `logging_config.py`, `app.py`.
- Free-tier allowance reserved before the graph runs and **never refunded on failure/abort** → retries burn the shared quota — `llm/dependencies.py:57-65`.

**Model defaults**
- Anthropic tiers point at deprecated `claude-3-5-*-latest`; "strong synth" should be a current pinned Sonnet/Opus 4.x, not 3.5, and `-latest` risks silent drift — `config.py:71-72`, `anthropic.py:35`.
- No provider usage/cost accounting captured from any response (can't verify prompt-cache ROI or bill by tokens) — `llm/base.py`.
- Markdown memory truncation slices mid-note (corrupts the oldest entry, re-fed to synthesis) — `memory/markdown.py:56-57`.
- Entity extraction sends full doc text to the **operator's** Gemini key by default with no per-user consent/PII gating — `memory/extract.py:95`, `config.py:108-116`.

---

## 🟢 LOW / ℹ️ INFO (condensed; full prose in the workflow output)

**Backend correctness/robustness:** generic "free tier Limit Reached" 500 masks all graph exceptions (`app.py:629-633`); `_count_context_chunks` substring-counts `"CONTEXT "` (`app.py:461-469`); SSE persistence drops the user turn on early disconnect, no idempotency on retries (`app.py:603-612`); StaticFiles/FileResponse use CWD-relative paths (crashes if launched elsewhere) (`app.py:154,695`); supervisor swallows LLM auth/rate-limit errors into a blank route (`nodes.py:57-67`); `_resolve_decision` keys on context-string truthiness not `docs_relevant` (`nodes.py:130-148`); DDG result dicts indexed by required keys (`duckduckgo/client.py:23`); `doc_parser` double-wraps the scanned-page error and rejects whole PDFs on one low-text page with no size/page cap (`doc_parser.py:20-38`).

**Backend security (low):** login user-enumeration timing oracle (`router.py:113-115`); JWT lacks `aud`/`iss` (`security.py`); password silently truncated to 72 bytes vs rejected (`security.py:19-29`); single Fernet key, no MultiFernet/rotation, `InvalidToken` → unhandled 500 (`crypto.py`); message preview + full session_id logged at INFO (`app.py:541-547,663`); no upload size limit / content-type allowlist (`app.py:293-312`); unsanitized filename → S3 key tail (`s3/client.py:67-74`); Langfuse keys copied into `os.environ` (`observability/langfuse.py:31-33`); routing-prompt injection on the raw query (`_prompts.py:50-59`).

**Backend data/perf (low):** no composite `(session_id, created_at)` index for the hot history query (`models.py:140-153`); naive (tz-less) timestamps on sessions/documents vs tz-aware elsewhere; no DB CHECK on `Message.role`/`UserLLMKey.provider`; `DocumentStatus` enum values duplicated between model and migration with no sync test; engine pool lacks `pool_pre_ping`/`recycle` for Neon (`session.py:32-34`); Celery result backend = broker with no `result_expires`; `_mark_failed` can clobber another doc via unique-`s3_key` UPDATE; module-level `Settings()` at import time (brittle CI/tests); compose has no app/worker services; uvicorn binds `0.0.0.0` single-worker, no `--proxy-headers`; OTEL sample ratio defaults to 1.0; ConsoleSpanExporter fallback spews identifier-bearing spans to stdout; KG Redis lock has no blocking timeout; hybrid retriever passes the whole vector context as one fixed-score hit (fusion inert) + graph seeds from raw single tokens.

**Frontend (low/info):** open-redirect `?next` (also High); rich blocks keyed by array index (`chat-message.tsx:129`); markdown `<img>` has no `referrerPolicy`/allowlist (tracking beacon) (`lib/markdown/components.tsx`); chart accepts `Infinity`, no finite/size guards (`component.schemas.ts:40`); Dockerfile not `output:'standalone'`, no `.dockerignore`; no `npm audit`/Dependabot; `FreeTierExhaustedDialog` O(n) reverse+scan per streamed token; API-key list query not identity-scoped (relies on `qc.clear`); save-key create-vs-update routes on stale client list state; `formatRelativeTime` caps at days + swallows future timestamps; React Query `retry:1` retries 4xx (re-runs refresh dance); module-level `refreshInFlight` unsafe if `request()` ever runs on the server; SSE early-return never cancels the response body; positional merge of `done.sources` layers can mis-assign provenance; blocking chat fabricates synthetic placeholder "sources"; `chat.store.reset()` leaks `draft`/`webSearchAllowed`; provider store has no rehydration gate (label flicker + pre-hydration send uses wrong provider); auth persisted blob not removed on logout; clipboard helper has no insecure-context fallback; trace `randomBytes` can throw / module-scope `lastTraceId` shared across SSR requests; settings page reachable by minted guests; insights drawer not a dialog (no focus trap/Escape); login Suspense fallback renders nothing; hardcoded personal email + repo URLs in the shipped bundle.

**Cross-cutting (low/info):** streaming session-ownership claim depends on dependency-commit timing (latent if future deferred writes are added); KG load→mutate→save not atomic across processes (fixed-TTL lock, last-writer-wins); CORS `allow_credentials=True` + `allow_headers='*'` unnecessary for Bearer auth; FE `KeyMeta.last4` advertised but backend never returns it; SSE `done.layers` emitted by backend but stripped by FE schema, while the FE's read paths (`done.sources[i].layer`, citation `.layer`) are never populated → provenance badges never render; SSE parser flushes a trailing partial frame on stream end; READMEs disagree on storage backend (B2/MinIO vs AWS S3) and under-document the Phase-7 auth surface.

---

## Refuted (2)
- "alembic.ini commits a live DB URL" — the placeholder is **dead config**; `migrations/env.py` always overrides from `DATABASE_URL`.
- Memory-panel markdown rejected as XSS — the `a` renderer **does** set `rel="noopener noreferrer"`, no `rehype-raw`; residual is self-phishing only.

## Quick triage order
1. **C1–C3** (tenant isolation, confirm_upload, broken register/upgrade) — block release.
2. **H-X2 / H-B4 / H-F2 / H-F3 / H-F4** (refresh-token lifecycle, auth rate-limiting, token storage + logout cleanup + CSP) — the auth/security spine.
3. **H-B1 / H-B5 / H-B7 / H-X1 / H-F1 / H-F5** (history-to-LLM, Anthropic cap, Gemini loop block, BOTH route, citation XSS, error boundary) — correctness/availability.
4. **H-B8/B9/H-F10 + H-F8** (secrets-in-image, root containers, CI test gate) — supply-chain/CI hygiene.
