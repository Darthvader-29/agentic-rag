# M1 — Architecture Refactor (Behavioral Parity)

This milestone moves all chat logic out of `app/page.tsx` into feature-based modules
(`features/chat/{api,store,hooks,components}`), introduces TanStack Query + Zustand, a typed
`http-client` with Zod-validated responses, and a `useChat` facade — **without changing a single
observable behavior**. The blocking `/chat` flow stays blocking; send / upload / cleanup / reset /
web-search toggle behave byte-for-byte as they do today. We deliberately build the *streaming-shaped*
`Message` type and store now so M2 can flip streaming on with zero UI churn.

**Status:** Not started · **Depends on:** M0 (env/flags/providers: `lib/env.ts`, `lib/flags.ts`,
`app/providers.tsx`, `ThemeProvider`/`Toaster` mounted in `app/layout.tsx`) · **Unlocks:** M2
(streaming-ready core: `useStreamingChat`, `lib/sse/*`), M3 (chat UX polish: thinking-steps,
sources-panel, message-actions).

---

## 1. Objective & Scope

### In scope

- Add runtime deps `@tanstack/react-query` and `zustand`.
- Create the API layer: `lib/api/api-error.ts` (typed `ApiError`) and `lib/api/http-client.ts`
  (generic `request<T>(path, opts)` that prepends `env.NEXT_PUBLIC_API_URL`, JSON/`FormData`-aware,
  Zod-parses the response, maps failures to `ApiError`, carries a dormant auth-interceptor seam).
- Create `lib/query-client.ts` and wire `QueryClientProvider` into the **existing** `app/providers.tsx`
  (created in M0).
- Create the chat feature module:
  - `features/chat/api/chat.schemas.ts` — Zod schemas matching today's deployed backend contract.
  - `features/chat/api/chat.api.ts` — `sendMessage` / `uploadFile` / `cleanupSession` / session-id
    helpers ported verbatim (semantics-preserving) from `services/api.ts`.
  - `features/chat/store/chat.store.ts` — Zustand store owning `messages`, `draft`,
    `webSearchAllowed`, `isLoading`, with streaming-shaped actions.
  - `features/chat/hooks/use-blocking-chat.ts` — TanStack `useMutation` that writes a unified
    `Message` into the store on success.
  - `features/chat/hooks/use-chat.ts` — the stable facade (`{ messages, isStreaming, sendMessage,
    stop, retry }`) reading `flags.streaming`; delegates to blocking today.
  - `features/chat/components/chat-screen.tsx` — the entire JSX/layout extracted from `page.tsx`
    (sidebar, scroll area, message list, input, auto-scroll effect, `beforeunload` cleanup beacon).
- Gut `app/page.tsx` to a thin shell that renders `<ChatScreen />`.
- Rewrite `types/index.ts` as `z.infer` re-exports and extend `Message` with `steps` / `sources` /
  `status` / `components` (the last an **opaque forward-compat** carrier for the backend Phase-6
  `component` SSE event — empty on the blocking path, rendered by M10).
- Delete the dead `components/chat/chat-interface.tsx` (proven unused below).
- Unit tests: store actions and `useBlockingChat`.

### Out of scope (do NOT touch in M1)

- **No streaming.** `lib/sse/*`, `useStreamingChat`, and the `flags.streaming === true` branch are
  M2. `useChat` scaffolds the seam but only the blocking strategy is wired.
- **No visual / class changes.** `chat-message.tsx`, `chat-input.tsx`, `sidebar.tsx`,
  `empty-state.tsx`, `message-loading.tsx` keep their current markup and hardcoded `slate/blue/white`
  classes — semantic-token migration is **M3**. We only change *where state lives and how data flows*,
  never how it looks.
- **No new API endpoints / contract changes.** We match the *currently deployed* backend exactly
  (JSON `/cleanup`, multipart `/upload`), not the Phase-2 `Form(...)` variant (see §5.f gotcha).
- **No auth.** The interceptor seam in `http-client` is present but dormant (`auth: false` default);
  activation is M6.
- **No `services/api.ts` deletion.** It is *replaced* in usage (nothing imports it after this
  milestone), but per "delete dead code only when proven dead" we leave the file untouched unless the
  reviewer asks; all *new* code imports from `features/chat/api`. (We DO delete `chat-interface.tsx`,
  which is genuinely empty and unimported.)

**The contract of this milestone: a `git stash` of the diff and a re-run of the app must be
indistinguishable to a user.** Same requests on the wire, same messages on screen, same toasts.

---

## 2. Decisions & Rationale

| Decision | Rationale | Rejected alternative |
|---|---|---|
| **TanStack Query** for discrete async resources (`/chat` mutation, upload, cleanup) | First-class mutation lifecycle (`isPending`/`isError`/`onSuccess`), retry/cancellation, and a single place to grow sessions/document-status polling (M8) and auth (M6). The plan's "API layer" section names it explicitly. | **SWR** — excellent for `GET` caching but its mutation story (`useSWRMutation`) is thinner, and the roadmap's polling/refetch-interval needs (M8) and request cancellation map more cleanly onto Query. |
| **Zustand** owns live chat (`messages[]`, in-flight buffer, per-message `steps[]`/`sources`/`status`, `draft`, `webSearchAllowed`) | Streaming (M2) appends tokens at high frequency (`appendContent` per chunk) and pushes `status` steps. Routing that through the Query cache would mean `setQueryData` churn + structural-sharing comparisons on every token — wrong tool. A plain external store with targeted selectors re-renders only the subscribed message. The blocking mutation writes into this **same** store with the **same** shape, so when streaming flips on the UI is unchanged. | **React Query cache as the message store** — high-frequency mutation of cached data is an anti-pattern; **`useState` in `page.tsx`** (today) — prop-drills, can't be shared by future strategies, and dies on every route change. |
| **`http-client` wrapper** over raw `fetch` | One choke point for: base-URL prepend (`env.NEXT_PUBLIC_API_URL`), Zod response parsing → typed data, uniform `ApiError` mapping (so callers `catch` one shape), and the dormant `Bearer`/`401-refresh` seam for M6. Today's `services/api.ts` repeats `fetch` + ad-hoc `res.ok` checks + manual `detail` extraction in every method. | **Per-call raw `fetch`** — duplicated error handling, no runtime validation, `any` leakage (`uploadFile: Promise<any>` today, `services/api.ts:80`). |
| **Unified `Message` shape now** (`steps`/`sources`/`status`/`components`) even though M1 is blocking-only | The plan mandates it: the blocking path synthesizes one `done` step + `context_count` sources, so the M3 thinking-steps/sources panels render today and the M2 streaming path writes the identical shape. We also carry an **opaque `components` field now** (empty on the blocking path) so the backend Phase-6 `component` event — the agentic rich-output upgrade — lands as a **flag-flip + renderer (M10)**, not another `Message` refactor. Adding any of these fields later would force a second `page.tsx`-scale refactor. | **Minimal `Message` now, extend in M2/M10** — guarantees a churn wave through the store, hooks, and `chat-message.tsx` exactly when streaming or rich components land; defeats the "architect once" principle. |
| **`z.infer` re-exports** in `types/index.ts` | Runtime validation (Zod) and compile-time types stay locked to one source of truth; impossible for the TS type and the parsed shape to drift. | Hand-written `interface`s parallel to schemas — two sources of truth that silently diverge. |

---

## 3. Current-State Snapshot

### `app/page.tsx` — home of ALL chat logic today (169 lines)

| Concern | Location | Behavior to preserve exactly |
|---|---|---|
| `messages` state | `app/page.tsx:23` | `useState<Message[]>([])` |
| `isLoading` state | `app/page.tsx:24` | toggles the `<MessageLoading />` row + disables input |
| `isSidebarOpen` state | `app/page.tsx:25` | width/opacity transition of the sidebar shell |
| `scrollRef` | `app/page.tsx:26` | sentinel `<div ref={scrollRef} />` at list end (`:149`) |
| **Auto-scroll effect** | `app/page.tsx:29-33` | `scrollRef.current?.scrollIntoView({ behavior: "smooth" })` on `[messages, isLoading]` |
| **`beforeunload` cleanup beacon** | `app/page.tsx:36-54` | on tab close, if `api.getSessionId()` truthy, `navigator.sendBeacon(\`${API_BASE_URL}/cleanup\`, Blob<JSON {session_id, file_keys: []}>)` |
| **`handleSendMessage(text, webSearch)`** | `app/page.tsx:56-96` | push user `Message` (`uuidv4`, `role:"user"`, `timestamp: new Date()`); `setIsLoading(true)`; `await api.sendMessage`; push assistant `Message` with `content: response.answer`, `route: response.route`, `sourcesCount: response.context_count`; on error push assistant `Message` with `route: "ERROR"` and `content = err?.message ?? fallback`; `finally setIsLoading(false)` |
| **`handleClearSession()`** | `app/page.tsx:98-102` | `await api.clearSession()`; `setMessages([])`; `toast.success("Chat history cleared")` |
| **File-upload callback** | `app/page.tsx:156-164` | on `onFileUploaded(fileName)`, push assistant `Message` with `content: \`📄 "${fileName}" uploaded and queued for ingestion.\`` |
| Local module const | `app/page.tsx:19-20` | `API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api"` (note: **differs** from the `services/api.ts:5-6` default of the Render URL — preserve both call sites' current behavior; new code uses `env.NEXT_PUBLIC_API_URL` from M0) |
| Layout JSX | `app/page.tsx:104-168` | sidebar shell + main column + scroll area + `<EmptyState/>` / list / `<MessageLoading/>` + `<ChatInput/>` |

### `services/api.ts` — methods to port (96 lines)

- `getSessionId(): string` — `services/api.ts:9-17`. SSR guard (`typeof window === "undefined"` → `""`);
  reads/creates `localStorage["rag_session_id"]` (`uuidv4()` if absent).
- `clearSession(): Promise<void>` — `services/api.ts:19-38`. `POST ${API_BASE_URL}/cleanup` JSON
  `{session_id, file_keys: []}` (errors swallowed with `console.error`); then `removeItem` +
  immediately `setItem` a **fresh** `uuidv4()` (so the next message starts a new session).
- `sendMessage(message, webSearchAllowed): Promise<ChatResponse>` — `services/api.ts:40-78`. Builds
  `ChatRequest {message, session_id, web_search_allowed}`; `POST /chat`; on `!res.ok` extracts
  `data.detail` (backend `AppException.detail`) else `Backend error: ${status}` and `throw new Error`;
  on success, if `data.session_id` present, persists it to `localStorage`.
- `uploadFile(file): Promise<any>` — `services/api.ts:80-95`. `FormData` (`file` + `session_id`);
  `POST /upload`; `throw new Error(\`Upload failed: ${status}\`)` on `!res.ok`; returns `res.json()`.
  **`any` return type is a defect we fix** by typing it through a Zod schema.

### `types/index.ts` (33 lines)

`RouteType` union (`:3-10`), `ChatRequest` (`:12-16`), `ChatResponse` (`:18-23`), `Message`
(`:25-32`, fields `id/role/content/route?/sourcesCount?/timestamp: Date`).

### Dead code: `components/chat/chat-interface.tsx`

`wc -c` → **0 bytes** (empty file). Grep proof it is unimported:

```
$ grep -rn "chat-interface" --include="*.ts" --include="*.tsx" . | grep -v node_modules
NO REFERENCES FOUND
```

→ Safe to delete (task 5.m).

### Consumers of `services/api.ts` after M1 (must be re-pointed)

```
$ grep -rn "@/services/api" --include="*.tsx" --include="*.ts" . | grep -v node_modules
app/page.tsx:6            (removed — page becomes a shell)
components/chat/chat-input.tsx:8   (re-point to features/chat/api)
```

`chat-input.tsx:42` calls `api.uploadFile(file)`. **We must re-point this import** to the new
`features/chat/api/chat.api.ts` (which re-exports an `api`-compatible surface) so the component's
behavior is unchanged but the dead `services/api.ts` truly has zero importers. (This is the single
edit allowed to an existing chat component in M1 — an import swap, no markup change.)

---

## 4. Target Architecture & File Tree (delta)

```
lib/
  query-client.ts                      NEW  — makeQueryClient() singleton-per-request
  api/
    api-error.ts                       NEW  — class ApiError + isApiError()
    http-client.ts                     NEW  — request<T>(path, { method, body, schema, auth, signal })
features/
  chat/
    api/
      chat.schemas.ts                  NEW  — Zod: ChatRequest/ChatResponse/Upload/Cleanup
      chat.api.ts                      NEW  — sendMessage/uploadFile/cleanupSession/session helpers (+ `api` compat re-export)
    store/
      chat.store.ts                    NEW  — Zustand: messages/draft/webSearchAllowed/isLoading + actions
    hooks/
      use-blocking-chat.ts             NEW  — useMutation → store (synthesize done step + sources)
      use-chat.ts                      NEW  — facade; reads flags.streaming; delegates blocking now
    components/
      chat-screen.tsx                  NEW  — moved-out layout from page.tsx (+ effects)
app/
  providers.tsx                        EDIT — wrap children in <QueryClientProvider> (M0 created the file)
  page.tsx                             EDIT — gut to `export default () => <ChatScreen />`
types/
  index.ts                             EDIT — z.infer re-exports + Message.{steps,sources,status}
components/chat/
  chat-interface.tsx                   DELETE — empty, unimported
  chat-input.tsx                       EDIT — swap `@/services/api` import → `@/features/chat/api/chat.api`
test/
  setup.ts                             NEW (or M0's) — RTL/jsdom setup
features/chat/store/chat.store.test.ts NEW  — store action unit tests
features/chat/hooks/use-blocking-chat.test.tsx NEW — mutation → store unit test
```

> **Facade note:** `use-chat.ts` is scaffolded here with the **blocking** strategy only. The
> `useStreamingChat` import and the `flags.streaming === true` branch land in **M2**; M1 leaves a
> clearly-commented seam so the diff in M2 is additive.

---

## 5. Tasks (ordered)

Each task lists its goal, exact files, and full copy-pasteable code. Do them in order; later tasks
import earlier ones.

### 5.a — Install runtime deps

**Goal:** add the two state libraries (Query + Zustand). Test libs (`vitest`, RTL, `msw`) are assumed
installed in M0; if not, install them here too (see §7).

```bash
npm install @tanstack/react-query@^5 zustand@^5
# if M0 did not already add the test toolchain:
npm install -D vitest@^3 @testing-library/react@^16 @testing-library/jest-dom@^6 \
  @testing-library/user-event@^14 jsdom@^25 @vitejs/plugin-react@^4
```

Verify: `@tanstack/react-query` and `zustand` appear under `dependencies` in `package.json`.

---

### 5.b — `lib/api/api-error.ts` (typed error class)

**Goal:** one error shape every caller can `catch`. Carries HTTP `status`, the backend `detail`
string (today's `AppException.detail`), and the raw payload for debugging.

```ts
// lib/api/api-error.ts

/**
 * Uniform error thrown by the http-client for any non-2xx response,
 * network failure, or response-validation failure.
 *
 * `detail` mirrors the backend's `AppException.detail` (FastAPI) when present,
 * so UI surfaces (toasts, the "ERROR" assistant bubble) can show a real reason.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Backend-provided human message (FastAPI `detail`), if any. */
  readonly detail?: string;
  /** Raw parsed body (or text), for logging/debugging. */
  readonly payload?: unknown;

  constructor(args: {
    message: string;
    status: number;
    detail?: string;
    payload?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.status = args.status;
    this.detail = args.detail;
    this.payload = args.payload;
    // Restore prototype chain (TS target < ES2022 / transpilation safety).
    Object.setPrototypeOf(this, ApiError.prototype);
  }

  /** Message preferred for user display: backend detail beats the generic message. */
  get userMessage(): string {
    return this.detail ?? this.message;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
```

---

### 5.c — `lib/api/http-client.ts` (generic `request<T>`)

**Goal:** the single networking primitive. Prepends the env base URL, serializes JSON *or* passes a
`FormData` through untouched (multipart upload), Zod-parses successful responses, maps every failure
to `ApiError`, threads an `AbortSignal`, and exposes a **dormant** auth seam.

```ts
// lib/api/http-client.ts
import type { ZodType } from "zod";
import { env } from "@/lib/env"; // from M0: Zod-validated env; exposes NEXT_PUBLIC_API_URL
import { ApiError } from "./api-error";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions<T> {
  method?: HttpMethod;
  /** Plain object → JSON-encoded. FormData → sent as-is (multipart). */
  body?: unknown;
  /** Zod schema to validate+parse the JSON response into `T`. Omit for void/opaque responses. */
  schema?: ZodType<T>;
  /** Dormant in M1 (default false). When true (M6), attaches Bearer + 401-refresh-retry. */
  auth?: boolean;
  /** For cancellation (Stop button in M2; component unmount). */
  signal?: AbortSignal;
  /** Extra headers (merged; never overrides Content-Type for FormData). */
  headers?: Record<string, string>;
}

const BASE_URL = env.NEXT_PUBLIC_API_URL;

/**
 * DORMANT auth interceptor seam — wired in M6 (NEXT_PUBLIC_FEATURE_AUTH).
 * Today returns headers unchanged. Kept as a function so M6 is a localized change.
 */
async function applyAuth(
  headers: Headers,
  _auth: boolean | undefined,
): Promise<Headers> {
  // M6: if (_auth && flags.auth) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

export async function request<T = void>(
  path: string,
  opts: RequestOptions<T> = {},
): Promise<T> {
  const { method = "GET", body, schema, auth, signal, headers: extra } = opts;

  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const headers = new Headers(extra);
  if (!isForm && body !== undefined) headers.set("Content-Type", "application/json");
  await applyAuth(headers, auth);

  const url = `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    // Network failure / abort.
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError({
      message: e instanceof Error ? e.message : "Network request failed",
      status: 0,
      payload: e,
    });
  }

  // Non-2xx → extract backend `detail` (FastAPI AppException) when present.
  if (!res.ok) {
    let detail: string | undefined;
    let payload: unknown;
    try {
      payload = await res.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        const d = (payload as { detail?: unknown }).detail;
        if (typeof d === "string") detail = d;
      }
    } catch {
      /* body wasn't JSON; ignore */
    }
    throw new ApiError({
      message: detail ?? `Backend error: ${res.status}`,
      status: res.status,
      detail,
      payload,
    });
  }

  // 204 / no schema → caller expects void.
  if (res.status === 204 || !schema) return undefined as T;

  let json: unknown;
  try {
    json = await res.json();
  } catch (e) {
    throw new ApiError({
      message: "Response was not valid JSON",
      status: res.status,
      payload: e,
    });
  }

  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new ApiError({
      message: "Response failed schema validation",
      status: res.status,
      detail: parsed.error.message,
      payload: json,
    });
  }
  return parsed.data;
}
```

> If M0 has not yet created `lib/env.ts`, the minimal shim this milestone depends on is:
> `export const env = { NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "https://python-agentic-rag-backend.onrender.com/api" } as const;`
> — but per the dependency, **M0 owns `lib/env.ts`**; do not author it here beyond confirming the
> `NEXT_PUBLIC_API_URL` field exists.

---

### 5.d — `lib/query-client.ts` + wire `QueryClientProvider`

**Goal:** a `QueryClient` factory safe for the App Router (a fresh client per request on the server,
a stable singleton in the browser), then mount `QueryClientProvider` in the M0-created
`app/providers.tsx`.

```ts
// lib/query-client.ts
import { QueryClient, isServer } from "@tanstack/react-query";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0, // chat/upload/cleanup must not silently re-fire
      },
    },
  });
}

let browserClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient(); // never share across requests on the server
  if (!browserClient) browserClient = makeQueryClient();
  return browserClient;
}
```

Edit `app/providers.tsx` (created in M0 with `ThemeProvider`) to add the Query provider. The file is
already `"use client"`. Add only the wrapping — do not remove M0's `ThemeProvider`/anything else:

```tsx
// app/providers.tsx  (EDIT — illustrative final shape; preserve M0's existing providers)
"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/components/theme/theme-provider"; // from M0
import { getQueryClient } from "@/lib/query-client";

export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
```

> If M0 named the provider component or import differently, keep M0's structure and only insert the
> `QueryClientProvider` wrapper + `getQueryClient()` call.

---

### 5.e — `features/chat/api/chat.schemas.ts` (Zod, matching today's backend)

**Goal:** runtime-validate the **currently deployed** contract. `ChatResponse` shape comes from
`types/index.ts:18-23` and the backend `done` payload (`{"answer", "route", ...}`, backend doc
`07_Phase6_LangGraph_and_Streaming.md:202`). `route` is the union from `types/index.ts:3-10`. The
upload response is today `{ status, s3_key }` (Phase-2 doc `03_..._State_Migration.md:545`) — we type
it leniently so older deploys returning extra/missing fields don't throw.

```ts
// features/chat/api/chat.schemas.ts
import { z } from "zod";

export const routeTypeSchema = z.enum([
  "RAG",
  "WEB",
  "DIRECT",
  "WEB+RAG",
  "DIRECT+WEB",
  "DIRECT+RAG",
  "ERROR",
]);

/** POST /api/chat request body (matches services/api.ts:46-50). */
export const chatRequestSchema = z.object({
  message: z.string(),
  session_id: z.string(),
  web_search_allowed: z.boolean(),
});

/** POST /api/chat response (matches types/index.ts:18-23 + backend `done` payload). */
export const chatResponseSchema = z.object({
  answer: z.string(),
  route: routeTypeSchema,
  context_count: z.number().int().nonnegative(),
  // Backend may echo a (possibly new) session id to persist. Optional for resilience.
  session_id: z.string().optional(),
});

/**
 * POST /api/upload response. Today's deploy returns { status, s3_key }
 * (Phase-2 doc :545). Lenient so we never crash the toast path on field drift.
 */
export const uploadResponseSchema = z
  .object({
    status: z.string().optional(),
    s3_key: z.string().optional(),
  })
  .passthrough();

/** POST /api/cleanup response — today returns { status: "cleaned" } or is ignored. */
export const cleanupResponseSchema = z
  .object({ status: z.string().optional() })
  .passthrough();

export type RouteType = z.infer<typeof routeTypeSchema>;
export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type UploadResponse = z.infer<typeof uploadResponseSchema>;
export type CleanupResponse = z.infer<typeof cleanupResponseSchema>;
```

> **Contract note (Phase-2 drift, intentionally NOT followed in M1):** Phase-2 migrates `/upload`
> and `/cleanup` to `Form(...)` fields and `/cleanup` to a `session_id` form field (doc lines
> 526-560). The **currently deployed** backend (which M1 must keep parity with) takes multipart
> `/upload` (`file` + `session_id`) and **JSON** `/cleanup` `{session_id, file_keys}` — exactly what
> `services/api.ts:24-31` and `page.tsx:41-49` send today. M1 preserves the current wire format. The
> `Form(...)` switch is a future milestone aligned to the backend phase ship, not this one.

---

### 5.f — `features/chat/api/chat.api.ts` (ported from `services/api.ts`)

**Goal:** the chat feature's typed API surface, porting `services/api.ts` semantics 1:1 onto
`request<T>`. Keep the localStorage session-id behavior identical. Export an `api`-compatible object
so `chat-input.tsx`'s `api.uploadFile(file)` call (`chat-input.tsx:42`) works after a one-line import
swap.

```ts
// features/chat/api/chat.api.ts
import { v4 as uuidv4 } from "uuid";
import { env } from "@/lib/env";
import { request } from "@/lib/api/http-client";
import {
  chatRequestSchema,
  chatResponseSchema,
  uploadResponseSchema,
  type ChatResponse,
  type UploadResponse,
} from "./chat.schemas";

const SESSION_KEY = "rag_session_id";

/** Identical to services/api.ts:9-17 — SSR-safe get-or-create. */
export function getSessionId(): string {
  if (typeof window === "undefined") return "";
  let sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = uuidv4();
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  return sessionId;
}

function persistSessionId(id: string): void {
  if (typeof window !== "undefined") localStorage.setItem(SESSION_KEY, id);
}

/** Ported from services/api.ts:40-78. Validates the request body, parses the response. */
export async function sendMessage(
  message: string,
  webSearchAllowed: boolean,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const payload = chatRequestSchema.parse({
    message,
    session_id: getSessionId(),
    web_search_allowed: webSearchAllowed,
  });

  const data = await request("/chat", {
    method: "POST",
    body: payload,
    schema: chatResponseSchema,
    signal,
  });

  // Backend may return a fresh session_id to persist (services/api.ts:74-76).
  if (data.session_id) persistSessionId(data.session_id);
  return data;
}

/** Ported from services/api.ts:80-95. `any` return is now the typed UploadResponse. */
export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", getSessionId());

  return request("/upload", {
    method: "POST",
    body: formData, // multipart — http-client leaves Content-Type to the browser
    schema: uploadResponseSchema,
  });
}

/**
 * Ported from services/api.ts:19-38. Preserves the exact JSON `/cleanup`
 * body `{ session_id, file_keys: [] }`, swallows errors, then rotates the
 * local session id so the next message starts fresh.
 */
export async function cleanupSession(): Promise<void> {
  if (typeof window === "undefined") return;
  const sessionId = localStorage.getItem(SESSION_KEY);
  if (sessionId) {
    try {
      await request("/cleanup", {
        method: "POST",
        body: { session_id: sessionId, file_keys: [] as string[] },
        // No schema: today's caller ignores the body; errors are swallowed below.
      });
    } catch (e) {
      console.error("Cleanup failed", e);
    }
  }
  localStorage.removeItem(SESSION_KEY);
  localStorage.setItem(SESSION_KEY, uuidv4());
}

/**
 * Back-compat surface so existing call sites (chat-input.tsx:42 `api.uploadFile`)
 * work with a single import swap. `clearSession` aliases `cleanupSession` to match
 * the old method name used by the sidebar reset flow.
 */
export const api = {
  getSessionId,
  sendMessage,
  uploadFile,
  cleanupSession,
  clearSession: cleanupSession,
} as const;
```

Then the single import swap in `components/chat/chat-input.tsx:8` (no markup change):

```diff
- import { api } from "@/services/api";
+ import { api } from "@/features/chat/api/chat.api";
```

---

### 5.g — `features/chat/store/chat.store.ts` (Zustand)

**Goal:** the live-chat store. Actions are designed so the **streaming** path (M2) reuses them
unchanged: `addMessage` seeds an assistant placeholder, `appendContent` streams tokens, `pushStep`
feeds thinking-steps, `setSources`/`setStatus`/`finalize` close it out. The **blocking** path (M1)
calls `addMessage` + `appendContent`(full answer) + `pushStep`(one `done`) + `setSources` + `finalize`
inside `useBlockingChat`.

```ts
// features/chat/store/chat.store.ts
import { create } from "zustand";
import { v4 as uuidv4 } from "uuid";
import type { Message, Step, Source, RichComponent, MessageStatus, RouteType } from "@/types";

interface ChatState {
  messages: Message[];
  draft: string;
  webSearchAllowed: boolean;
  isLoading: boolean;

  // ---- live-chat actions (shared by blocking + streaming) ----
  addMessage: (msg: Message) => void;
  /** Append text to a message body (streaming tokens, or one blocking write). */
  appendContent: (id: string, chunk: string) => void;
  /** Push/replace a thinking step (matched by `label`) onto a message. */
  pushStep: (id: string, step: Step) => void;
  setSources: (id: string, sources: Source[]) => void;
  /** Append a backend P6 rich component (immutable append). Dark in M1; rendered by M10. */
  addComponent: (id: string, component: RichComponent) => void;
  setStatus: (id: string, status: MessageStatus) => void;
  setRoute: (id: string, route: RouteType) => void;
  /** Mark a message complete (status -> "done") and flip the route badge in. */
  finalize: (id: string) => void;

  // ---- ui/session actions ----
  setDraft: (draft: string) => void;
  setWebSearchAllowed: (v: boolean) => void;
  setLoading: (v: boolean) => void;
  reset: () => void;
}

/** Convenience used by hooks to seed user/assistant messages. */
export function createMessage(partial: Omit<Message, "id" | "timestamp"> & {
  id?: string;
  timestamp?: number;
}): Message {
  return {
    id: partial.id ?? uuidv4(),
    timestamp: partial.timestamp ?? Date.now(),
    steps: partial.steps ?? [],
    sources: partial.sources ?? [],
    status: partial.status ?? "pending",
    ...partial,
  };
}

const updateMessage = (
  messages: Message[],
  id: string,
  fn: (m: Message) => Message,
): Message[] => messages.map((m) => (m.id === id ? fn(m) : m));

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  draft: "",
  webSearchAllowed: false,
  isLoading: false,

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  appendContent: (id, chunk) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        content: m.content + chunk,
      })),
    })),

  pushStep: (id, step) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => {
        const existing = m.steps.findIndex((st) => st.label === step.label);
        const steps =
          existing >= 0
            ? m.steps.map((st, i) => (i === existing ? step : st))
            : [...m.steps, step];
        return { ...m, steps };
      }),
    })),

  setSources: (id, sources) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, sources })),
    })),

  // Dark in M1: nothing emits components yet (the backend `component` event is M2's
  // SSE plumbing, gated behind M10's renderer). Kept here so M10 needs no store change.
  addComponent: (id, component) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        components: [...(m.components ?? []), component],
      })),
    })),

  setStatus: (id, status) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, status })),
    })),

  setRoute: (id, route) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, route })),
    })),

  finalize: (id) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        status: "done" as MessageStatus,
      })),
    })),

  setDraft: (draft) => set({ draft }),
  setWebSearchAllowed: (webSearchAllowed) => set({ webSearchAllowed }),
  setLoading: (isLoading) => set({ isLoading }),
  reset: () => set({ messages: [], isLoading: false }),
}));
```

---

### 5.h — `features/chat/hooks/use-blocking-chat.ts`

**Goal:** the blocking strategy. A TanStack `useMutation` calls `sendMessage`; `onMutate` seeds the
user message + an assistant placeholder + `isLoading`; `onSuccess` writes the unified `Message`
(synthesize one `done` step + `context_count` synthetic sources); `onError` writes the `"ERROR"`
assistant bubble — **exactly** the behavior of `page.tsx:56-96`.

```ts
// features/chat/hooks/use-blocking-chat.ts
import { useMutation } from "@tanstack/react-query";
import { sendMessage } from "@/features/chat/api/chat.api";
import { useChatStore, createMessage } from "@/features/chat/store/chat.store";
import { isApiError } from "@/lib/api/api-error";
import type { ChatResponse } from "@/features/chat/api/chat.schemas";
import type { Source } from "@/types";

interface SendVars {
  text: string;
  webSearch: boolean;
}

interface Ctx {
  assistantId: string;
}

/** Synthesize `context_count` placeholder sources so the M3 sources-panel renders today. */
function synthSources(count: number): Source[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `synthetic-${i}`,
    title: `Source chunk ${i + 1}`,
    snippet: undefined,
    url: undefined,
  }));
}

export function useBlockingChat() {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendContent = useChatStore((s) => s.appendContent);
  const pushStep = useChatStore((s) => s.pushStep);
  const setSources = useChatStore((s) => s.setSources);
  const setRoute = useChatStore((s) => s.setRoute);
  const setStatus = useChatStore((s) => s.setStatus);
  const finalize = useChatStore((s) => s.finalize);
  const setLoading = useChatStore((s) => s.setLoading);

  const mutation = useMutation<ChatResponse, unknown, SendVars, Ctx>({
    mutationFn: ({ text, webSearch }) => sendMessage(text, webSearch),

    onMutate: ({ text }) => {
      // User bubble (page.tsx:57-63).
      addMessage(createMessage({ role: "user", content: text, status: "done" }));
      // Assistant placeholder we fill on success/error (drives <MessageLoading/> parity via isLoading).
      const assistant = createMessage({ role: "assistant", content: "", status: "streaming" });
      addMessage(assistant);
      setLoading(true);
      return { assistantId: assistant.id };
    },

    onSuccess: (res, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      appendContent(assistantId, res.answer);     // full answer in one write
      setRoute(assistantId, res.route);
      setSources(assistantId, synthSources(res.context_count));
      pushStep(assistantId, { label: "done", state: "complete" }); // single synthetic step
      finalize(assistantId);                        // status -> "done"
    },

    onError: (err, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      const message = isApiError(err)
        ? err.userMessage
        : err instanceof Error
          ? err.message
          : "The AI service returned an error. Please try again later.";
      appendContent(assistantId, message);
      setRoute(assistantId, "ERROR");
      setStatus(assistantId, "error");
    },

    onSettled: () => setLoading(false),
  });

  return {
    sendMessage: (text: string, webSearch: boolean) =>
      mutation.mutate({ text, webSearch }),
    isPending: mutation.isPending,
    reset: mutation.reset,
  };
}
```

> Parity detail: today the assistant bubble appears *after* the await resolves. Here we add an empty
> assistant placeholder on `onMutate` and fill it on success. Visually identical because `isLoading`
> drives `<MessageLoading/>` while content is empty, and the placeholder renders no visible body until
> `appendContent` fills it. If pixel-exact ordering matters, the placeholder can instead be added in
> `onSuccess`/`onError` — but the placeholder approach is what M2 streaming requires, so we adopt it
> now and verify in §7 that the rendered result is unchanged.

---

### 5.i — `features/chat/hooks/use-chat.ts` (facade)

**Goal:** the stable public API the UI consumes. Reads `flags.streaming` (from M0's `lib/flags.ts`).
In M1 it always delegates to blocking; the streaming branch is a commented seam filled in M2.

```ts
// features/chat/hooks/use-chat.ts
import { useChatStore } from "@/features/chat/store/chat.store";
import { useBlockingChat } from "./use-blocking-chat";
import { cleanupSession } from "@/features/chat/api/chat.api";
import { flags } from "@/lib/flags"; // from M0
import type { Message } from "@/types";

export interface UseChat {
  messages: Message[];
  isStreaming: boolean;
  sendMessage: (text: string, webSearch: boolean) => void;
  stop: () => void;
  retry: () => void;
}

export function useChat(): UseChat {
  const messages = useChatStore((s) => s.messages);

  // M2 will add: const streaming = useStreamingChat();
  const blocking = useBlockingChat();

  // Facade always exposes the same surface regardless of strategy.
  if (flags.streaming) {
    // M2: return streaming-backed implementation here.
    // Falls through to blocking in M1 because flags.streaming === false (dark).
  }

  return {
    messages,
    isStreaming: blocking.isPending, // "isStreaming" is true while the blocking request is in flight
    sendMessage: blocking.sendMessage,
    stop: () => {
      // M2: AbortController.abort(); no-op in blocking M1.
    },
    retry: blocking.reset,
  };
}

/** Reset flow used by the sidebar (parity with page.tsx handleClearSession). */
export async function resetSession(): Promise<void> {
  await cleanupSession();
  useChatStore.getState().reset();
}
```

> The `if (flags.streaming) { … }` block intentionally falls through in M1 (the flag is dark). It
> documents exactly where M2's streaming return goes, keeping that diff additive.

---

### 5.j — `features/chat/components/chat-screen.tsx`

**Goal:** move the entire layout + effects out of `page.tsx` (`app/page.tsx:104-168` JSX, plus the
auto-scroll `:29-33` and `beforeunload` `:36-54` effects) into a client component driven by the store
and the facade. Markup is copied verbatim — same classes, same structure — only the data source
changes (store/facade instead of `useState`).

```tsx
// features/chat/components/chat-screen.tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useChat, resetSession } from "@/features/chat/hooks/use-chat";
import { useChatStore } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { env } from "@/lib/env";

import { Sidebar } from "@/components/chat/sidebar";
import { ChatMessage } from "@/components/chat/chat-message";
import { ChatInput } from "@/components/chat/chat-input";
import { EmptyState } from "@/components/chat/empty-state";
import { MessageLoading } from "@/components/chat/message-loading";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Menu } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { createMessage } from "@/features/chat/store/chat.store";

export function ChatScreen() {
  const { messages, sendMessage } = useChat();
  const isLoading = useChatStore((s) => s.isLoading);
  const addMessage = useChatStore((s) => s.addMessage);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll (ported from page.tsx:29-33).
  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Cleanup beacon on tab close (ported from page.tsx:36-54).
  useEffect(() => {
    const handleBeforeUnload = () => {
      const sessionId = getSessionId();
      if (!sessionId) return;
      const payload = JSON.stringify({ session_id: sessionId, file_keys: [] });
      navigator.sendBeacon(
        `${env.NEXT_PUBLIC_API_URL}/cleanup`,
        new Blob([payload], { type: "application/json" }),
      );
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  const handleClearSession = async () => {
    await resetSession();
    toast.success("Chat history cleared");
  };

  return (
    <div className="flex h-screen w-full bg-slate-50 overflow-hidden dark:bg-slate-950">
      <div
        className={cn(
          "transition-all duration-300 ease-in-out overflow-hidden",
          isSidebarOpen ? "w-64 opacity-100" : "w-0 opacity-0",
        )}
      >
        <Sidebar
          onClearSession={handleClearSession}
          onToggle={() => setIsSidebarOpen(false)}
        />
      </div>

      <div className="flex flex-col flex-1 h-full relative bg-background shadow-xl rounded-l-2xl border-l border-slate-100 overflow-hidden my-0 mr-0 dark:border-slate-800 dark:shadow-none">
        {!isSidebarOpen && (
          <div className="absolute top-4 left-4 z-10">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(true)}
              className="hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              <Menu className="h-5 w-5 text-slate-500" />
            </Button>
          </div>
        )}

        <ScrollArea className="flex-1 p-4 max-h-[calc(100vh-80px)]">
          <div className="max-w-4xl mx-auto space-y-6 pb-10 pt-10">
            {messages.length === 0 ? (
              <div className="mt-10">
                <EmptyState />
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))}
                {isLoading && <MessageLoading />}
              </>
            )}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <ChatInput
          isLoading={isLoading}
          onSend={sendMessage}
          onFileUploaded={(fileName) => {
            addMessage(
              createMessage({
                role: "assistant",
                content: `📄 "${fileName}" uploaded and queued for ingestion.`,
                status: "done",
              }),
            );
          }}
        />
      </div>
    </div>
  );
}
```

> Note: the empty-assistant-placeholder of §5.h means `messages.length` is never `0` mid-request after
> the first send, so `<EmptyState/>` parity is preserved (it only shows before the first message). The
> `<MessageLoading/>` row still appears while `isLoading` is true, identical to today.

---

### 5.k — Gut `app/page.tsx` to a thin shell

**Goal:** `page.tsx` becomes a server component that renders the client `<ChatScreen />`. All `"use
client"`, state, effects, and handlers are gone (they live in `chat-screen.tsx`).

```tsx
// app/page.tsx  (EDIT — full replacement)
import { ChatScreen } from "@/features/chat/components/chat-screen";

export default function Home() {
  return <ChatScreen />;
}
```

---

### 5.l — Rewrite `types/index.ts` (z.infer re-exports + unified Message)

**Goal:** types become the single source of truth derived from Zod schemas, and `Message` gains
`steps` / `sources` / `status`. `timestamp` switches from `Date` to `number` (epoch ms) for
serializability (see §8). `sourcesCount` is kept as an optional legacy alias so the **unmodified**
`chat-message.tsx:112` (`message.sourcesCount`) still renders without edits in M1.

```ts
// types/index.ts  (EDIT — full replacement)
import { z } from "zod";
import {
  routeTypeSchema,
  chatRequestSchema,
  chatResponseSchema,
} from "@/features/chat/api/chat.schemas";

// ---- API contract types (re-exported from Zod schemas) ----
export type RouteType = z.infer<typeof routeTypeSchema>;
export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;

// ---- Unified live-chat types (streaming-shaped; used today by the blocking path) ----

/** Lifecycle of an assistant message as it is produced. */
export type MessageStatus = "pending" | "streaming" | "done" | "error";

/** One agent/thinking step (M3 thinking-steps panel; fed live by SSE `status` in M2). */
export interface Step {
  /** Stable label, e.g. "routing" | "retrieving" | "searching web" | "synthesizing" | "done". */
  label: string;
  state: "active" | "complete" | "error";
  /** Optional human detail for the panel. */
  detail?: string;
}

/** A retrieved/cited source (M3 sources-panel). Blocking path synthesizes these from context_count. */
export interface Source {
  id: string;
  title: string;
  snippet?: string;
  url?: string;
}

/**
 * Opaque forward-compat placeholder for the backend Phase-6 `component` SSE event
 * (catalog: table | chart | citation | code | callout | media — backend
 * `09_Phase6_Agentic_Architecture.md` §5 + Appendix C). M1 carries it as an
 * untyped bag so the agentic rich-output upgrade is a flag-flip + renderer, not a
 * second `Message` refactor (the "architect once" principle). It is **refined into
 * a validated discriminated union by M10** (the strict per-type Zod schemas +
 * renderers live there); nothing in M1/M2 reads or writes a typed shape.
 */
export interface RichComponent {
  type: string;
  [key: string]: unknown;
}

/** The one message shape both blocking (M1) and streaming (M2) write. */
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** epoch milliseconds — serializable (see M1 §8). */
  timestamp: number;
  status: MessageStatus;
  steps: Step[];
  sources: Source[];
  route?: RouteType;
  /** Backend P6 `component` event payloads; empty on the blocking path; rendered by M10. */
  components?: RichComponent[];
  /** Legacy alias kept so the unmodified chat-message.tsx renders the chunk count in M1. */
  sourcesCount?: number;
}
```

> **Compat shim for the unmodified `chat-message.tsx`:** that component reads `message.sourcesCount`
> (`:112`) and `message.route` (`:50`). `route` is still present. For `sourcesCount`, since M1 does not
> edit `chat-message.tsx`, set it alongside `sources` in `useBlockingChat.onSuccess` by also calling a
> tiny store update — OR simplest: in `synthSources` flow, also `setSourcesCount`. To avoid adding a
> store action, the pragmatic M1 choice is to keep deriving the displayed count from
> `message.sources.length` in M3; **for strict M1 parity** add `sourcesCount: res.context_count` by
> extending `createMessage`/`onSuccess` to set it. (Implementation note: set
> `message.sourcesCount = res.context_count` in `onSuccess` via a one-line `setStatus`-style action, or
> accept that the "Referenced N chunks" footer now reads `sources.length`, which equals
> `context_count` by construction — behaviorally identical.) Choose the `sources.length` derivation in
> M3; in M1 keep `sourcesCount` populated to guarantee zero visual diff.

---

### 5.m — Delete dead `components/chat/chat-interface.tsx`

**Goal:** remove the empty, unimported file.

Proof (re-run before deleting):

```bash
wc -c components/chat/chat-interface.tsx           # -> 0
grep -rn "chat-interface" --include="*.ts" --include="*.tsx" . | grep -v node_modules
# (no output) => zero importers
rm components/chat/chat-interface.tsx
```

---

## 6. Unified Message Shape

The final types (authored in §5.l):

```ts
export type MessageStatus = "pending" | "streaming" | "done" | "error";

export interface Step {
  label: string;                                  // "routing" | "retrieving" | "searching web" | "synthesizing" | "done"
  state: "active" | "complete" | "error";
  detail?: string;
}

export interface Source {
  id: string;
  title: string;
  snippet?: string;
  url?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;          // epoch ms (serializable)
  status: MessageStatus;
  steps: Step[];
  sources: Source[];
  route?: RouteType;
  components?: RichComponent[]; // backend P6 `component` event; empty on the blocking path; rendered by M10
  sourcesCount?: number;      // legacy alias for the unmodified chat-message.tsx (M1 only)
}
```

**How the blocking path (M1) synthesizes it** (in `useBlockingChat.onSuccess`, §5.h):

1. `appendContent(id, res.answer)` — the whole answer arrives in one write. M2's streaming path calls
   `appendContent` repeatedly per token; the resulting `content` is identical.
2. `setRoute(id, res.route)` — drives the route badge (`chat-message.tsx:50-54`).
3. `setSources(id, synthSources(res.context_count))` — manufactures `context_count` placeholder
   `Source` objects so the **M3 sources-panel** has rows today. M2/M9 replaces these with real cited
   sources from the backend; the panel code never changes because the field shape is identical.
4. `pushStep(id, { label: "done", state: "complete" })` — a single synthetic step. M2 pushes the real
   sequence (`routing → retrieving/searching → synthesizing → done`) from SSE `status` events into the
   **same** `steps[]`, so the **M3 thinking-steps panel** renders the synthetic one-step today and the
   live multi-step later with no panel change.
5. `finalize(id)` — `status: "done"`.

Because both strategies write the **same** five fields through the **same** store actions, flipping
`flags.streaming` in M9 changes *which hook calls them and how often* — never the `Message` shape, the
store, or any panel/component. That is the entire point of building the shape now.

---

## 7. Testing & Verification

### Vitest config (if not already added in M0)

```ts
// vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
  },
  resolve: { alias: { "@": resolve(__dirname, ".") } },
});
```

```ts
// test/setup.ts
import "@testing-library/jest-dom/vitest";
```

Add to `package.json` scripts: `"test": "vitest run"`, `"test:watch": "vitest"`.

### Store unit tests — `features/chat/store/chat.store.test.ts`

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore, createMessage } from "./chat.store";

const reset = () =>
  useChatStore.setState({ messages: [], draft: "", webSearchAllowed: false, isLoading: false });

describe("chat.store", () => {
  beforeEach(reset);

  it("addMessage appends with defaulted fields", () => {
    const m = createMessage({ role: "user", content: "hi" });
    useChatStore.getState().addMessage(m);
    const [stored] = useChatStore.getState().messages;
    expect(stored.content).toBe("hi");
    expect(stored.status).toBe("pending");
    expect(stored.steps).toEqual([]);
    expect(stored.sources).toEqual([]);
    expect(typeof stored.timestamp).toBe("number");
  });

  it("appendContent concatenates (streaming-equivalent)", () => {
    const m = createMessage({ role: "assistant", content: "" });
    const { addMessage, appendContent } = useChatStore.getState();
    addMessage(m);
    appendContent(m.id, "Hel");
    appendContent(m.id, "lo");
    expect(useChatStore.getState().messages[0].content).toBe("Hello");
  });

  it("pushStep replaces a step with the same label", () => {
    const m = createMessage({ role: "assistant", content: "" });
    const { addMessage, pushStep } = useChatStore.getState();
    addMessage(m);
    pushStep(m.id, { label: "routing", state: "active" });
    pushStep(m.id, { label: "routing", state: "complete" });
    const steps = useChatStore.getState().messages[0].steps;
    expect(steps).toHaveLength(1);
    expect(steps[0].state).toBe("complete");
  });

  it("setSources + finalize produce a done message", () => {
    const m = createMessage({ role: "assistant", content: "answer" });
    const { addMessage, setSources, finalize } = useChatStore.getState();
    addMessage(m);
    setSources(m.id, [{ id: "s0", title: "Source chunk 1" }]);
    finalize(m.id);
    const msg = useChatStore.getState().messages[0];
    expect(msg.sources).toHaveLength(1);
    expect(msg.status).toBe("done");
  });

  it("reset clears messages and loading", () => {
    const { addMessage, setLoading, reset: r } = useChatStore.getState();
    addMessage(createMessage({ role: "user", content: "x" }));
    setLoading(true);
    r();
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().isLoading).toBe(false);
  });
});
```

### `useBlockingChat` unit test — `features/chat/hooks/use-blocking-chat.test.tsx`

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock the API module the hook depends on.
vi.mock("@/features/chat/api/chat.api", () => ({
  sendMessage: vi.fn(),
}));

import { sendMessage } from "@/features/chat/api/chat.api";
import { useBlockingChat } from "./use-blocking-chat";
import { useChatStore } from "@/features/chat/store/chat.store";

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: 0 } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("useBlockingChat", () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], isLoading: false });
    vi.clearAllMocks();
  });

  it("on success writes a unified assistant Message (synthesized step + sources)", async () => {
    (sendMessage as ReturnType<typeof vi.fn>).mockResolvedValue({
      answer: "The answer.",
      route: "RAG",
      context_count: 2,
      session_id: "s1",
    });

    const { result } = renderHook(() => useBlockingChat(), { wrapper });
    act(() => result.current.sendMessage("question", false));

    await waitFor(() => {
      const msgs = useChatStore.getState().messages;
      expect(msgs).toHaveLength(2);                 // user + assistant
      const assistant = msgs[1];
      expect(assistant.role).toBe("assistant");
      expect(assistant.content).toBe("The answer.");
      expect(assistant.route).toBe("RAG");
      expect(assistant.status).toBe("done");
      expect(assistant.sources).toHaveLength(2);    // context_count synthesized
      expect(assistant.steps).toEqual([{ label: "done", state: "complete" }]);
    });
    expect(useChatStore.getState().isLoading).toBe(false);
  });

  it("on error writes an ERROR assistant bubble with the backend detail", async () => {
    (sendMessage as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Gemini 429"));
    const { result } = renderHook(() => useBlockingChat(), { wrapper });
    act(() => result.current.sendMessage("q", true));

    await waitFor(() => {
      const assistant = useChatStore.getState().messages[1];
      expect(assistant.route).toBe("ERROR");
      expect(assistant.status).toBe("error");
      expect(assistant.content).toBe("Gemini 429");
    });
  });
});
```

### Functional parity checklist (manual, against today's backend / Render URL)

- [ ] **Send message** — type + Enter / send button → user bubble, `<MessageLoading/>`, then assistant
      bubble with the same `answer`, route badge, and "Referenced N chunks" footer as before.
- [ ] **Web-search toggle** — Globe toggles; the request body `web_search_allowed` flips accordingly
      (verify in Network tab) — same as today.
- [ ] **Upload file** — paperclip → file picker → `toast.success` "<name> uploaded" → assistant
      "📄 … queued for ingestion." bubble appears. Multipart request body unchanged.
- [ ] **Reset session** — sidebar "Reset Session" → `/cleanup` JSON `{session_id, file_keys:[]}` fires,
      messages clear, `toast.success("Chat history cleared")`, a fresh `rag_session_id` is in
      `localStorage`.
- [ ] **Cleanup on unload** — close/refresh the tab → `navigator.sendBeacon` to `/cleanup` fires (verify
      in Network tab → "ping"/beacon).
- [ ] **Error path** — point at a failing backend → assistant `ERROR` bubble shows the backend
      `detail` string.
- [ ] **Gates:** `npm run lint`, `tsc --noEmit`, `vitest run`, `next build` all green.

---

## 8. Risks & Gotchas

1. **SSR / `'use client'` boundaries.** `chat-screen.tsx` and `providers.tsx` MUST be `"use client"`
   (Zustand store + Query hooks). `app/page.tsx` becomes a **server** component that only renders the
   client `<ChatScreen/>` — keep it free of hooks. Importing the store into a server component throws.
2. **`QueryClient` per request on the server.** `getQueryClient()` returns a fresh client on the server
   (`isServer`) and a singleton in the browser. A module-level `new QueryClient()` would leak state
   across requests/users in RSC.
3. **Do NOT put `messages` in the Query cache.** They live only in Zustand. Mixing them invites
   `setQueryData` churn on every token in M2 and double-source-of-truth bugs. The mutation's *result*
   is written to the store in `onSuccess`; the Query cache holds nothing chat-message-shaped.
4. **`timestamp` must be serializable.** We switched `Date` → `number` (epoch ms). A `Date` object in a
   store that may later be persisted/hydrated (auth/session stores in M6) or serialized for tests/SSR
   serializes to a string and breaks equality. Any display formatting uses
   `new Date(message.timestamp)` at render time. (`chat-message.tsx` does not currently render the
   timestamp, so no component change is required in M1.)
5. **Session-id hydration from `localStorage`.** `getSessionId()` is SSR-guarded (`typeof window`).
   Never call it during render of a server component; it's used inside effects (`beforeunload`) and
   client API calls only. Hydration mismatch risk is nil because no server-rendered markup depends on
   it.
6. **Empty-assistant-placeholder ordering (see §5.h note).** Adding the placeholder on `onMutate`
   changes *when* the assistant DOM node is created (immediately vs. after await). Verify §7's "send
   message" item to confirm no visible difference (the body is empty until `appendContent`, and
   `<MessageLoading/>` covers the in-flight state exactly as today).
7. **`chat-input.tsx` import swap is the only allowed edit to an existing chat component.** It must be a
   pure import change (`@/services/api` → `@/features/chat/api/chat.api`). Do not refactor its markup or
   state — that's M3. Forgetting this swap leaves `services/api.ts` with a live importer and the "dead
   code" claim false.
8. **Behavior drift via Query defaults.** `mutations.retry: 0` (5.d) is mandatory — a default retry
   would double-fire `/chat`/`/upload`, a real behavior change. Verify the Network tab shows exactly one
   request per action.
9. **Zod strictness vs. real payloads.** `uploadResponseSchema`/`cleanupResponseSchema` use
   `.passthrough()` and optional fields so older/newer backend deploys with extra fields don't throw and
   break the toast/reset flow. `chatResponseSchema` is strict on the four known fields; if the deployed
   backend omits `context_count`, that's a real contract break we *want* surfaced as an `ApiError`.
10. **`sourcesCount` legacy alias.** Kept on `Message` only so the unmodified `chat-message.tsx:112`
    footer renders in M1. M3 migrates that component to read `sources.length` and the alias is dropped.
    Track it as M3 cleanup; don't let it ossify.

---

## 9. Exit Criteria (checkable)

- [ ] `@tanstack/react-query` and `zustand` are in `package.json` `dependencies`.
- [ ] `lib/api/api-error.ts`, `lib/api/http-client.ts`, `lib/query-client.ts` exist and compile.
- [ ] `app/providers.tsx` wraps children in `QueryClientProvider` (M0's `ThemeProvider` preserved).
- [ ] `features/chat/api/{chat.schemas.ts,chat.api.ts}`, `features/chat/store/chat.store.ts`,
      `features/chat/hooks/{use-blocking-chat.ts,use-chat.ts}`,
      `features/chat/components/chat-screen.tsx` all exist and compile.
- [ ] `app/page.tsx` is a thin server-component shell rendering `<ChatScreen/>` (no `"use client"`, no
      hooks, no handlers).
- [ ] `types/index.ts` re-exports `z.infer` types and `Message` includes `steps`/`sources`/`status`;
      `timestamp` is `number`.
- [ ] `components/chat/chat-input.tsx` imports `api` from `@/features/chat/api/chat.api`.
- [ ] `components/chat/chat-interface.tsx` is deleted; `grep -rn "chat-interface"` returns nothing.
- [ ] `grep -rn "@/services/api"` returns **zero** importers (only the now-orphaned file itself, if
      retained).
- [ ] `npm run lint`, `tsc --noEmit`, `vitest run`, `next build` all pass.
- [ ] Store + `useBlockingChat` unit tests pass.
- [ ] Manual parity checklist (§7) fully ticked: send / web-toggle / upload / reset / unload-cleanup /
      error path all behave identically to pre-M1.
- [ ] Network tab shows exactly one request per user action (no retry double-fire).
- [ ] `NEXT_PUBLIC_FEATURE_STREAMING` is `false`/unset and no streaming code path is reachable.

---

## 10. Commit Plan

Milestone-sized commits on the working branch (`claude/frontend-improvements-planning-1aX4u`). Group so
each commit builds & passes typecheck independently.

1. `chore(deps): add @tanstack/react-query + zustand (and vitest toolchain if absent)`
2. `feat(api): typed http-client + ApiError + query-client; wire QueryClientProvider`
   — `lib/api/api-error.ts`, `lib/api/http-client.ts`, `lib/query-client.ts`, `app/providers.tsx`.
3. `feat(chat): zod schemas + chat.api ported from services/api (parity)`
   — `features/chat/api/{chat.schemas.ts,chat.api.ts}`; swap `chat-input.tsx` import.
4. `feat(chat): zustand store + useBlockingChat + useChat facade (streaming-shaped Message)`
   — `features/chat/store/chat.store.ts`, `features/chat/hooks/*`, `types/index.ts`.
5. `refactor(chat): extract ChatScreen, gut page.tsx to a thin shell; delete dead chat-interface.tsx`
   — `features/chat/components/chat-screen.tsx`, `app/page.tsx`, remove
   `components/chat/chat-interface.tsx`.
6. `test(chat): unit tests for chat store + useBlockingChat`
   — `features/chat/store/chat.store.test.ts`, `features/chat/hooks/use-blocking-chat.test.tsx`,
   `vitest.config.ts`/`test/setup.ts` if not from M0.

Final body footer for the milestone PR:

```
M1 — Architecture Refactor (Behavioral Parity).
Moves all chat logic out of app/page.tsx into features/chat/{api,store,hooks,components};
adds TanStack Query + Zustand + typed http-client + Zod schemas; unified streaming-shaped
Message; deletes dead chat-interface.tsx. No behavior change (blocking flow identical).

https://claude.ai/code/session_01Vf1vzppqBGXAd1k9PPKMAB
```
