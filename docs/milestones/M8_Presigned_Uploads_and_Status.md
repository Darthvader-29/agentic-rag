# M8 — Presigned Uploads + Ingestion Status (Backend Phase P5)

Replace today's fire-and-forget multipart upload with a **presigned-PUT, direct-to-S3** upload flow that shows real per-file progress, kicks off **queue-based ingestion**, and **polls ingestion status** (via TanStack Query `refetchInterval`) until the document is `ready` or `failed`. A `document-manager` UI lists the session's (later: the user's) documents with live status badges. The whole forward-compatible flow is **flag-gated**; with the flag OFF the app behaves **exactly** as it does today (multipart FormData to `/upload`, fire-and-forget).

**Status:** backend-dependent / **depends on** (M1 feature-folder architecture + `http-client` + TanStack Query; M6 auth for user-scoped docs + `Authorization: Bearer`) / **unlocks** (robust large-file ingestion UX: no API passthrough, visible progress, durable status). **Flag `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD` defaults OFF.**

---

## 1. Objective & Scope

### In scope

- **Presigned PUT flow** — request a presigned S3 URL from the backend (`POST /api/upload`), receive `{ document_id, upload_url, s3_key }`.
- **Direct-to-S3 upload with progress** — `PUT` the raw file bytes straight to S3/MinIO at `upload_url`, surfacing **upload percentage** via `XMLHttpRequest.upload.onprogress` (the API process never touches the bytes).
- **Ingestion task kickoff** — confirm the object landed (`POST /api/upload/confirm` with `{ document_id, s3_key }`), which `head_object`-verifies and enqueues the Celery ingestion task.
- **Status polling** — poll the document's ingestion status with TanStack Query `refetchInterval`, **stopping on a terminal status** (`ready` / `failed`).
- **`document-manager` UI** — a list of the session's (M6: user's) documents with live status badges, surfaced in the sidebar.
- **Flag gating + multipart fallback** — `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD`; OFF → today's multipart `/upload` FormData path, fire-and-forget, no polling, no document-manager.

### Out of scope

- Chat streaming / SSE (`NEXT_PUBLIC_FEATURE_STREAMING`, M2/M9).
- Provider keys / BYOK / model picker (M7).
- Resumable / multipart-chunked S3 uploads, pause/resume, client-side retry of S3 PUT (noted as future work in §9).
- Server-side document deletion UI beyond what `/cleanup` already provides (cleanup wiring is untouched here).
- Chat-path freemium rate-limiting / the `free_tier_exhausted` error (backend Phase 5 rate limiting + `09_Phase6` §3 provider ladder) — handled in **M7/M9**, not here. The upload routes have their own per-route rate limit (backend Phase 5 Appendix C); a `429` on `/api/upload` surfaces as an ordinary upload error, with no BYOK/freemium UX in this milestone.

---

## 2. Backend Upload / Ingestion Contract

> **Citations:** all backend behavior below is sourced from
> `Python-Agentic-RAG-Backend/docs/06_Phase5_Redis_Scaling.md` (Phase 5: presigned uploads + queue ingestion) and
> `Python-Agentic-RAG-Backend/docs/03_Phase2_PostgreSQL_and_State_Migration.md` (the `documents` table + `DocumentStatus` enum that status polling reads).
> **All routes are under the `/api` prefix** (matches `NEXT_PUBLIC_API_URL`, which already includes `/api` — see `services/api.ts:6`).

### 2.0 Plan-vs-backend reconciliation (READ THIS FIRST)

The frontend improvement plan (M8 line) describes the target as _"presigned PUT to S3 + `/upload/status/{task_id}` polling"_ with status enum _`pending|processing|done|failed`_. The **actual backend Phase 5 doc does not implement a `/upload/status/{task_id}` endpoint and does not return a `task_id` to the client.** Reconciling the two:

| Plan wording                                    | Actual backend (P5/P2 docs)                                                                                                                                                                                                                      | Decision for this milestone                                                                                                                                                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /upload` → presigned URL                  | `POST /api/upload` body `{ filename, content_type? }` → `{ document_id, upload_url, s3_key }` (`06...md:463-473`)                                                                                                                                | Use the real two-field request + 3-field response.                                                                                                                                                                      |
| "ingest-start call returning `task_id`"         | `POST /api/upload/confirm` body `{ document_id, s3_key }` → `{ document_id, status: "queued" }`. Celery's task id is **server-internal**, never returned (`06...md:475-490`).                                                                    | The client's polling key is **`document_id`**, not a `task_id`.                                                                                                                                                         |
| `GET /upload/status/{task_id}`                  | **No such route.** Status lives on the `documents` row and is read via `GET /api/documents/{document_id}` (referenced at `06...md:63`, `:517`, `:634`).                                                                                          | Poll `GET /api/documents/{document_id}`.                                                                                                                                                                                |
| status enum `pending\|processing\|done\|failed` | `DocumentStatus = pending \| processing \| ready \| failed` (`03...md:263,717,728`). Note `06...md:392` uses `"complete"` in the Celery task body — an **inconsistency in the backend docs**; the enum of record (Phase 2 migration) is `ready`. | Treat **`ready`** as the success terminal. The Zod schema **accepts both `ready` and `complete`** and normalizes to `ready`, so we are robust to whichever the deployed backend emits. `done` is a UI-layer alias only. |

**Assumptions (explicitly flagged — backend doc is silent):**

- **(A1)** `GET /api/documents/{id}` returns at least `{ id, filename, status, s3_key, session_id }`. The doc references this poll route (`06...md:517`) but never prints its response body. We **assume** the shape below and Zod-parse defensively (`.passthrough()` not used; unknown keys ignored by object parse).
- **(A2)** Listing a session's documents (`GET /api/documents?session_id=...`) exists for the `document-manager`. The doc implies per-session document tracking (`session_has_documents`, `list_s3_keys_for_session`, `03...md:36`) but does not print a list route. We design `document-manager` to render whatever the store already holds (uploads performed this session) and to **optionally** hydrate from a list endpoint if present; if the list 404s the manager simply shows session-local uploads. This keeps M8 shippable without the list route.
- **(A3)** The presigned URL is a plain **PUT** URL (not presigned-POST form fields). Confirmed: `06...md:62` — _"Presigned **POST** adds form-field surface we do not need."_ So the client does a single `PUT upload_url` with the file as the raw body and `Content-Type` matching what was presigned.
- **(A4)** Auth: after M6, `/api/upload` and `/api/upload/confirm` require `Authorization: Bearer <token>` (`06...md:81`). The S3 `PUT` is **unauthenticated** (the signature is in the URL). Our `http-client` already attaches the bearer for backend calls; the S3 PUT must go through a **raw XHR that does NOT attach the bearer** (sending `Authorization` to S3 can break the signature — see §9).

### 2.1 Today (flag OFF) — multipart passthrough

```
POST /api/upload                         Content-Type: multipart/form-data
  form fields: file=<binary>, session_id=<uuid>
→ 200 { "status": "processing", "s3_key": "uploads/<...>" }     // 03...md:544
```

This is exactly what `services/api.ts uploadFile` does today (FormData, fire-and-forget). **Preserved verbatim** as the fallback.

### 2.2 Presigned flow (flag ON)

**Step 1 — request presigned URL** (`06...md:463-473`)

```http
POST /api/upload
Authorization: Bearer <token>        # M6
Content-Type: application/json

{ "filename": "report.pdf", "content_type": "application/pdf" }
```

```jsonc
// 200
{
  "document_id": "doc_01HZ...",
  "upload_url": "https://bucket.s3.amazonaws.com/uploads/u1/uuid_report.pdf?X-Amz-Signature=...",
  "s3_key": "uploads/u1/uuid_report.pdf",
}
```

> Server side: `key = uploads/{user.id}/{uuid}_{filename}`; `repo.create_document(status="pending")`; presign expires in `900s` (`06...md:439,469-473`).

**Step 2 — PUT bytes directly to S3** (no API involvement, `06...md:625`)

```http
PUT <upload_url>
Content-Type: application/pdf        # MUST match the content_type used at presign time (§9)

<raw file bytes>
```

```
→ 200 / 204 (empty body) on success. Progress observed via XHR upload events.
```

**Step 3 — confirm + enqueue ingestion** (`06...md:475-490`)

```http
POST /api/upload/confirm
Authorization: Bearer <token>
Content-Type: application/json

{ "document_id": "doc_01HZ...", "s3_key": "uploads/u1/uuid_report.pdf" }
```

```jsonc
// 200  -> ingestion enqueued (Celery)
{ "document_id": "doc_01HZ...", "status": "queued" }

// 409  -> object never landed in S3 (head_object failed); doc set to "failed"
{ "detail": "object not uploaded" }

// 404  -> document not found / not owned by caller
{ "detail": "document not found" }
```

**Step 4 — poll ingestion status** (assumption A1; route ref `06...md:517,634`)

```http
GET /api/documents/doc_01HZ...
Authorization: Bearer <token>
```

```jsonc
// 200
{
  "id": "doc_01HZ...",
  "filename": "report.pdf",
  "status": "processing", // pending | processing | ready | failed   (03...md:717)
  "s3_key": "uploads/u1/uuid_report.pdf",
  "session_id": "sess_...",
  "error": null, // assumed; may be absent. Present on failed.
}
```

**Status lifecycle** (`03...md:728`): `pending → processing → ready | failed`. Terminal states: `ready` (success), `failed` (error). After `confirm` returns `queued`, the document is still `pending`/`processing` until the worker finishes; `ready`/`failed` is durable in Postgres and readable from any instance.

---

## 3. Decisions & Rationale

| Decision                                                                    | Rationale                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Direct-to-S3 presigned PUT** (not multipart-through-API) when flag ON     | Offloads file bytes and bandwidth/memory from the API process (`06...md:39-40,62,625`); supports large files; the API only issues a URL and later verifies + enqueues. Multipart-through-API streams the whole file through FastAPI and is what we are replacing.                                                                                                                 |
| **Single PUT, not presigned-POST**                                          | Backend chose presigned **PUT** (`06...md:62`): no form-field surface. Client does one `PUT` with the raw body.                                                                                                                                                                                                                                                                   |
| **`XMLHttpRequest` for the S3 PUT** (not `fetch`)                           | `fetch()` has **no upload-progress** API (no `ReadableStream` request-progress in browsers today; `ReadableStream` request bodies are not broadly supported and don't give byte counts). `XMLHttpRequest.upload.onprogress` gives `loaded/total` for a real progress bar. We isolate XHR to exactly one function (`putToS3`) and keep everything else on the typed `http-client`. |
| **Two-step confirm (presign → PUT → confirm)**                              | Mirrors the backend's race-closing design (`06...md:62,116-119`): the client must finish the PUT before the server `head_object`-verifies and enqueues. The client orchestrates all three steps in order.                                                                                                                                                                         |
| **Poll `GET /api/documents/{id}` keyed on `document_id`** (not a `task_id`) | The backend never returns the Celery task id (§2.0); status of record lives on the `documents` row (`06...md:63`, _"Ingestion status in Postgres… any instance can poll `/api/documents/{id}`"_).                                                                                                                                                                                 |
| **TanStack Query `refetchInterval` with stop-on-terminal**                  | Query's `refetchInterval: (query) => isTerminal ? false : intervalMs` is the idiomatic self-terminating poll; it auto-pauses on tab blur (`refetchIntervalInBackground: false`) and auto-cancels on unmount, preventing the "polling never stops" leak (§9). Linear `2s` interval (backoff capped) — ingestion is seconds-to-minutes, not sub-second.                             |
| **Accept both `ready` and `complete`** in the status schema                 | The backend docs disagree (`ready` in P2 enum vs `complete` in P5 task body). Normalizing both to `ready` makes the client correct against whichever the deployed server emits, with zero risk.                                                                                                                                                                                   |
| **`document-manager` backed by Query + a Zustand `upload.store`**           | Query owns server truth (per-doc status polls); Zustand owns the **in-flight** client phases (requesting/uploading%/ingesting) that have no server representation yet (the S3 PUT % is purely client-side). The manager renders the union.                                                                                                                                        |
| **Flag-gated fallback so flag-off == today**                                | `use-upload` reads `flags.presignedUpload`; OFF → `multipartUpload` (the literal port of today's `uploadFile`) + a single success toast, **no** store entry, **no** polling, **no** document-manager. Proven in §7.                                                                                                                                                               |

---

## 4. Current-State Snapshot

- **`services/api.ts` → `uploadFile(file)`** (`services/api.ts:80-95`): builds `FormData` with `file` + `session_id`, `POST`s multipart to `${API_BASE_URL}/upload`, throws on `!res.ok`, returns `res.json()` typed `Promise<any>`. **No progress, no polling, fire-and-forget.** `API_BASE_URL` already includes `/api` (`:6`).
- **`components/chat/chat-input.tsx` → `handleFileUpload`** (`:36-51`): on `<input type="file" accept=".pdf,.docx,.txt">` change, sets `isUploading`, `await api.uploadFile(file)`, then `toast.success(\`${file.name} uploaded\`)`and`onFileUploaded?.(file.name)`; `toast.error("Upload failed")`on throw; clears the input. The paperclip button shows a`Loader2`spinner while`isUploading`.
- **`app/page.tsx` → `onFileUploaded`** callback: injects a synthetic chat message noting the file was uploaded (the "fire-and-forget toast/message injected today"). This is the UX M8 replaces (flag ON) with real progress + status.
- **Architecture (post-M1):** `lib/api/http-client.ts` (typed `request<T>(path,{method,body,schema,auth,signal})`, prepends `env.NEXT_PUBLIC_API_URL`, Zod-parses, throws `ApiError`); `lib/flags.ts` (Zod env flags); TanStack Query provider mounted in `app/providers.tsx`; feature folders under `features/`. `features/upload` does not exist yet — created here.

---

## 5. Target File Tree (delta)

```
features/upload/
  api/
    upload.api.ts            # getPresignedUrl, putToS3 (XHR+progress), confirmIngestion,
                             #   getDocumentStatus, listDocuments?, multipartUpload (legacy)
    upload.schemas.ts        # Zod: PresignRequest/Response, ConfirmRequest/Response,
                             #   DocumentStatus enum, DocumentRecord, normalized helpers
  store/
    upload.store.ts          # Zustand: active uploads (id, filename, phase, progress, documentId, error)
  hooks/
    use-upload.ts            # orchestrates presign->PUT(progress)->confirm  OR  multipart (flag)
    use-upload-status.ts     # TanStack Query poll /api/documents/{id}, stop-on-terminal, toasts
  components/
    upload-button.tsx        # file picker trigger (drag/drop optional); calls use-upload
    upload-progress.tsx      # per-file: S3 PUT % bar -> ingestion spinner/status
    document-manager.tsx     # list of session/user documents w/ live status
    document-row.tsx         # one document row (filename + status badge + progress)
    ingestion-status-badge.tsx  # status -> colored badge (pending/processing/ready/failed)

# edits to existing files
features/chat/components/chat-input.tsx   # replace fire-and-forget handleFileUpload with <UploadButton/>
components/layout/app-sidebar.tsx         # mount <DocumentManager/> (flag ON)
lib/flags.ts                              # add presignedUpload flag (reads NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD)
lib/env.ts                                # add NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD to Zod env schema
types/index.ts                            # re-export z.infer<DocumentRecord/DocumentStatus> (optional)
test/msw/handlers.ts                      # add presign + S3 PUT + confirm + status handlers
```

> **Note on M1 paths:** the M1 plan keeps `chat-input.tsx` under `components/chat/` today and migrates chat into `features/chat/components/`. Wire the `<UploadButton/>` wherever `chat-input.tsx` lives in your branch; both locations are listed for safety.

---

## 6. Tasks (ordered)

> Each task: **goal · files · full copy-pasteable code.** TS strict; no `any`. Assumes M1's `http-client`, `env`, `flags`, and the Query provider exist.

### Task 6.1 — `upload.schemas.ts` (Zod contracts)

**Goal:** runtime-validated request/response shapes for every step, plus the status enum normalized across the backend-doc inconsistency.
**Files:** `features/upload/api/upload.schemas.ts`

```ts
// features/upload/api/upload.schemas.ts
import { z } from "zod";

/** Backend DocumentStatus enum of record (Phase 2 migration, 03...md:717,728):
 *  pending -> processing -> ready | failed.
 *  Phase 5 task body (06...md:392) uses "complete"; we accept it and normalize to "ready". */
export const RawDocumentStatusSchema = z.enum([
  "pending",
  "processing",
  "ready",
  "complete", // backend-doc inconsistency; normalized below
  "failed",
]);
export type RawDocumentStatus = z.infer<typeof RawDocumentStatusSchema>;

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export function normalizeStatus(raw: RawDocumentStatus): DocumentStatus {
  return raw === "complete" ? "ready" : raw;
}

export const TERMINAL_STATUSES: ReadonlySet<DocumentStatus> = new Set([
  "ready",
  "failed",
]);
export const isTerminalStatus = (s: DocumentStatus): boolean =>
  TERMINAL_STATUSES.has(s);

/** Step 1 request/response — POST /api/upload (06...md:463-473) */
export const PresignRequestSchema = z.object({
  filename: z.string().min(1),
  content_type: z.string().min(1).optional(),
});
export type PresignRequest = z.infer<typeof PresignRequestSchema>;

export const PresignResponseSchema = z.object({
  document_id: z.string().min(1),
  upload_url: z.string().url(),
  s3_key: z.string().min(1),
});
export type PresignResponse = z.infer<typeof PresignResponseSchema>;

/** Step 3 request/response — POST /api/upload/confirm (06...md:475-490) */
export const ConfirmRequestSchema = z.object({
  document_id: z.string().min(1),
  s3_key: z.string().min(1),
});
export type ConfirmRequest = z.infer<typeof ConfirmRequestSchema>;

export const ConfirmResponseSchema = z.object({
  document_id: z.string().min(1),
  status: z.string(), // "queued" per 06...md:490; not load-bearing client-side
});
export type ConfirmResponse = z.infer<typeof ConfirmResponseSchema>;

/** Step 4 — GET /api/documents/{id}  (assumption A1; route ref 06...md:517,634).
 *  Unknown keys are ignored by object parse; `error` is optional/nullable. */
export const DocumentRecordSchema = z.object({
  id: z.string().min(1),
  filename: z.string().min(1),
  status: RawDocumentStatusSchema,
  s3_key: z.string().optional(),
  session_id: z.string().optional(),
  error: z.string().nullish(),
});
export type DocumentRecordRaw = z.infer<typeof DocumentRecordSchema>;

/** Normalized record the UI consumes. */
export interface DocumentRecord {
  id: string;
  filename: string;
  status: DocumentStatus;
  error: string | null;
}
export function toDocumentRecord(raw: DocumentRecordRaw): DocumentRecord {
  return {
    id: raw.id,
    filename: raw.filename,
    status: normalizeStatus(raw.status),
    error: raw.error ?? null,
  };
}

/** Optional list endpoint (assumption A2). Tolerates absence (caller 404-guards). */
export const DocumentListResponseSchema = z.object({
  documents: z.array(DocumentRecordSchema),
});
export type DocumentListResponse = z.infer<typeof DocumentListResponseSchema>;

/** Legacy multipart response (flag OFF) — 03...md:544. */
export const MultipartUploadResponseSchema = z.object({
  status: z.string(),
  s3_key: z.string().optional(),
});
export type MultipartUploadResponse = z.infer<
  typeof MultipartUploadResponseSchema
>;
```

### Task 6.2 — `upload.api.ts` (network layer; XHR for S3 PUT)

**Goal:** one function per backend step; the S3 PUT uses `XMLHttpRequest` for progress; legacy multipart preserved.
**Files:** `features/upload/api/upload.api.ts`

```ts
// features/upload/api/upload.api.ts
import { httpClient } from "@/lib/api/http-client";
import { api as legacyApi } from "@/services/api"; // for getSessionId() only
import {
  ConfirmRequestSchema,
  ConfirmResponseSchema,
  DocumentRecordSchema,
  MultipartUploadResponseSchema,
  PresignRequestSchema,
  PresignResponseSchema,
  toDocumentRecord,
  type ConfirmResponse,
  type DocumentRecord,
  type PresignResponse,
} from "./upload.schemas";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  "https://python-agentic-rag-backend.onrender.com/api";

/** Step 1: ask the backend for a presigned PUT URL. Auth attached by http-client. */
export async function getPresignedUrl(
  filename: string,
  contentType: string | undefined,
  signal?: AbortSignal
): Promise<PresignResponse> {
  const body = PresignRequestSchema.parse({
    filename,
    content_type: contentType,
  });
  return httpClient.request<PresignResponse>("/upload", {
    method: "POST",
    body,
    schema: PresignResponseSchema,
    auth: true,
    signal,
  });
}

export interface PutToS3Options {
  onProgress?: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

/**
 * Step 2: PUT raw bytes DIRECTLY to S3 with upload-progress.
 * Uses XMLHttpRequest because fetch() has no upload-progress API (§3, §9).
 * IMPORTANT: do NOT send an Authorization header to S3 — the signature is in the URL (A4, §9).
 * Content-Type MUST match the content_type used at presign time (§9).
 */
export function putToS3(
  uploadUrl: string,
  file: File,
  opts: PutToS3Options = {}
): Promise<void> {
  const { onProgress, signal } = opts;
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", uploadUrl, true);
    // Match the presigned content-type; falls back to a generic type if the browser gave none.
    xhr.setRequestHeader(
      "Content-Type",
      file.type || "application/octet-stream"
    );

    xhr.upload.onprogress = (e: ProgressEvent) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`S3 PUT failed: ${xhr.status} ${xhr.statusText}`));
    };
    xhr.onerror = () =>
      reject(new Error("S3 PUT network error (check CORS — §9)"));
    xhr.onabort = () =>
      reject(new DOMException("S3 PUT aborted", "AbortError"));

    if (signal) {
      if (signal.aborted) {
        xhr.abort();
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(file);
  });
}

/** Step 3: confirm the object landed; backend head_object-verifies and enqueues ingestion. */
export async function confirmIngestion(
  documentId: string,
  s3Key: string,
  signal?: AbortSignal
): Promise<ConfirmResponse> {
  const body = ConfirmRequestSchema.parse({
    document_id: documentId,
    s3_key: s3Key,
  });
  return httpClient.request<ConfirmResponse>("/upload/confirm", {
    method: "POST",
    body,
    schema: ConfirmResponseSchema,
    auth: true,
    signal,
  });
}

/** Step 4: read current ingestion status for a document. Normalized for the UI. */
export async function getDocumentStatus(
  documentId: string,
  signal?: AbortSignal
): Promise<DocumentRecord> {
  const raw = await httpClient.request(
    "/documents/" + encodeURIComponent(documentId),
    {
      method: "GET",
      schema: DocumentRecordSchema,
      auth: true,
      signal,
    }
  );
  return toDocumentRecord(raw);
}

/** Legacy fallback (flag OFF) — literal port of services/api.ts uploadFile (no progress). */
export async function multipartUpload(file: File): Promise<void> {
  const sessionId = legacyApi.getSessionId();
  const form = new FormData();
  form.append("file", file);
  form.append("session_id", sessionId);

  const res = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  // Validate but do not require fields — fire-and-forget, matches today.
  try {
    MultipartUploadResponseSchema.parse(await res.json());
  } catch {
    /* tolerate shape drift — today's path ignores the body too */
  }
}
```

### Task 6.3 — `upload.store.ts` (Zustand: in-flight client phases)

**Goal:** track active uploads that have **no** server representation yet (the S3 PUT %), plus the `documentId` once known so polling can attach.
**Files:** `features/upload/store/upload.store.ts`

```ts
// features/upload/store/upload.store.ts
import { create } from "zustand";
import type { DocumentStatus } from "../api/upload.schemas";

export type UploadPhase =
  | "requesting" // asking backend for a presigned URL
  | "uploading" // PUTting to S3 (progress 0..100)
  | "ingesting" // confirmed; worker is processing; polling status
  | "ready"
  | "failed";

export interface ActiveUpload {
  id: string; // client-generated transient id (crypto.randomUUID)
  filename: string;
  phase: UploadPhase;
  progress: number; // 0..100, meaningful during "uploading"
  documentId: string | null; // set after presign; the polling key
  status: DocumentStatus | null; // last server status seen during "ingesting"
  error: string | null;
}

interface UploadState {
  uploads: Record<string, ActiveUpload>;
  start: (id: string, filename: string) => void;
  setPhase: (id: string, phase: UploadPhase) => void;
  setProgress: (id: string, progress: number) => void;
  setDocumentId: (id: string, documentId: string) => void;
  setStatus: (id: string, status: DocumentStatus) => void;
  fail: (id: string, error: string) => void;
  remove: (id: string) => void;
}

export const useUploadStore = create<UploadState>((set) => ({
  uploads: {},
  start: (id, filename) =>
    set((s) => ({
      uploads: {
        ...s.uploads,
        [id]: {
          id,
          filename,
          phase: "requesting",
          progress: 0,
          documentId: null,
          status: null,
          error: null,
        },
      },
    })),
  setPhase: (id, phase) =>
    set((s) =>
      s.uploads[id]
        ? { uploads: { ...s.uploads, [id]: { ...s.uploads[id], phase } } }
        : s
    ),
  setProgress: (id, progress) =>
    set((s) =>
      s.uploads[id]
        ? {
            uploads: {
              ...s.uploads,
              [id]: { ...s.uploads[id], progress, phase: "uploading" },
            },
          }
        : s
    ),
  setDocumentId: (id, documentId) =>
    set((s) =>
      s.uploads[id]
        ? { uploads: { ...s.uploads, [id]: { ...s.uploads[id], documentId } } }
        : s
    ),
  setStatus: (id, status) =>
    set((s) => {
      const u = s.uploads[id];
      if (!u) return s;
      const phase: UploadPhase =
        status === "ready"
          ? "ready"
          : status === "failed"
            ? "failed"
            : "ingesting";
      return { uploads: { ...s.uploads, [id]: { ...u, status, phase } } };
    }),
  fail: (id, error) =>
    set((s) =>
      s.uploads[id]
        ? {
            uploads: {
              ...s.uploads,
              [id]: { ...s.uploads[id], phase: "failed", error },
            },
          }
        : s
    ),
  remove: (id) =>
    set((s) => {
      const next = { ...s.uploads };
      delete next[id];
      return { uploads: next };
    }),
}));
```

### Task 6.4 — `use-upload.ts` (orchestration + flag branch)

**Goal:** flag ON → presign → PUT(progress) → confirm, writing phases into the store; flag OFF → `multipartUpload` + a single toast (today's behavior, **no** store entry).
**Files:** `features/upload/hooks/use-upload.ts`

```ts
// features/upload/hooks/use-upload.ts
"use client";

import { useCallback, useRef } from "react";
import { toast } from "sonner";
import { flags } from "@/lib/flags";
import {
  confirmIngestion,
  getPresignedUrl,
  multipartUpload,
  putToS3,
} from "../api/upload.api";
import { useUploadStore } from "../store/upload.store";

export interface UseUploadResult {
  upload: (file: File) => Promise<void>;
  /** Abort the in-flight S3 PUT for a given client upload id (best-effort). */
  abort: (id: string) => void;
  presignedEnabled: boolean;
}

export function useUpload(): UseUploadResult {
  const presignedEnabled = flags.presignedUpload;
  const start = useUploadStore((s) => s.start);
  const setPhase = useUploadStore((s) => s.setPhase);
  const setProgress = useUploadStore((s) => s.setProgress);
  const setDocumentId = useUploadStore((s) => s.setDocumentId);
  const fail = useUploadStore((s) => s.fail);

  // Per-upload AbortControllers for the S3 PUT.
  const controllers = useRef<Map<string, AbortController>>(new Map());

  const abort = useCallback((id: string) => {
    controllers.current.get(id)?.abort();
  }, []);

  const upload = useCallback(
    async (file: File): Promise<void> => {
      // ---- Flag OFF: today's behavior exactly. ----
      if (!presignedEnabled) {
        try {
          await multipartUpload(file);
          toast.success(`${file.name} uploaded`);
        } catch {
          toast.error("Upload failed");
        }
        return;
      }

      // ---- Flag ON: presign -> PUT(progress) -> confirm. ----
      const id = crypto.randomUUID();
      const controller = new AbortController();
      controllers.current.set(id, controller);
      start(id, file.name);

      try {
        // Step 1
        const presign = await getPresignedUrl(
          file.name,
          file.type || undefined,
          controller.signal
        );
        setDocumentId(id, presign.document_id);

        // Step 2 (progress)
        setPhase(id, "uploading");
        await putToS3(presign.upload_url, file, {
          signal: controller.signal,
          onProgress: (loaded, total) =>
            setProgress(id, Math.round((loaded / total) * 100)),
        });

        // Step 3
        setPhase(id, "ingesting");
        await confirmIngestion(
          presign.document_id,
          presign.s3_key,
          controller.signal
        );
        // Polling now begins in use-upload-status (keyed by document_id). Terminal toast fires there.
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          fail(id, "Upload cancelled");
          toast.message(`${file.name} cancelled`);
        } else {
          const msg = err instanceof Error ? err.message : "Upload failed";
          fail(id, msg);
          toast.error(`${file.name}: ${msg}`);
        }
      } finally {
        controllers.current.delete(id);
      }
    },
    [presignedEnabled, start, setPhase, setProgress, setDocumentId, fail]
  );

  return { upload, abort, presignedEnabled };
}
```

### Task 6.5 — `use-upload-status.ts` (Query polling, stop-on-terminal)

**Goal:** poll `GET /api/documents/{id}` with `refetchInterval` that returns `false` on terminal status; sync into the store; toast once on `ready`/`failed`.
**Files:** `features/upload/hooks/use-upload-status.ts`

```ts
// features/upload/hooks/use-upload-status.ts
"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { getDocumentStatus } from "../api/upload.api";
import { isTerminalStatus, type DocumentRecord } from "../api/upload.schemas";
import { useUploadStore } from "../store/upload.store";

const POLL_INTERVAL_MS = 2000;

/**
 * Polls one document's ingestion status until terminal.
 * - `refetchInterval` returns false once status is ready|failed -> polling stops.
 * - `refetchIntervalInBackground: false` pauses polling on hidden tabs.
 * - Query auto-cancels on unmount (no leaked timers — §9).
 * Pass `clientUploadId` to mirror status back into the in-flight store entry.
 */
export function useUploadStatus(
  documentId: string | null,
  clientUploadId?: string
) {
  const setStatus = useUploadStore((s) => s.setStatus);
  const toasted = useRef(false);

  const query = useQuery<DocumentRecord>({
    queryKey: ["document-status", documentId],
    queryFn: ({ signal }) => getDocumentStatus(documentId as string, signal),
    enabled: !!documentId,
    refetchInterval: (q) => {
      const data = q.state.data;
      if (data && isTerminalStatus(data.status)) return false; // STOP on terminal
      return POLL_INTERVAL_MS;
    },
    refetchIntervalInBackground: false,
    staleTime: 0,
  });

  // Sync server status -> store + one-shot terminal toast.
  useEffect(() => {
    const data = query.data;
    if (!data) return;
    if (clientUploadId) setStatus(clientUploadId, data.status);
    if (!toasted.current && isTerminalStatus(data.status)) {
      toasted.current = true;
      if (data.status === "ready") toast.success(`${data.filename} ready`);
      else
        toast.error(
          `${data.filename} failed to ingest${data.error ? `: ${data.error}` : ""}`
        );
    }
  }, [query.data, clientUploadId, setStatus]);

  return query;
}
```

### Task 6.6 — `upload-progress.tsx` (per-file progress → ingestion status)

**Goal:** a `<Progress/>` bar driven by the S3 PUT %, then an ingestion spinner/badge once confirmed. Activates polling via `useUploadStatus`.
**Files:** `features/upload/components/upload-progress.tsx`

```tsx
// features/upload/components/upload-progress.tsx
"use client";

import { Loader2, X } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { useUploadStatus } from "../hooks/use-upload-status";
import type { ActiveUpload } from "../store/upload.store";
import { IngestionStatusBadge } from "./ingestion-status-badge";

interface UploadProgressProps {
  upload: ActiveUpload;
  onCancel?: (id: string) => void;
}

export function UploadProgress({ upload, onCancel }: UploadProgressProps) {
  // Poll only once we have a documentId and aren't terminal yet.
  const isPolling =
    !!upload.documentId &&
    (upload.phase === "ingesting" || upload.phase === "uploading");
  useUploadStatus(isPolling ? upload.documentId : null, upload.id);

  return (
    <div className="border-border bg-card flex flex-col gap-1 rounded-md border p-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">{upload.filename}</span>
        {(upload.phase === "requesting" || upload.phase === "uploading") &&
          onCancel && (
            <Button
              variant="ghost"
              size="icon"
              className="text-muted-foreground h-6 w-6"
              onClick={() => onCancel(upload.id)}
              title="Cancel upload"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
      </div>

      {upload.phase === "uploading" && (
        <div className="flex items-center gap-2">
          <Progress value={upload.progress} className="h-1.5 flex-1" />
          <span className="text-muted-foreground w-9 text-right text-xs">
            {upload.progress}%
          </span>
        </div>
      )}

      {upload.phase === "requesting" && (
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <Loader2 className="h-3 w-3 animate-spin" /> preparing upload…
        </span>
      )}

      {(upload.phase === "ingesting" ||
        upload.phase === "ready" ||
        upload.phase === "failed") && (
        <div className="flex items-center gap-2 text-xs">
          {upload.phase === "ingesting" && (
            <Loader2 className="text-muted-foreground h-3 w-3 animate-spin" />
          )}
          <IngestionStatusBadge status={upload.status ?? "processing"} />
          {upload.error && (
            <span className="text-destructive truncate">{upload.error}</span>
          )}
        </div>
      )}
    </div>
  );
}
```

### Task 6.7 — `ingestion-status-badge.tsx`, `document-row.tsx`, `document-manager.tsx`

**Goal:** the document list with live status badges. The manager renders in-flight uploads (from the store) and, where available, server-side documents (assumption A2).
**Files:** three components below.

```tsx
// features/upload/components/ingestion-status-badge.tsx
"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DocumentStatus } from "../api/upload.schemas";

const LABEL: Record<DocumentStatus, string> = {
  pending: "Pending",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

const CLASS: Record<DocumentStatus, string> = {
  pending: "bg-muted text-muted-foreground",
  processing: "bg-primary/10 text-primary",
  ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-destructive/15 text-destructive",
};

export function IngestionStatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent text-xs font-medium", CLASS[status])}
    >
      {LABEL[status]}
    </Badge>
  );
}
```

```tsx
// features/upload/components/document-row.tsx
"use client";

import { FileText } from "lucide-react";
import type { DocumentStatus } from "../api/upload.schemas";
import { IngestionStatusBadge } from "./ingestion-status-badge";

export interface DocumentRowModel {
  id: string;
  filename: string;
  status: DocumentStatus;
}

export function DocumentRow({ doc }: { doc: DocumentRowModel }) {
  return (
    <div className="hover:bg-accent/50 flex items-center justify-between gap-2 rounded-md px-2 py-1.5">
      <div className="flex min-w-0 items-center gap-2">
        <FileText className="text-muted-foreground h-4 w-4 shrink-0" />
        <span className="truncate text-sm">{doc.filename}</span>
      </div>
      <IngestionStatusBadge status={doc.status} />
    </div>
  );
}
```

```tsx
// features/upload/components/document-manager.tsx
"use client";

import { useMemo } from "react";
import { flags } from "@/lib/flags";
import { useUploadStore } from "../store/upload.store";
import { UploadProgress } from "./upload-progress";
import { DocumentRow, type DocumentRowModel } from "./document-row";
import type { DocumentStatus } from "../api/upload.schemas";

/**
 * Lists the session's documents with live status.
 * - In-flight uploads (store) render as <UploadProgress/> (progress + polling).
 * - Settled uploads (ready/failed) render as compact <DocumentRow/>.
 * (A2) A server list endpoint can hydrate additional rows later; absent it, the
 * manager shows session-local uploads only — still fully functional.
 */
export function DocumentManager() {
  if (!flags.presignedUpload) return null; // flag OFF: no document-manager (today)
  return <DocumentManagerInner />;
}

function DocumentManagerInner() {
  const uploads = useUploadStore((s) => s.uploads);
  const list = useMemo(() => Object.values(uploads), [uploads]);

  if (list.length === 0) {
    return (
      <p className="text-muted-foreground px-2 py-3 text-xs">
        No documents uploaded this session.
      </p>
    );
  }

  const active = list.filter(
    (u) => u.phase !== "ready" && u.phase !== "failed"
  );
  const settled: DocumentRowModel[] = list
    .filter((u) => u.phase === "ready" || u.phase === "failed")
    .map((u) => ({
      id: u.id,
      filename: u.filename,
      status: u.status ?? (u.phase as DocumentStatus),
    }));

  return (
    <div className="flex flex-col gap-2 p-2">
      <h3 className="text-muted-foreground px-2 text-xs font-semibold tracking-wide uppercase">
        Documents
      </h3>
      {active.map((u) => (
        <UploadProgress key={u.id} upload={u} />
      ))}
      {settled.map((d) => (
        <DocumentRow key={d.id} doc={d} />
      ))}
    </div>
  );
}
```

### Task 6.8 — `upload-button.tsx` + wire into `chat-input.tsx`

**Goal:** replace the fire-and-forget `handleFileUpload` with `<UploadButton/>` that calls `useUpload`. Flag OFF → identical toast + (optional) `onFileUploaded`; flag ON → store-driven progress UI takes over (no synthetic chat message).
**Files:** `features/upload/components/upload-button.tsx`, edit `chat-input.tsx`.

```tsx
// features/upload/components/upload-button.tsx
"use client";

import { useRef, useState } from "react";
import { Loader2, Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUpload } from "../hooks/use-upload";

interface UploadButtonProps {
  disabled?: boolean;
  /** Flag-OFF parity hook: notify parent so today's synthetic chat message still works. */
  onLegacyUploaded?: (fileName: string) => void;
  accept?: string;
}

export function UploadButton({
  disabled,
  onLegacyUploaded,
  accept = ".pdf,.docx,.txt",
}: UploadButtonProps) {
  const { upload, presignedEnabled } = useUpload();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const onChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await upload(file);
      // Flag OFF preserves today's parent notification (synthetic message). Flag ON: store UI owns it.
      if (!presignedEnabled) onLegacyUploaded?.(file.name);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        type="file"
        ref={inputRef}
        className="hidden"
        onChange={onChange}
        accept={accept}
      />
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-foreground h-8 w-8 rounded-full"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || busy}
        title="Upload document"
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Paperclip className="h-4 w-4" />
        )}
      </Button>
    </>
  );
}
```

**Edit `chat-input.tsx`** — remove the inline file input + `handleFileUpload` + `api.uploadFile` import and drop in `<UploadButton/>`:

```tsx
// chat-input.tsx (delta)
import { UploadButton } from "@/features/upload/components/upload-button";
// remove: import { api } from "@/services/api";  and  the handleFileUpload + fileInputRef + isUploading state

// inside the left-buttons cluster, replace the <input type="file"> + paperclip <Button> with:
<UploadButton disabled={isLoading} onLegacyUploaded={onFileUploaded} />;
```

> `onFileUploaded` (today's `app/page.tsx` callback that injects the synthetic "file uploaded" chat message) is passed straight through as `onLegacyUploaded`, so with the flag OFF the chat-message behavior is byte-for-byte today's. With the flag ON, `UploadButton` does **not** call it; the `document-manager` + `upload-progress` own the UX.

### Task 6.9 — Flag + env wiring

**Goal:** add the flag to the Zod env + `flags` object.
**Files:** `lib/env.ts`, `lib/flags.ts`, `.env.example`.

```ts
// lib/env.ts (delta) — within the z.object passed to the env schema
NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true"),
```

```ts
// lib/flags.ts (delta)
export const flags = {
  // ...existing flags (streaming, auth, byok)...
  presignedUpload: env.NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD,
} as const;
```

```dotenv
# .env.example (delta)
# M8 / backend P5 — presigned S3 uploads + ingestion-status polling. OFF = today's multipart upload.
NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD=false
```

> Mount `<DocumentManager/>` inside `components/layout/app-sidebar.tsx` (it self-gates on the flag and returns `null` when OFF, so mounting unconditionally is safe).

---

## 7. Feature-Flag Behavior Matrix

| Surface                                 | `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD=false` (default)                      | `=true`                                                                 |
| --------------------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Upload transport                        | `multipartUpload(file)` → `POST /api/upload` FormData (`file`,`session_id`) | presign → `PUT` direct-to-S3 → confirm                                  |
| API touches bytes?                      | Yes (passthrough, today)                                                    | No (direct-to-S3)                                                       |
| Progress UI                             | None (paperclip spinner only, today)                                        | Real S3 PUT % bar via XHR                                               |
| Ingestion kickoff                       | Implicit server-side (today)                                                | Explicit `POST /api/upload/confirm` → Celery enqueue                    |
| Status polling                          | None                                                                        | `GET /api/documents/{id}` via Query `refetchInterval`, stop-on-terminal |
| Toasts                                  | `success("{file} uploaded")` / `error("Upload failed")` (today)             | progress UI + terminal toast `"{file} ready"` / `"{file} failed…"`      |
| `onFileUploaded` synthetic chat message | Fired (today)                                                               | Not fired (manager owns UX)                                             |
| `document-manager` in sidebar           | Not rendered (`return null`)                                                | Rendered with live rows                                                 |
| `upload.store` entries                  | None created                                                                | One per upload (phase/progress/status)                                  |
| Auth header on backend calls            | as today                                                                    | bearer attached by `http-client` (M6)                                   |

**Proof that flag-off == today:** the only OFF-path code is `multipartUpload` (a literal port of `services/api.ts:80-95` — same endpoint, same fields, same `!res.ok` throw, fire-and-forget) plus the same two toasts and the same `onFileUploaded` callback. No store entry, no Query, no polling, no `document-manager` (it returns `null`). UX and network are identical to the current build.

---

## 8. Testing & Verification

### MSW handlers (`test/msw/handlers.ts`)

```ts
import { http, HttpResponse } from "msw";

const API = process.env.NEXT_PUBLIC_API_URL ?? "https://example.test/api";
const S3 = "https://s3.example.test";

// Scripted ingestion lifecycle per document id: pending -> processing -> ready.
const statusScript = new Map<string, ("pending" | "processing" | "ready")[]>();

export const uploadHandlers = [
  // Step 1 — presign
  http.post(`${API}/upload`, async ({ request }) => {
    const ct = request.headers.get("content-type") ?? "";
    if (ct.includes("multipart/form-data")) {
      // Flag-OFF legacy path
      return HttpResponse.json({
        status: "processing",
        s3_key: "uploads/legacy.pdf",
      });
    }
    const docId = "doc_test_1";
    statusScript.set(docId, ["pending", "processing", "ready"]);
    return HttpResponse.json({
      document_id: docId,
      upload_url: `${S3}/put/${docId}`,
      s3_key: `uploads/u/${docId}.pdf`,
    });
  }),

  // Step 2 — S3 PUT (mocked; jsdom XHR works with MSW)
  http.put(`${S3}/put/:id`, () => new HttpResponse(null, { status: 200 })),

  // Step 3 — confirm
  http.post(`${API}/upload/confirm`, async ({ request }) => {
    const body = (await request.json()) as { document_id: string };
    return HttpResponse.json({
      document_id: body.document_id,
      status: "queued",
    });
  }),

  // Step 4 — status poll, advancing the script each call
  http.get(`${API}/documents/:id`, ({ params }) => {
    const id = params.id as string;
    const q = statusScript.get(id) ?? ["ready"];
    const status = q.length > 1 ? q.shift()! : q[0];
    return HttpResponse.json({
      id,
      filename: "report.pdf",
      status,
      s3_key: `uploads/${id}`,
      session_id: "s1",
    });
  }),
];
```

### Unit tests

- **`use-upload` (flag ON):** mock the three api functions; assert order presign → putToS3(progress) → confirm; assert `upload.store` transitions `requesting → uploading(progress) → ingesting`; assert `documentId` set after presign.
- **`use-upload` (flag OFF):** with flag stubbed false, assert `multipartUpload` called, `getPresignedUrl`/`putToS3`/`confirmIngestion` **not** called, success toast fired, **no** store entry created. (Flag-off parity gate.)
- **`use-upload-status`:** with the scripted MSW status handler, render the hook; assert it polls and that `refetchInterval` returns `false` once `ready` (poll count stops increasing); assert store `status`/`phase` becomes `ready`; assert exactly **one** terminal toast.
- **`putToS3` progress:** drive a fake XHR (or MSW + jsdom) and assert `onProgress` produces a monotonic `loaded/total`; assert `signal.abort()` rejects with `AbortError`.
- **`upload.schemas`:** `normalizeStatus("complete") === "ready"`; `isTerminalStatus("ready"|"failed") === true`, `("processing") === false`.

### Component tests

- **`upload-progress`:** phase `uploading` renders a `<Progress value=…/>` + `%`; phase `ingesting` renders spinner + `IngestionStatusBadge`; phase `failed` renders error text.
- **`ingestion-status-badge`:** each status renders its label + class.
- **`document-manager`:** flag OFF → renders `null`; flag ON + store entries → active uploads render `<UploadProgress/>`, settled render `<DocumentRow/>`; empty → "No documents…".

### Manual

- **Flag ON against MSW (or MinIO):** pick a file → progress bar climbs → badge goes Processing → Ready; `document-manager` row appears; cancel mid-upload aborts.
- **Flag OFF parity:** pick a file → single "uploaded" toast, synthetic chat message, no progress UI, no manager — identical to current build.

### Gates

`npm run lint`, `prettier --check`, `tsc --noEmit`, `vitest run`, `next build` all pass.

---

## 9. Risks & Gotchas

1. **`fetch` has no upload-progress.** It cannot report request-body bytes sent. **Resolution:** `putToS3` uses `XMLHttpRequest` (`xhr.upload.onprogress`), isolated to that one function; all other calls use the typed `http-client`.
2. **S3 CORS for browser PUT.** A direct browser `PUT` requires the S3/MinIO bucket CORS to allow `PUT` from the app origin and to expose the needed headers; otherwise the PUT fails with an opaque network error. **Resolution:** `putToS3`'s `onerror` message points at CORS; document the required bucket CORS (`AllowedMethods: [PUT]`, `AllowedOrigins: [<app origin>]`, `AllowedHeaders: [Content-Type]`) as a backend/infra prerequisite for flag-on.
3. **Presigned URL expiry (900s).** A slow/large upload can outlive the signature → `403` from S3. **Resolution:** surface the failure and let the user retry (which re-presigns). Resumable uploads are out of scope.
4. **Polling that never stops.** A naive interval leaks timers across unmounts/terminal states. **Resolution:** Query `refetchInterval` returns `false` on terminal status, `refetchIntervalInBackground: false` pauses on hidden tabs, and Query auto-cancels on unmount.
5. **Large files / memory.** `xhr.send(file)` streams the `File` (a disk-backed blob) — no full in-memory read. **Resolution:** never `await file.arrayBuffer()`; pass the `File` directly.
6. **Content-Type must match the presign.** If the server presigned a specific `Content-Type`, the `PUT` must send the same value or S3 rejects the signature. **Resolution:** send `file.type` (the same value passed to `getPresignedUrl` as `content_type`); fall back to `application/octet-stream` only when the browser gives none, and keep server presign permissive accordingly.
7. **Auth header to S3 breaks the signature.** **Resolution:** `putToS3` is a raw XHR that sets **only** `Content-Type` — it does **not** go through `http-client` and never attaches `Authorization`.
8. **Confirm/ingest race.** Confirming before the PUT finishes → backend `head_object` fails → `409`. **Resolution:** the orchestration `await`s `putToS3` to resolve before calling `confirmIngestion`.
9. **`task_id` vs `document_id` mismatch (plan vs backend).** The backend never returns a Celery task id; status is keyed by `document_id`. **Resolution:** documented in §2.0; the polling key is `document_id`.
10. **Status enum drift (`ready` vs `complete`).** Backend docs disagree. **Resolution:** schema accepts both, normalizes to `ready`.
11. **User-scoped isolation (M6).** Documents are per-user once auth lands; an anonymous build (no M6) cannot fetch a user-owned doc. **Resolution:** flag-on assumes M6 bearer auth; without M6, run flag-on against a mock only.
12. **Failed ingestion UX.** A `failed` status must be visible, not silently stuck. **Resolution:** terminal toast + `failed` badge + optional `error` string; polling stops.
13. **Aborting an in-flight upload.** **Resolution:** per-upload `AbortController` wired into `putToS3` (and the presign/confirm requests); `abort(id)` cancels; rejection is handled as `AbortError`.
14. **Retry / resumability out of scope.** No chunked/multipart S3, no pause/resume — noted as future work.

---

## 10. Exit Criteria (checkable)

1. **Flag OFF == today:** upload uses multipart `POST /api/upload` FormData, fire-and-forget, same toasts + synthetic chat message; **no** progress UI, **no** polling, `document-manager` renders `null`. Parity unit test green.
2. **Flag ON happy path:** presign → S3 `PUT` (with visible %) → confirm → poll `GET /api/documents/{id}` until `ready`; terminal toast fires once; `document-manager` shows the row.
3. **Polling stops on terminal:** `refetchInterval` returns `false` at `ready`/`failed`; no timers leak after unmount (verified in test).
4. **No API passthrough on flag-on path:** bytes go to S3 via XHR; backend calls carry only JSON.
5. **Schema robustness:** `complete` and `ready` both resolve to `ready`; status zod-parse tolerates unknown keys / nullable `error`.
6. **Abort works:** cancelling mid-upload aborts the PUT and marks the entry `failed`/cancelled.
7. **Gates green:** `lint`, `prettier --check`, `tsc --noEmit`, `vitest run`, `next build` all pass; MSW handlers script `pending → processing → ready`.
8. **All other milestones unaffected:** chat/cleanup/reset/theme behave as before regardless of flag.

---

## 11. Commit Plan

Conventional commits, milestone-sized, each leaving the tree releasable and gates green:

1. `feat(upload): add Zod upload/ingestion schemas (presign/confirm/status, normalize ready)`
2. `feat(upload): upload.api with XHR direct-to-S3 PUT + presign/confirm/status + legacy multipart`
3. `feat(upload): zustand upload.store tracking in-flight phases/progress`
4. `feat(upload): use-upload orchestration (flag-gated presign->PUT->confirm | multipart)`
5. `feat(upload): use-upload-status Query polling with stop-on-terminal + terminal toast`
6. `feat(upload): upload-progress + ingestion-status-badge + document-row + document-manager`
7. `feat(upload): UploadButton; wire into chat-input replacing fire-and-forget upload`
8. `feat(flags): add NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD (default false) + env + sidebar mount`
9. `test(upload): MSW presign/PUT/confirm/status + unit/component tests (both flag states)`

> Branch: `claude/frontend-improvements-planning-1aX4u`. Flag ships **OFF**; flip to `true` only against a P5-complete backend with S3 CORS configured.
