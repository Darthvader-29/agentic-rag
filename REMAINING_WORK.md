# Remaining-work queue — post-bug-sweep

This file is the **single source of truth** for what is left to implement after the
`fix/bug-sweep` correctness loop completed (B01–B26 all `DONE`). It uses the same
one-item-per-commit, fully-resumable protocol as `BUGFIX_QUEUE.md`: any fresh/wiped
context can re-read this file and continue from where the last iteration left off.

Authored against `fix/bug-sweep` (28 commits ahead of `main`). Line numbers below are
**approximate hints** — trust the described symptom/goal, not the exact line, and confirm
against current code before editing.

## Loop protocol (read this every iteration)

1. **Pick**: find the **first** entry whose status is `TODO`. If none remain, the run is
   complete — stop and report a summary. Skip `DONE` / `SKIPPED` / **Deferred** entries.
2. **Confirm**: open the cited files and verify the gap still exists. If already done, mark
   `SKIPPED (already implemented)` and move on — do not invent work.
3. **Build**: make the smallest change that delivers the described goal. Stay in scope; do
   not refactor unrelated code or pick up other entries opportunistically.
4. **Test**: add/update a test that fails before and passes after (backend: `pytest` for the
   touched area; frontend: `vitest run <file>`). Note unrelated pre-existing reds; don't let
   them block a verified change.
5. **Record**: change the entry's status `TODO` → `DONE` and append the commit sha.
6. **Commit + push**: one focused commit. `feat(<area>)/fix(<area>)/chore(<area>): <title>  [<ID>]`.
   Then `git push origin <branch>`.
7. **One item per iteration.** After pushing, end the iteration.

Branch: continue on `fix/bug-sweep` (or cut a `feat/remaining-work` off it once it's merged).
**Never commit to `main`.** Status legend: `TODO` · `DONE` · `SKIPPED (<reason>)`.

> **Context from the scan (2026-06-14):** the project is feature-complete against its roadmap
> (backend P0–P7, frontend M0–M10 all built). What remains is: (1) ship the stranded branch,
> (2) two genuine feature gaps, (3) the security/resilience backlog already itemized in
> `CODE_REVIEW.md`, and (4) activating + e2e-verifying the dark-launched features.

---

## Priority 0 — Release blockers (operational; do BEFORE the code loop)

### REL-1 — Nothing is merged to `main`: tenant-isolation fix + all 26 bug fixes are stranded
- **Status:** TODO
- **Type:** operational (no regression test; exit = CI green)
- **Detail:** `main` (86 commits, tip `2c3bd91`) has the feature code but **not** the security
  fix (`689814a`, C1/C2/C3 tenant isolation + auth contract) nor B01–B26. `fix/bug-sweep` =
  `main` + 28 commits and `fix/bug-sweep..main` is **empty** → clean fast-forward.
- **Action:** review and merge `fix/bug-sweep` → `main` (open the PR if a review gate is wanted;
  otherwise `git merge --ff-only`). Confirm frontend CI + Jenkins are green on the merge commit.
- **Exit:** `git log main..fix/bug-sweep` is empty; CI green on `main`.

### REL-2 — Rotate secrets that were baked into the Docker image; add `.dockerignore`
- **Status:** TODO
- **Type:** operational + small code change
- **Detail:** `CODE_REVIEW.md` H-B8 — the local `.env` (real prod secrets incl. the Fernet
  master key) is copied into the backend image; there is no `.dockerignore`.
- **Action:** rotate the Fernet `LLM_KEY_ENCRYPTION_KEY`, JWT secret, DB/Redis/S3/provider creds
  in your secret store; add `backend/.dockerignore` + `frontend/.dockerignore` excluding `.env*`,
  `.venv`, `node_modules`, tests, docs; stop copying `.env` in the Dockerfiles. (Overlaps R08.)
- **Exit:** no secret material in a built image layer; `.dockerignore` present both sides.

---

## Priority 1 — Genuine feature gaps (truly not implemented)

### R01 — Conversation history never reaches the LLM (multi-turn is effectively single-turn)
- **Status:** DONE
- **Severity:** HIGH (core capability) · `CODE_REVIEW.md` H-B1 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `backend/agents/nodes.py` (`_rewrite_query` ~:81, `supervisor_node` ~:58-68,
  `synthesis_node` ~:224), `backend/llm/base.py` (`route`/`generate`/`stream` ~:37-47),
  `backend/llm/_prompts.py` (`routing_user` ~:51, `generation_user` ~:117),
  `backend/agents/state.py` (`history` ~:37), `backend/app.py` (history load ~:522-536).
- **Symptom:** History is loaded into `GraphState` and passed to `_rewrite_query(query, history)`,
  but the function **ignores `history`** and the provider contract has **no history parameter**.
  Follow-ups ("what about the second one?") can't resolve. Contradicts `09_Phase6 §2`.
- **Goal/approach:** Decide the prompt design (this is the deferred design call), then thread
  `history` (last-N turns, already in state) through `LLMProvider.route/generate/stream` and the
  routing/synthesis prompt builders; implement the real history-aware `_rewrite_query` (or fold
  rewriting into `provider.route`). Keep cost bounded (cap N; verbatim last-N is fine to start).
- **Test:** a follow-up turn whose referent is only in prior history routes/synthesizes correctly
  (e.g. supervisor resolves "the second one" using history); prompt builders include history.

### R02 — Presigned upload (M8) is unbuilt on the frontend; flag consumed nowhere
- **Status:** DONE
- **Severity:** MEDIUM (planned feature) · frontend `M8`
- **Files (new):** `frontend/features/upload/` (hook + component + api + tests). Touch
  `frontend/components/chat/chat-input.tsx` (~:52 upload trigger). Backend is **ready**:
  `backend/app.py` `POST /api/upload` (~:358), `POST /api/upload/confirm` (~:384),
  `GET /api/documents/{id}` (~:424). Legacy fallback: `frontend/features/chat/api/chat.api.ts` (~:67).
- **Symptom:** `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD` exists (`lib/flags.ts` ~:20) but has zero
  consumers; uploads still run the blocking multipart path.
- **Goal/approach:** Build the presigned flow gated by the flag, with the current `api.uploadFile`
  as the flag-off fallback: request presign → PUT file straight to S3 → `POST /upload/confirm` →
  poll `GET /api/documents/{id}` (TanStack `refetchInterval`) until `ready|failed` → progress UI.
- **Test:** vitest — with the flag on, the hook issues presign→PUT→confirm→poll and surfaces
  `ready`/`failed`; with the flag off, it falls back to the multipart path.

### R03 — `POST /api/auth/logout` does not exist server-side
- **Status:** DONE
- **Severity:** MEDIUM · (pairs with R04 token revocation)
- **Files:** `backend/auth/router.py` (no logout route), `backend/auth/security.py`,
  `frontend/features/auth/hooks/use-auth-mutation.ts` (logout path).
- **Symptom:** Auth router has register/guest/upgrade/login/refresh only; logout is client-side
  token-drop with no server kill-switch.
- **Goal/approach:** Add `POST /api/auth/logout` that revokes the refresh token (denylist its
  `jti` — see R04) so a stolen token can't be refreshed; frontend calls it on logout.
- **Test:** after logout, a refresh with the old token is rejected (401).

---

## Priority 2 — Security hardening (`CODE_REVIEW.md`, criticals already fixed)

### R04 — No refresh-token rotation / revocation / `jti` denylist; no `aud`/`iss`
- **Status:** DONE
- **Severity:** HIGH · `CODE_REVIEW.md` H-X2 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `backend/auth/router.py` (`refresh` ~:135), `backend/auth/security.py` (claims/mint),
  `backend/database/models.py` (denylist/rotation table if DB-backed), Settings.
- **Symptom:** Refresh re-mints statelessly; no rotation, no revocation list, no `aud`/`iss`. A
  deleted/compromised user keeps minting for the token lifetime (~7 days).
- **Goal/approach:** Rotate the refresh token on each use; track `jti` in a denylist (Redis or a
  table) checked on refresh/logout; add `aud`/`iss` claims and validate them. Re-derive identity
  from the DB user on refresh.
- **Test:** a rotated/denylisted refresh token is rejected; `aud`/`iss` mismatch is rejected.

### R05 — Auth endpoints have no rate limiting (unbounded guest minting / credential stuffing)
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-B4 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `backend/auth/router.py` (login/register/guest/refresh), `backend/app.py` (slowapi
  limiter ~:137-156).
- **Symptom:** Chat is rate-limited but auth is not; `/auth/guest` can be minted without bound.
- **Goal/approach:** Apply per-IP (and where sensible per-account) slowapi limits to the auth
  routes; pick limits that don't break the guest-on-load flow.
- **Test:** exceeding the configured auth-rate limit returns 429 in the FE's `{detail,code}` shape
  (see R27).

### R06 — Citation/media URL XSS: `javascript:`/`data:` reaches an anchor `href`
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-F1 · `BUGFIX_QUEUE.md` Deferred (rich flag defaults ON)
- **Files:** `frontend/features/chat/components/rich/citation.tsx`, `…/media.tsx`,
  `frontend/features/chat/components/rich/component.schemas.ts`; backend
  `backend/agents/schemas.py` (`CitationItem`/`MediaItem` url validation).
- **Symptom:** `z.string().url()` validates syntax, not protocol, so a `javascript:`/`data:` URL
  can land in `href`/`src`.
- **Goal/approach:** Allowlist `http(s)` (and `mailto:` if wanted) on both sides before rendering;
  drop/disarm anything else. Keep the field preserved (B08) but render-safe.
- **Test:** a `javascript:` citation URL renders inert (no executable href); `https:` works.

### R07 — No security headers (CSP / HSTS / X-Frame-Options) on backend or frontend
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-F4 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `backend/app.py` (response middleware), `frontend/next.config.ts` (`headers()`).
- **Symptom:** No CSP/HSTS/frame-deny on either app or the backend-served SPA.
- **Goal/approach:** Add a strict CSP (script/style/connect/img allowlists incl. the API origin),
  HSTS, `X-Frame-Options: DENY`/`frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy`. Verify the SSE/connect-src and Sentry/analytics origins are allowed.
- **Test:** responses carry the headers; the app still loads + streams under the CSP.

### R08 — Containers run as root; no HEALTHCHECK; `.dockerignore` missing
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-B9/H-F10 · `BUGFIX_QUEUE.md` Deferred (overlaps REL-2)
- **Files:** `backend/Dockerfile`, `frontend/Dockerfile`, new `.dockerignore` both sides.
- **Goal/approach:** Add a non-root `USER`, a `HEALTHCHECK`, `.dockerignore` (exclude `.env*`,
  `.venv`, `node_modules`, tests/docs); frontend `npm ci` + `output:'standalone'` slim image;
  backend multistage to drop the build toolchain.
- **Test:** image runs as non-root (`id -u` ≠ 0) and starts; healthcheck passes.

### R09 — Indirect prompt injection: untrusted web/doc context is concatenated unfenced
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-B2
- **Files:** `backend/llm/_prompts.py` (`generation_user`/`generation_system`),
  `backend/agents/nodes.py` (`_assemble_context` ~:173-202).
- **Goal/approach:** Fence retrieved/web context as untrusted data (clear delimiters + a system
  instruction to treat it as content, not instructions); https-allowlist any URLs surfaced in the
  answer. Don't weaken the component protocol.
- **Test:** a context blob containing "ignore previous instructions…" does not change routing/format.

### R10 — Access + refresh tokens live in `localStorage` (XSS → durable takeover)
- **Status:** TODO
- **Severity:** HIGH (design) · `CODE_REVIEW.md` H-F2 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `frontend/features/auth/store/auth.store.ts`, `frontend/lib/api/http-client.ts`,
  backend cookie-auth support if chosen.
- **Symptom:** Bearer-from-`localStorage` is XSS-readable; mitigations exist (short TTL, HTML-off
  markdown) but the exposure is durable via the refresh token.
- **Goal/approach:** Move to httpOnly/SameSite cookies via a BFF or backend cookie-auth path. This
  is a larger architectural change — may stay **Deferred** pending a decision; tighten CSP (R07)
  meanwhile.
- **Test:** tokens are not readable from JS (`window.localStorage`); auth still works over cookies.

### R11 — Open-redirect via unvalidated `?next`; Sentry has no PII scrubbing
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` (H-F redirect, H-F9)
- **Files:** `frontend/features/auth/*` (post-login redirect), `frontend/lib/observability/sentry.ts`.
- **Goal/approach:** Validate `?next` is a same-origin relative path before redirecting; add a
  Sentry `beforeSend` scrub + `sendDefaultPii:false` so emails/prompts/session-ids aren't shipped.
- **Test:** an absolute/off-origin `?next` is rejected; Sentry payload omits PII fields.

---

## Priority 3 — Resilience & correctness (`CODE_REVIEW.md`)

### R12 — No timeout/retry on any LLM client
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-B6 · `BUGFIX_QUEUE.md` Deferred
- **Files:** `backend/llm/{gemini,openai,anthropic}.py`, `backend/llm/base.py`.
- **Symptom:** A hung upstream pins a request forever; transient 429/529 fail immediately.
- **Goal/approach:** Add connect/read timeouts + bounded tenacity retry/backoff on transient
  statuses across all three adapters (shared helper); map exhaustion to the existing taxonomy.
- **Test:** a simulated timeout raises a handled provider error (not a hang); a transient 429 retries.

### R13 — Gemini streaming iterates a sync generator on the event loop (starves concurrency)
- **Status:** TODO
- **Severity:** HIGH · `CODE_REVIEW.md` H-B7 · `BUGFIX_QUEUE.md` Deferred (Gemini = default free tier)
- **Files:** `backend/llm/gemini.py` (stream path).
- **Goal/approach:** Offload the blocking iterator (`asyncio.to_thread` per chunk via a queue, or
  the async Gemini API) so streaming doesn't block other requests.
- **Test:** a concurrent request makes progress while a Gemini stream is in flight (no serialization).

### R14 — Anthropic synthesis capped at 1024 output tokens → silent truncation; `-latest` model ids
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` H-B5 + model-pin
- **Files:** `backend/llm/anthropic.py` (`max_tokens` ~:30, tier model ids).
- **Symptom:** Long answers/rich-component blocks get truncated; `stop_reason` is never inspected;
  tiers point at deprecated `claude-3-5-*-latest`.
- **Goal/approach:** Raise the output cap to a sensible bound, inspect `stop_reason` (handle
  `max_tokens` gracefully), and pin to current Claude 4.x model ids.
- **Test:** a long synthesis isn't truncated mid-component; a `max_tokens` stop is handled, not silent.

### R15 — Vector IDs aren't per-document unique (same-named docs clobber)
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` H-B3
- **Files:** `backend/components/preprocessing.py` (chunk id `session_id_filename_i`),
  `backend/database/db_manager.py` / ingestion.
- **Goal/approach:** Include the document id (UUID) in the vector id so re-uploading a same-named
  file doesn't overwrite/orphan; ensure cleanup targets the right ids.
- **Test:** ingesting two same-named docs keeps both sets of chunks retrievable; cleanup removes the right one.

### R16 — Free-tier allowance reserved before the graph runs, never refunded on failure/abort
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` (freemium)
- **Files:** `backend/llm/freemium.py`, `backend/llm/dependencies.py`, `backend/app.py` (chat path).
- **Goal/approach:** Refund/credit the counter when the turn fails or is aborted before producing an
  answer; make the reserve+refund atomic so retries don't burn shared quota.
- **Test:** a failed turn leaves the allowance counter unchanged net-of-refund.

### R17 — Frontend has no error boundary around rich-component rendering
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` H-F5
- **Files:** `frontend/features/chat/components/rich/component-block.tsx` (wrap), new boundary.
- **Symptom:** One malformed-but-schema-valid chart/table can throw and unmount the whole chat surface.
- **Goal/approach:** Wrap each rendered rich component in an error boundary that falls back to a
  collapsed raw block on render error, isolating the failure to that block.
- **Test:** a component that throws on render shows the fallback; the rest of the chat stays mounted.

### R18 — Fetch wrapper has no request timeout / abort
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` H-F6
- **Files:** `frontend/lib/api/http-client.ts`.
- **Goal/approach:** Add an `AbortController` timeout to the fetch wrapper (and wire abort on
  unmount/navigation) so a hung backend doesn't leave a promise unsettled forever.
- **Test:** a never-resolving request rejects after the timeout.

### R19 — External clients (S3/HF/DDG) have no timeouts; DDG `@retry` is dead code
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` (resilience)
- **Files:** `backend/integrations/{s3,huggingface,duckduckgo}/client.py`, `backend/integrations/_retry.py`.
- **Symptom:** No connect/read timeouts; DDG wraps the body so `@retry` never fires and a failure
  silently returns `[]`.
- **Goal/approach:** Add timeouts to each client; let DDG errors propagate to `@retry` (don't
  swallow) and surface a real failure / empty signal distinctly.
- **Test:** a DDG error triggers a retry then a surfaced failure (not a silent `[]`).

---

## Priority 4 — Infra / CI / deploy

### R20 — Frontend CI never runs the test suite
- **Status:** TODO
- **Severity:** MEDIUM · `CODE_REVIEW.md` H-F8 · `SUGGESTIONS.md`
- **Files:** `frontend/.github/workflows/ci.yml` (lint/format/typecheck/build only).
- **Goal/approach:** Add a `npm run test -- run` (vitest) step to the `quality` job; ~40 test files
  currently never gate a PR.
- **Test:** CI fails when a vitest test fails (verify with a deliberately broken test locally).

### R21 — No Playwright E2E suite (M5 deliverable absent)
- **Status:** TODO
- **Severity:** MEDIUM · frontend `M5`
- **Files (new):** `frontend/playwright.config.ts`, `frontend/e2e/chat.spec.ts`.
- **Goal/approach:** Add the E2E half of M5: load → send → assistant reply → upload → theme toggle →
  reset, against MSW or a stubbed backend; wire into CI.
- **Test:** the spec passes locally + in CI.

### R22 — Backend CI is Jenkins-only; no GitHub Actions; mypy skips some packages
- **Status:** TODO
- **Severity:** LOW-MEDIUM · P0 exit asked for GitHub Actions
- **Files:** `backend/Jenkinsfile` (type-check stage omits `agents auth llm memory`), new
  `backend/.github/workflows/ci.yml` (or root).
- **Goal/approach:** Add a GitHub Actions backend workflow (ruff + mypy over **all** packages +
  pytest + coverage gate) for PR parity with the frontend; align the mypy target set.
- **Test:** the workflow runs green on a PR and red on a lint/type/test failure.

### R23 — `/health` is liveness-only; Docker/compose hardening
- **Status:** TODO
- **Severity:** LOW-MEDIUM · `CODE_REVIEW.md` (ops)
- **Files:** `backend/app.py` (`/health` ~:819), `backend/docker-compose.yml`.
- **Goal/approach:** Add a readiness endpoint checking DB/Redis/S3/Pinecone reachability; don't
  publish Redis (no password) / MinIO default creds on all interfaces in compose; add app/worker
  services if they're meant to be there.
- **Test:** readiness returns unhealthy when a dependency is down.

---

## Priority 5 — UX / a11y / cleanup

### R24 — Default config (`auth=false` + `byok=true`) advertises BYOK it can't deliver
- **Status:** TODO
- **Severity:** MEDIUM (UX)
- **Files:** `frontend/lib/flags.ts`, `frontend/features/keys/components/settings-screen.tsx` (~:44),
  `frontend/lib/api/http-client.ts` (~:76 `applyAuth`).
- **Symptom:** Picker + free-tier banner show, but Settings dead-ends at "Sign in to add keys" and
  `/login` mints nothing because auth is off — key-saving is impossible in the default config.
- **Goal/approach:** Either auto-require/enable auth when BYOK is on, or hide the picker/banner/
  Settings entry when `auth` is off, so the UI doesn't imply a capability it can't provide.
- **Test:** with `auth=false,byok=true` the key-management surface is hidden (or auth is engaged),
  not a dead CTA.

### R25 — Model picker offers providers with no stored key (→ persistently broken turns)
- **Status:** TODO
- **Severity:** MEDIUM (UX) · `CODE_REVIEW.md`
- **Files:** `frontend/features/keys/components/model-picker.tsx`.
- **Goal/approach:** Disable/hint unowned providers (the M7 spec already describes this) so a user
  can't select a provider they have no key for and lose the free tier.
- **Test:** an unowned provider's models are disabled with an "Add key" affordance.

### R26 — Knowledge-graph panel renders force-graph with no node/edge cap
- **Status:** TODO
- **Severity:** MEDIUM (UX/perf) · `CODE_REVIEW.md`
- **Files:** `frontend/features/knowledge-graph/components/graph-panel.tsx`.
- **Goal/approach:** Cap rendered nodes/edges (top-N by degree/recency) with a "showing N of M"
  note, so a large graph can't freeze the tab.
- **Test:** a large graph renders within the cap.

### R27 — 429 responses use slowapi's `{error}` envelope, not the FE's `{detail,code}`
- **Status:** TODO
- **Severity:** MEDIUM (UX) · `CODE_REVIEW.md`
- **Files:** `backend/app.py` (rate-limit handler), `frontend/lib/api/api-error.ts`.
- **Symptom:** Users see "Backend error: 429" instead of a friendly throttle message.
- **Goal/approach:** Customize the slowapi handler to emit the `{detail,code}` shape the frontend
  parses (e.g. `code: "rate_limited"`).
- **Test:** a 429 surfaces the FE's normalized message, not the raw envelope.

### R28 — a11y: insights drawer isn't a focus-trapped dialog; toggles drop focus
- **Status:** TODO
- **Severity:** LOW-MEDIUM (a11y) · `CODE_REVIEW.md`
- **Files:** `frontend/features/chat/components/chat-screen.tsx` (insights drawer, sidebar toggles).
- **Goal/approach:** Make the insights drawer a proper dialog (focus trap + Escape) and move focus
  when a toggle removes the focused control (WCAG 2.4.3).
- **Test:** keyboard-only: focus is trapped in the open drawer and restored on close.

### R29 — Cleanup: dead legacy helpers, vestigial setting, stale docs
- **Status:** TODO
- **Severity:** LOW (tech-debt)
- **Files:** `backend/components/generation.py` (`synthesize`, `generate_final_response`),
  `backend/components/router.py` (`route_query`) — only tests call them; `backend/config.py`
  (`UPLOADTHING_API_KEY` ~:29, unused); `frontend/GEMINI.md` (empty); `README.md` files (storage
  backend disagreement: S3 vs B2/MinIO).
- **Goal/approach:** Remove the superseded linear-flow helpers + their tests (or document why
  retained); drop the unused setting; delete/fill `GEMINI.md`; reconcile the storage-backend docs.
- **Test:** suite stays green after removals (no live caller breaks).

---

## Deferred — needs your decision (NOT part of the loop)

- **B25 — `beforeunload` cleanup beacon always 401s.** `navigator.sendBeacon` can't send an
  Authorization header. Needs a choice: short-lived token in the beacon body + a body-token auth
  path, or drop the beacon and rely on a server-side session TTL/sweeper. (Carried from `BUGFIX_QUEUE.md`.)
- **R10 (tokens → BFF/httpOnly cookies)** if you don't take the architectural change now.
- **Hosting/CICD migration (Render → AWS)** — infra decision (`frontend/README.md`, `SUGGESTIONS.md`).
- **PII / consent on entity extraction** — full doc text is sent to the operator's Gemini key by
  default; needs a per-user consent/PII-gating policy call (`CODE_REVIEW.md`).

---

## Run log (newest first)

_(each iteration appends one line: `R-ID — <sha> — <one-line outcome>`)_

- R04 — <commit pending> — completed the auth-hardening spine on R03's jti denylist: (1) refresh-token **ROTATION** — `/refresh` consumes (denylists via `revoke_token`) the presented token before minting a fresh pair, so a refresh token is single-use and a replay is rejected (401); the frontend already persists the rotated pair, so no client change. (2) **aud/iss** — tokens carry audience+issuer (`config.JWT_AUDIENCE`/`JWT_ISSUER`, with defaults) and `decode_token` validates them, so a token minted for another aud/iss is rejected. New tests: rotation→reuse-rejected (`test_auth_router`); wrong-aud, wrong-iss, and tokens-carry-aud/iss (`test_auth_security`). Full backend suite green **except** the 5 PRE-EXISTING `test_config` env-validation reds (proven unrelated via stash); ruff+mypy clean. DEFERRED (noted): refresh-time DB re-derivation (a deleted user is already rejected at `get_current_user`) and reuse-detection family revocation — both layer on this jti foundation.
- R03 — f6ca3bb — added `POST /api/auth/logout` (server-side kill-switch) + a Redis `jti` denylist (`auth/revocation.py`): tokens now carry a `jti` (`auth/security.py`); logout revokes the presented refresh token until its `exp`; `/refresh` rejects a revoked jti (401). The denylist READ fails OPEN on a Redis outage (refresh stays available) and the WRITE is best-effort — mirroring `llm.freemium`'s availability stance. Frontend: `authApi.logout` + a best-effort fire-and-forget call in `useAuth.logout` before the local token drop. Backend auth suites green (4 new tests: revoke→401, idempotent-on-garbage, per-jti isolation, fail-open); frontend **290/290** (+`auth.api.test`); ruff+mypy clean, typecheck +0 (3 pre-existing reds unrelated). NOTE: revokes the refresh token only (access tokens are short-lived and expire naturally); refresh-token ROTATION + `jti`-on-every-refresh + `aud`/`iss` remain **R04**.
- R02 — 7dccf9f — built `features/upload/` (M8), gated on `flags.presignedUpload` with the legacy multipart `uploadFile` as the flag-off fallback (today's behavior byte-for-byte, incl. the synthetic `onFileUploaded` message). Flag ON: presign `POST /api/upload` → XHR `PUT` direct-to-S3 (progress) → `POST /api/upload/confirm` → poll `GET /api/documents/{id}` via TanStack Query `refetchInterval`, stop-on-terminal (ready|failed) with one terminal toast; inline `UploadStatus` in the composer. Reconciled to the ACTUAL backend (NOT the M8 doc): confirm body is `{document_id}` only (server derives the key), presign returns `session_id`, backend calls use `auth: flags.auth`. New `upload.schemas`/`upload.api`/`use-upload`/`upload-status` + 2 test files (10 tests). Full frontend suite **289/289**, eslint+prettier clean; typecheck adds 0 errors (3 pre-existing reds in `use-streaming-chat.test.tsx`, proven via stash — unrelated). DEFERRED: the `document-manager` sidebar panel + multi-file/abort UI (single active upload for now).
- R01 — 919a602 — threaded an optional `history` kwarg through `LLMProvider.route/generate/stream` into the VARIABLE user suffix of the routing + generation prompts (reaches every route incl. DIRECT, which ignores `context`). Stable cached system prefix untouched (caching invariant held) and the no-history output is byte-identical, so existing cache/contract tests stayed green. New `test/llm/test_history_prompts.py` + supervisor history assertion; updated 5 provider test fakes. llm/agents/router (135) + graph integration (35) green; ruff+mypy clean. Pre-existing env-only red: `test_chat_provider_di::test_chat_uses_injected_provider` (missing `db_sessionmaker` lifespan state — proven unrelated via stash). NOTE: retrieval-query rewrite (`_rewrite_query` resolving pronouns in the *search* string) intentionally left as a follow-up.
