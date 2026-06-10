# Bug-fix queue — `fix/bug-sweep`

This file is the **single source of truth** for an automated, one-bug-per-commit fix loop.
It is committed to the repo so the process is fully resumable: any fresh/wiped context can
re-read this file and continue from where the last iteration left off.

## Loop protocol (read this every iteration)

1. **Pick**: open this file, find the **first** entry whose status is `TODO`. If none remain,
   the run is complete — stop the loop and report a summary. Do not touch entries marked
   `DONE`, `SKIPPED`, or anything under **Deferred**.
2. **Confirm**: open the cited files and verify the bug still exists in the current code.
   Line numbers here are *approximate hints* (the queue was authored against `main` @ `b014b82`);
   trust the described symptom, not the exact line. If the bug is already fixed/absent, mark the
   entry `SKIPPED (already fixed)` and move to the next — do not invent work.
3. **Fix**: make the smallest change that resolves the described failure. Stay in scope — do not
   refactor unrelated code or fix other queue entries opportunistically.
4. **Test**: add or update a regression test that fails before the fix and passes after. Run the
   relevant suite only (backend: `pytest` for the touched area; frontend: `vitest run <file>`).
   If the suite is red for an unrelated/pre-existing reason, note it but do not let it block a
   verified fix.
5. **Record**: change the entry's status from `TODO` to `DONE` in this file, and append the short
   commit sha once known (a follow-up amend or a note line is fine).
6. **Commit + push**: one focused commit. Message format:
   `fix(<area>): <short title>  [<BUG-ID>]` with a one-line body referencing the queue entry.
   Then `git push origin fix/bug-sweep`.
7. **One bug per iteration.** After pushing, end the iteration (the next iteration re-reads this file).

Branch: `fix/bug-sweep` (off `main`). Never commit to `main`. Backend tests run from `backend/`
(pytest, needs `TEST_DATABASE_URL` for DB-backed tests; pure-logic tests run without it).
Frontend tests run from `frontend/` (`npm run test -- run <path>` / `npx vitest run <path>`).

Status legend: `TODO` · `DONE` · `SKIPPED (<reason>)`.

---

## Priority 1 — new HIGH bugs (data loss / main user path)

### B01 — First turn of every new SSE chat is silently lost (FK violation on uncommitted session row)
- **Status:** DONE
- **Severity:** HIGH
- **Files:** `backend/app.py` (`_resolve_session`, the SSE chat generator, `_persist_turn`), `backend/agents/nodes.py` (`_persist_markdown` / synthesis node), `backend/memory/markdown.py`, `backend/dependencies.py` (`get_db_session`).
- **Symptom:** For a brand-new `session_id` over the SSE transport, `_resolve_session` creates the
  session row with `flush()` only; the request-scoped DB session commits on dependency teardown,
  which (FastAPI ≥0.106 streaming) runs *after* the SSE body finishes. Mid-stream, `_persist_markdown`
  and the generator's `finally`→`_persist_turn` open **fresh** DB sessions and INSERT `session_memory`
  / `messages` rows whose FK points at the still-uncommitted `sessions` row → Postgres
  `foreign_key_violation`, swallowed by `except Exception: logger.error(...)`. Net: turn 1 (user msg,
  assistant msg, memory note) of every new session is never persisted. JSON path is unaffected.
- **Fix approach:** Commit the new session row before streaming begins (mirror the upload path's
  `await db.commit()  # persist before enqueue`), so concurrent fresh sessions can see it. Ensure the
  commit happens for both new-session creation paths. Keep ownership semantics intact.
- **Test:** simulate the new-session SSE path and assert the user+assistant message rows and the
  memory note exist after the stream completes (or a unit test asserting the session is committed
  before the generator starts yielding).

### B02 — Logging in as a different account keeps the old `rag_session_id` → every chat 403s
- **Status:** DONE
- **Severity:** HIGH
- **Files:** `frontend/features/auth/hooks/use-auth-mutation.ts` (`onSuccess`), `frontend/features/chat/api/chat.api.ts` (`getSessionId` / `rag_session_id` localStorage key), `frontend/features/chat/store/chat.store.ts`, `frontend/features/auth/hooks/use-auth.ts`.
- **Symptom:** Login/register `onSuccess` clears React Query but never rotates the persisted
  `rag_session_id` nor resets the chat store. After the tenant-isolation fix, the backend
  (`_resolve_session`) now returns **403** for a session owned by another user. So switching identity
  leaves the old session id in localStorage → every `POST /api/chat` 403s until the user manually
  clears the session, and the previous identity's messages stay on screen.
- **Fix approach:** On successful identity change (login, register, and guest→user upgrade), remove/
  rotate `rag_session_id` and reset the chat store (mirror what logout should do). Centralize an
  "identity changed" cleanup so login and logout share it.
- **Test:** vitest — after a successful login mutation, assert `rag_session_id` is cleared/rotated and
  the chat store is reset.

### B03 — `confirm_upload`: the FAILED status write is rolled back by its own 409 → document stuck `pending`
- **Status:** DONE
- **Severity:** HIGH
- **Files:** `backend/app.py` (`confirm_upload`), `backend/dependencies.py` (`get_db_session`), `backend/database/repository.py` (`set_document_status_by_id`).
- **Symptom:** When the presigned object is missing, the endpoint sets `DocumentStatus.FAILED` then
  raises `HTTPException(409)`. The raise propagates into `get_db_session`'s `except: rollback()`,
  undoing the status UPDATE. Document stays `pending`; pollers never see `failed`.
- **Fix approach:** Commit the FAILED status in its own transaction before raising (or restructure so
  the status write is durable independent of the 409). Confirm no other endpoint has the same
  set-status-then-raise pattern.
- **Test:** call `confirm_upload` with a missing object and assert the document row is `failed` after
  the 409 response.

### B04 — A transient network error during refresh clears tokens and permanently destroys guest identity
- **Status:** DONE
- **Severity:** HIGH
- **Files:** `frontend/lib/api/http-client.ts` (the `catch` around `refreshAccessToken`, the network-error path).
- **Symptom:** Any refresh rejection — including a fetch network error (status 0, wifi reconnecting) —
  runs `authStore.clear()` + `redirectToLogin()`. A guest has no credentials to log back in, so their
  `user_id` + sessions + documents are orphaned forever by a transient blip. Only a definitive 401
  from `/auth/refresh` should clear tokens.
- **Fix approach:** Distinguish a definitive auth failure (HTTP 401/403 from the refresh call) from a
  transient network error. Only clear+redirect on the former; on a network error, surface a retryable
  error and keep the tokens.
- **Test:** vitest — when the refresh fetch throws a network error, assert tokens are NOT cleared and
  no redirect happens; when refresh returns 401, assert tokens ARE cleared.

---

## Priority 2 — contract mismatches (a whole feature silently does nothing)

### B05 — Per-conversation provider/model picker is a server-side no-op
- **Status:** DONE
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (`ChatRequest`), `backend/llm/dependencies.py` (`get_llm_provider`), `frontend/lib/sse/stream-chat.ts`, `frontend/features/chat/api/chat.api.ts`, `frontend/features/keys/store/provider.store.ts`.
- **Symptom:** Frontend sends `provider`/`model` on `/api/chat`, but backend `ChatRequest` declares only
  `message, session_id, web_search_allowed`; pydantic silently drops the extras, and `get_llm_provider`
  resolves provider solely from the stored key / free tier. The picker UI lies.
- **Fix approach:** Accept optional `provider`/`model` on `ChatRequest` and thread them into provider
  resolution (validated against the user's available keys / allowed set; fall back safely if the chosen
  provider has no usable key). Keep free-tier behavior intact. Decide one coherent precedence and
  document it in the code.
- **Test:** assert a request specifying a provider with a stored key uses that provider; an invalid/
  keyless choice falls back rather than 500s.

### B06 — Blocking (non-streaming) JSON chat silently drops all rich components
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (JSON `/api/chat` response builder), `backend/agents/schemas.py`, `frontend/features/chat/hooks/use-blocking-chat.ts`, `frontend/features/chat/api/chat.schemas.ts`.
- **Symptom:** The JSON path strips ```json component fences out of prose but never includes a
  `components` array in the response; the blocking hook never reads/stores components. Flip
  `NEXT_PUBLIC_FEATURE_STREAMING=false` and every table/chart/citation/code/callout vanishes.
- **Fix approach:** Include `components` in the JSON `/api/chat` response (same shape the SSE `done`/
  message uses) and have `use-blocking-chat.ts` store them on the message. Align the zod
  `chatResponseSchema`.
- **Test:** backend asserts the JSON response carries parsed `components`; vitest asserts blocking hook
  stores them on the message.

### B07 — `done` provenance contract mismatch: backend sends `layers: string[]`, frontend reads `sources[].layer`
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` / `backend/sse.py` (the `done` event payload + JSON response), `frontend/features/chat/api/chat.schemas.ts`, `frontend/features/chat/hooks/use-streaming-chat.ts` (`applyDoneSourceLayers`).
- **Symptom:** Backend emits `done.layers` (a `string[]`); the frontend schema strips `layers` and the
  layer-fold reads `done.sources[].layer`, which is never populated → the Phase-7 provenance feature is
  dead end-to-end (badges never render).
- **Fix approach:** Pick ONE contract and align both sides. Simplest: have the backend attach `layer`
  onto each source object (and/or have the frontend consume `layers`), then make the schema and the
  fold agree. Add a cross-stack shape note.
- **Test:** vitest asserts provenance badges render when `done` carries layer info in the chosen shape;
  a backend test pins the `done` payload shape.

### B08 — Backend pydantic component models strip fields the UI needs (citation `url`/`layer`, media `caption`, callout/chart/table `title`/`caption`)
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/agents/schemas.py` (`CitationItem`, `MediaItem`, `CalloutComponent`, `ChartComponent`, `TableComponent`, `validate_component`), `frontend/features/chat/components/rich/*` (citation.tsx, media.tsx, sources-panel.tsx, provenance-badge.tsx).
- **Symptom:** Backend component models declare only a subset of fields; `validate_component` returns
  `model_dump()`, dropping any `url`/`layer`/`caption`/`title` the model emitted. The frontend builds
  real UI on those (clickable citation links, provenance badge, media captions) → they can never render.
- **Fix approach:** Add the missing optional fields to the backend component models so legitimate values
  survive validation. Keep the citation `url` safe (this overlaps with the citation-XSS guard, which is
  out of this queue's scope — here just preserve the field; do NOT weaken any future protocol allowlist).
- **Test:** backend asserts a component with `url`/`caption`/`title`/`layer` round-trips through
  `validate_component` without losing those fields.

### B09 — Blocking JSON `/api/chat` route `BOTH` may be absent from the frontend route enum
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (JSON chat route mapping), `frontend/features/chat/api/chat.schemas.ts` (`routeTypeSchema`).
- **Symptom (verify first):** The graph can emit route `BOTH`; the SSE path maps `BOTH→WEB+RAG` but the
  blocking JSON path may pass it through, and the frontend `routeTypeSchema` may not include `BOTH` →
  `safeParse` rejects an otherwise-successful answer. **Confirm against current `main` — a partial fix
  may already exist.** If already handled both sides, mark SKIPPED.
- **Fix approach:** Map `BOTH→WEB+RAG` in the JSON branch (or add `BOTH` to the schema and map it).
- **Test:** a contract test asserting a `BOTH` graph result parses cleanly on the blocking path.

---

## Priority 3 — backend correctness / resilience

### B10 — `/api/cleanup` returns `"cleaned"` but Pinecone vectors are never deleted (swallowed exception, dead retry, serverless filter)
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/database/db_manager.py` (`_delete_vectors_sync`, `cleanup_session`), `backend/app.py` (`/api/cleanup`).
- **Symptom:** `_delete_vectors_sync` wraps `index.delete(filter=...)` in `try/except: logger.error` INSIDE
  the `@retry`-decorated function, so tenacity never retries and the failure never surfaces; cleanup
  always returns success. On serverless Pinecone, delete-by-metadata-filter is rejected outright, so
  vector deletion always silently no-ops.
- **Fix approach:** Let the delete error propagate to tenacity (remove the inner swallow) and surface a
  real failure to the caller (or a partial-success signal). For serverless, switch to a supported delete
  strategy (delete by id list, or per-user/session namespace). If namespaces are a larger change, at
  minimum stop reporting success when the delete failed.
- **Test:** assert `cleanup_session` raises/reports failure when the underlying delete raises, instead of
  returning success.

### B11 — `/api/auth/refresh` drops the `is_guest` claim → guests silently "become registered" after 15 min
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/auth/router.py` (`refresh`), `backend/auth/security.py` (token minting/claims).
- **Symptom:** `refresh` re-mints tokens via `create_access_token(sub)` without propagating
  `claims["is_guest"]` (defaults to False). After one refresh, a guest's tokens claim registered identity;
  the upgrade CTA disappears and client identity state is wrong.
- **Fix approach:** Carry `is_guest` (and any other identity claims) from the incoming refresh token into
  the newly minted tokens. Prefer re-deriving from the DB user where possible.
- **Test:** assert a refresh of a guest token yields tokens that still carry `is_guest=true`.

### B12 — `PUT /api/keys/{provider}` skips provider validation; unordered `LIMIT 1` key selection can brick chat
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/auth/keys_router.py` (`rotate_key`/`add_key`), `backend/auth/schemas.py` (`KeyIn`), `backend/database/repository.py` (`get_user_llm_key`).
- **Symptom:** `rotate_key` takes `provider` from the path and never validates it (the body's validated
  field is ignored), so `PUT /api/keys/grmini` stores a junk-provider row. `get_user_llm_key` selects
  `LIMIT 1` with no `ORDER BY`, so the junk row can win → `build_provider` raises → every chat 502s. Two
  legit keys also yields a nondeterministic billed provider.
- **Fix approach:** Validate the path `provider` against the allowed set (reuse `KeyIn`'s pattern) and
  reject unknown providers with 422. Give `get_user_llm_key` a deterministic order (e.g. most-recently-
  updated) or select by an explicit provider.
- **Test:** assert `PUT /api/keys/<invalid>` → 422; assert key selection is deterministic.

### B13 — Fernet decryption failure is unhandled → permanent 500 on every chat for BYOK users after a key rotation
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/llm/dependencies.py` (`get_llm_provider`, `decrypt_key`), `backend/auth/crypto.py`.
- **Symptom:** `decrypt_key(row.ciphertext)` raises `InvalidToken` if `LLM_KEY_ENCRYPTION_KEY` was rotated
  or ciphertext is corrupt; nothing catches it (not an `AppException`) → bare 500 for every BYOK request,
  no fallback.
- **Fix approach:** Catch `InvalidToken`, map to a clear `AppException` (e.g. "stored key can't be
  decrypted — re-enter it"), and fall back to the free tier where appropriate instead of 500.
- **Test:** assert a corrupt/undecryptable stored key yields a clean handled error (not a bare 500).

### B14 — Redis outage turns all free-tier chats into bare 500s
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/llm/freemium.py` (`within_free_allowance`), `backend/llm/dependencies.py` (`get_llm_provider`).
- **Symptom:** Raw `INCRBY`/`EXPIRE`/`DECRBY` with no error handling; a Redis `ConnectionError` propagates
  as an unhandled 500 from the dependency. (Also: a crash between `INCRBY` and `EXPIRE` leaves a key with
  no TTL.)
- **Fix approach (decision baked in): fail-open** — if Redis is unreachable, log and allow the request
  rather than 500 (availability over strict quota), and set the TTL atomically (single pipeline/`SET ...
  EX NX` or `INCR` then `EXPIRE` guarded). Map any surfaced error to an `AppException`.
- **Test:** assert that when the Redis client raises, the allowance check fails open (returns allowed) and
  logs, rather than raising.

### B15 — Supervisor route parsing: markdown-wrapped labels collapse to `DIRECT`, skipping web/RAG
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/llm/_prompts.py` (`normalize_decision`), `backend/components/router.py` (`decide_agentic_route`).
- **Symptom:** `normalize_decision` uses `startswith("RAG"/"WEB")` on the raw reply, so `**WEB**`,
  ` ```WEB``` `, `"WEB"`, `Answer: WEB` normalize to `DIRECT` (a *recognized* label), so the defensive
  fallback never fires. Result: web allowed, no docs, model says `**WEB**` → routed DIRECT → no search,
  hallucinated answer.
- **Fix approach:** Normalize by stripping non-alphanumerics / extracting the first known label token
  (case-insensitive) before matching; only fall back to the defensive default when no known label is
  present.
- **Test:** parametrized test asserting `**WEB**`, `` `WEB` ``, `"WEB"`, `Answer: WEB` → WEB (and similar
  for RAG/BOTH/DIRECT).

### B16 — Client `session_id` longer than 64 chars → unhandled 500 (should be 422)
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (`ChatRequest.session_id`, `PresignRequest.session_id`), `backend/database/models.py` (`Session.id` is `String(64)`).
- **Symptom:** `session_id` is an unbounded `str`; a 65+ char value passes validation then trips
  `StringDataRightTruncation` at `flush()` → 500.
- **Fix approach:** Add a `max_length=64` (and a sane min/charset) constraint to the `session_id` fields
  so an over-long id is a 422.
- **Test:** assert an over-long `session_id` → 422, not 500.

### B17 — `_upload_presign` returns 500 for malformed request bodies instead of 422
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (the presign branch that does `PresignRequest.model_validate(await request.json())` under a blanket `except Exception`).
- **Symptom:** Invalid JSON / failing field raises `JSONDecodeError`/`ValidationError`, caught by the
  endpoint's blanket `except Exception` → `AppException(500, "Upload failed unexpectedly.")`. Clients get
  a misleading server error for a request defect.
- **Fix approach:** Validate the presign body via a proper FastAPI request model / explicit
  `ValidationError`→422 handling, outside the catch-all. Let request defects surface as 422.
- **Test:** assert a malformed presign body → 422.

### B18 — JSON chat path mislabels every infrastructure failure as a free-tier limit
- **Status:** TODO
- **Severity:** MEDIUM (UX/observability correctness)
- **Files:** `backend/app.py` (the `except Exception` around `graph.ainvoke` on the JSON path).
- **Symptom:** Non-`AppException` failures (DB/Pinecone outage, code bugs) are turned into
  `AppException(500, "free tier Limit Reached ...")`, even for paid BYOK users. The genuine exhaustion
  path is a separate 402.
- **Fix approach:** Replace the misleading message with a generic internal-error envelope (preserve the
  real exhaustion 402 path). Log the underlying exception; don't claim a quota cause.
- **Test:** assert a simulated infra exception on the JSON path yields a generic 500 envelope, not the
  free-tier message.

### B19 — SSE disconnect: `await` inside the generator's `finally` defeats persistence-on-disconnect
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/app.py` (the SSE generator's `finally` / `_persist_turn`, the `is_disconnected` check).
- **Symptom:** On a real mid-stream disconnect Starlette raises `CancelledError` at the current await; the
  `finally` then awaits again (`is_disconnected`/`_persist_turn`), re-raising `CancelledError` (a
  `BaseException`, not caught by `except Exception`), so persistence never completes — a disconnect after
  the full answer but before `done` loses the whole turn. Also: when the disconnect check fires it
  `break`s and still yields `done` to a gone client.
- **Fix approach:** Do post-stream persistence in a way that survives cancellation — e.g. shield the
  persistence await (`asyncio.shield`) or perform the durable write outside the cancellable generator
  scope; guard against yielding `done` to a disconnected client.
- **Test:** simulate a disconnect after tokens streamed and assert the turn is persisted (or a unit test
  of the cancellation-safe persistence helper).

### B20 — SSE `status` events describe work that already finished ("synthesizing" arrives after the last token)
- **Status:** TODO
- **Severity:** MEDIUM (UX correctness)
- **Files:** `backend/app.py` (the `stream_mode="updates"` loop, `_node_stage`).
- **Symptom:** `updates` emits a node's update *after* it completes, so "routing"/"retrieving"/
  "synthesizing" each arrive after that phase is done — "synthesizing" lands right before `done`, after
  every token. The progress indicator is always one phase behind.
- **Fix approach:** Emit each stage on node *entry* (e.g. derive the next stage from the previous node's
  completion, or use a LangGraph mechanism that signals node start), so the displayed stage matches the
  work in flight.
- **Test:** assert the "synthesizing" status is emitted before the first token event in the SSE sequence.

### B21 — Markdown-memory first-append race drops notes; KG Redis lock TTL is fixed and never renewed
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `backend/memory/markdown.py` (`append`), `backend/memory/graph.py` (`KnowledgeGraph._lock`, `add_entities`).
- **Symptom (a):** `append` does `SELECT ... FOR UPDATE` then INSERT-if-absent; `FOR UPDATE` locks nothing
  when the row doesn't exist, so two concurrent first turns both INSERT → duplicate-PK `IntegrityError`,
  one note silently dropped. **(b):** the KG lock uses a fixed `timeout=15` never extended; a slow
  load→merge→dump→commit exceeding 15s lets a second writer acquire the "lock" and overwrite (lost
  triples); the release failure is swallowed.
- **Fix approach (a):** use an upsert (`ON CONFLICT (...) DO UPDATE`) like `get_or_create_session`.
  **(b):** make the critical section robust — longer/renewed lock or an atomic compare-and-set on save,
  and surface lock-release failures. (a) and (b) may be split into two commits if cleaner; if so, add a
  `B21a`/`B21b` note here and keep one TODO until both land.
- **Test:** (a) concurrent first-appends don't lose a note (upsert path); (b) a unit assertion on the
  lock/save atomicity where feasible.

---

## Priority 4 — frontend correctness / UX

### B22 — Streamed turns show a perpetual "Thinking…" spinner — status steps are never completed
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `frontend/features/chat/hooks/use-streaming-chat.ts` (status step pushes), `frontend/features/chat/components/thinking-steps.tsx`.
- **Symptom:** Every status stage is pushed as `{ state: "active" }`; neither `onDone` nor `finalize`
  flips them to complete, so `thinking-steps` shows a spinning "Thinking…" forever after a successful
  answer. (The blocking hook pushes a `complete` step — divergence.)
- **Fix approach:** On `done`/finalize, mark all pending steps complete (and append a terminal
  "done"/complete step to match the blocking hook).
- **Test:** vitest — after a completed stream, assert no step remains in `active` state.

### B23 — SSE parser: a CRLF split across network chunks fabricates a frame boundary and drops events
- **Status:** TODO
- **Severity:** MEDIUM
- **Files:** `frontend/lib/sse/parser.ts` (the `buffer += value.replace(/\r\n?/g, "\n")` normalization), `frontend/lib/sse/stream-chat.ts`.
- **Symptom:** Per-chunk `\r\n?`→`\n` normalization mishandles a `\r` at the end of chunk N followed by
  `\n` at the start of chunk N+1: the lone `\r`→`\n` plus the surviving `\n` make a spurious `\n\n`
  terminator mid-frame, so a CRLF-emitting server/proxy can split a frame and silently drop a token/`done`.
- **Fix approach:** Normalize on the buffered text (carry a "previous chunk ended with `\r`" flag, or
  append raw and handle `\r\n`/`\r` when splitting frames) so a CR/LF straddling a chunk boundary is one
  newline.
- **Test:** vitest — feed an event whose CRLF is split across two `push`es and assert the event parses
  intact.

### B24 — Auto-scroll hijack: the view is yanked to the bottom on every streamed token
- **Status:** TODO
- **Severity:** MEDIUM (UX)
- **Files:** `frontend/features/chat/components/chat-screen.tsx` (the `useEffect([messages, isLoading])` calling `scrollIntoView`).
- **Symptom:** `appendContent` replaces the `messages` array identity per token, so the effect runs a
  smooth `scrollIntoView` on every token — the user can't scroll up to read earlier messages while an
  answer streams.
- **Fix approach:** Only auto-scroll when the user is already near the bottom (track scroll position /
  a "stick to bottom" flag); don't force-scroll when the user has scrolled up.
- **Test:** vitest (or a focused logic test) — when the user is scrolled away from the bottom, a new
  token does not trigger a scroll-to-bottom.

### B26 — `done.answer` overwrites streamed content even when empty
- **Status:** TODO
- **Severity:** LOW-MEDIUM
- **Files:** `frontend/features/chat/hooks/use-streaming-chat.ts` (`onDone` building the finalize patch).
- **Symptom:** `finalize` always writes `content: answer`, so if the backend's `done.answer` is empty/
  missing, the already-streamed content is wiped to empty.
- **Fix approach:** Only overwrite `content` from `done.answer` when it's non-empty; otherwise keep the
  streamed accumulation.
- **Test:** vitest — a `done` with empty `answer` keeps the streamed content.

---

## Deferred — needs your decision (NOT part of the automated loop)

These are real but either need a design/policy call or are infra/non-code-local; the loop must skip them.

- **B25 — `beforeunload` cleanup beacon always 401s.** `navigator.sendBeacon` can't send an Authorization
  header, but `/api/cleanup` requires one. Fix needs a choice: send a short-lived token in the beacon
  body and add a body-token auth path, or drop the beacon and rely on a server-side session TTL/sweeper.
- **H-B1 — conversation history never reaches the LLM.** `_rewrite_query` ignores its `history` arg.
  Real correctness gap, but the fix is a prompt-design decision (how to fold history into routing/
  synthesis prompts) you should weigh in on.
- **Auth hardening (design):** refresh-token rotation + `jti`/denylist + logout endpoint; auth-endpoint
  rate limiting policy.
- **Security headers / CSP**, **non-root Dockerfiles + `.dockerignore`**, **CI `vitest` step**,
  **tokens-in-localStorage → BFF/httpOnly**, **LLM client timeouts/retry + Gemini sync-iterator offload**,
  **citation `javascript:` URL XSS guard**, **open-redirect `?next` validation.** (Several are small;
  they were excluded by the chosen "correctness only" scope — say the word to fold them in.)

---

## Run log (newest first)

_(each iteration appends one line: `BUG-ID — <sha> — <one-line outcome>`)_

- B05 — commit pending — backend now reads the picker's provider/model off the chat body (resolve_provider helper); honored for a BYOK user holding that provider's key (model→synth), falls through (never 500) otherwise; frontend already sent them. test_get_llm_provider 9/9, chat/repo/async 34/34.
- B04 — e6c43ca — clear tokens only on a definitive 401/403 refresh rejection; transient network/5xx errors propagate as retryable with tokens intact (no guest orphaning); new regression test, http-client 10/10.
- B03 — 54c1e1c — commit the FAILED status before raising the 409 so get_db_session's rollback can't erase it; regression asserts mark→commit ordering (test_upload: 9/9).
- B02 — c30cdfa — shared resetIdentityState() rotates rag_session_id + wipes chat store on login/register (cache:clear) and logout, gated off the in-place upgrade (cache:invalidate) so it keeps its session; new regression test + 28 auth/chat tests green.
- B01 — b1032cd — commit resolved session row before SSE stream opens so first-turn message/markdown writes no longer FK-violate an uncommitted parent; regression test asserts request-db commit precedes fresh-session persistence (test_chat_sse: 7/7). Pre-existing unrelated reds: 5 test_config env-validation tests.
