# M5 — Tests, E2E, Docker & CI Hardening

This milestone makes the frontend's testing comprehensive and ships it for production. It adds a
Vitest + React Testing Library + MSW unit/component suite, a Playwright end-to-end flow that runs the
real app against MSW network mocks, a slim multi-stage Docker image built on Next.js `output:'standalone'`,
and a hardened GitHub Actions pipeline that runs lint → typecheck → unit+coverage → build → e2e on
every push. No product behavior changes; this is pure quality, packaging, and CI infrastructure.

**Status:** Planned — first production-readiness milestone.
**Depends on:** M0 (Prettier/ESLint/Husky, CI skeleton, `lib/env.ts`, `lib/flags.ts`, theme provider/toggle),
M1 (TanStack Query + Zustand, `lib/api/http-client.ts`, `features/chat` folders, `chat.store`, `use-blocking-chat`),
M2 (`useChat` facade, `lib/sse/parser.ts`, unified `Message` shape with `steps`/`sources`/`status`),
M3 (`chat-message`, `chat-input`, `thinking-steps`, `sources-panel`, `code-block`, `route-badge`),
M4 (framer-motion layer + `use-reduced-motion`).
**Unlocks:** confident shipping — green CI gate, a small bootable production image, and an executable
regression contract for the core chat flow. This is the **recommended final milestone of the first
delivery (M0 → M5)**; after it lands the app is fully shippable against today's blocking backend, and
M6–M9 layer on backend-phase features behind their flags.

---

## 1. Objective & Scope

### In scope
- **Comprehensive unit/component tests (Vitest + RTL):** Zustand `chat.store`, `use-blocking-chat`,
  `parseSSE` (the M1/M2 focused tests are absorbed and extended here), plus component tests for
  `chat-message`, `chat-input`, `thinking-steps`, `sources-panel`, `route-badge`, `message-actions`.
- **MSW (Mock Service Worker v2):** one set of request handlers (`/chat`, `/upload`, `/cleanup`, plus
  a streaming SSE stub) reused by **both** the Node test runner (unit/component) and the **browser**
  during Playwright E2E.
- **Playwright E2E:** `e2e/chat.spec.ts` driving the full flow — load → send message → assistant
  renders → upload file → toggle theme → reset session — against MSW mocks (deterministic, no network).
- **Production packaging:** `next.config.ts` `output:'standalone'` + a slim multi-stage `Dockerfile`
  that copies only `.next/standalone` + `.next/static` + `public`, runs as a non-root user, and boots
  with `node server.js`. Add `.dockerignore`.
- **CI hardening:** extend the M0 `.github/workflows/ci.yml` skeleton into a fast-fail pipeline:
  install (npm cache) → lint → typecheck → unit+coverage → build → e2e (Playwright browsers) →
  optional docker build.

### Out of scope (explicitly)
- **No new product features.** No auth (M6), no BYOK/model picker (M7), no presigned uploads (M8), no
  real SSE flip (M9). The streaming MSW handler here is a **mock contract** so the streaming strategy
  *can* be exercised; `NEXT_PUBLIC_FEATURE_STREAMING` stays `false` for the default E2E run.
- No visual redesign, no new components, no copy changes.
- No deployment/release automation beyond producing the image (publishing to a registry is M9-adjacent
  and intentionally left as an optional, commented CI job).

---

## 2. Decisions & Rationale

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| **Vitest over Jest** | Native ESM + TS via Vite's transform pipeline (no `babel-jest`/`ts-jest` ceremony), shares the project's resolver/aliases, far faster watch, and is the de-facto choice for Next 16 / React 19 / Tailwind v4 toolchains. `expect` API is Jest-compatible so RTL/`jest-dom` work unchanged. | Jest (slow ESM story, config drift against Vite, awkward with `next/*` ESM). |
| **React Testing Library, behavior-first** | Tests assert what the **user** sees and does (roles, text, `userEvent`), not component internals or implementation detail. This survives the M4 motion refactor and shadcn/Radix swaps. Query by accessible role/name → also a passive a11y check. | Enzyme (shallow rendering, impl-coupled, no React 19 support). |
| **MSW for all network mocking** | One handler set is the single source of truth for the API contract and runs in **both** worlds: a Node `setupServer` for Vitest and a **browser** service worker for Playwright. We never hand-roll `fetch` stubs, and unit + E2E can't drift from each other. MSW v2 intercepts at the network layer, so `http-client.ts` is exercised for real. | Per-test `vi.fn()` fetch stubs (drift, no streaming, no browser story); a throwaway Express mock server (duplicate contract). |
| **Playwright over Cypress** | First-class multi-browser, fast parallel execution, built-in `webServer` orchestration, trace viewer, and a request-interception model that composes cleanly with MSW's browser worker. Native TS, no plugin zoo. | Cypress (slower, heavier, weaker parallelism, awkward TS/ESM, no true multi-tab). |
| **`output:'standalone'`** | Next traces the **exact** module graph the server needs and emits a self-contained `.next/standalone` (its own minimal `node_modules` + `server.js`). The runtime image no longer ships the full dev/prod `node_modules` — this is the lever that "drops image size sharply." | Shipping full `node_modules` (current Dockerfile — fat); `next start` in the image (needs all deps). |
| **Multi-stage Docker (deps → builder → runner)** | Build tooling, dev deps, and source never reach the final layer. The runner copies only the standalone bundle + static + public, runs non-root, and is reproducible/cacheable per stage. | Single-stage (leaks toolchain + source, huge). |
| **Fast-fail CI ordering** | `lint → typecheck → unit+coverage → build → e2e`. Cheap, high-signal checks fail in seconds before we pay for a build or spin up browsers. `build` is a prerequisite of `e2e` (Playwright serves the built app). Jobs share a primed npm cache. | One monolithic job (no parallelism, slow feedback, can't see which stage failed). |

---

## 3. Current-State Snapshot

**`Dockerfile` (current — fat).** Builds in a `node:22.11.0-alpine` builder, then in the runner stage
runs `npm install --omit=dev` and copies `.next`, `public`, `next.config.ts`, **and the entire source
tree** (`app`, `components`, `services`, `types`, `lib`), launching with `npm run start`. Two problems:
the runner re-installs a full production `node_modules` (hundreds of MB, includes everything in
`dependencies` whether or not the server needs it at runtime), and it carries source it doesn't need.
There is no `output:'standalone'`, so Next can't emit a traced minimal bundle. Result: a large image
and a slow cold start.

**`next.config.ts` (current).** Minimal — only `devIndicators: false`. **No `output:'standalone'`**, so
`next build` does not emit `.next/standalone`.

**`package.json` (current).** Scripts are `dev`/`build`/`start`/`lint` only. **No test tooling at all**
— no `vitest`, `@testing-library/*`, `jsdom`, `msw`, or `@playwright/test`, and no `test`/`test:e2e`
scripts. `dependencies` already include `next-themes`, `sonner`, `react-markdown`,
`react-syntax-highlighter`, `uuid`. (TanStack Query, Zustand, Zod, framer-motion, etc. arrive in M1–M4.)

**`tsconfig.json`.** `strict: true`, `moduleResolution: "bundler"`, path alias `@/* → ./*`. Vitest must
mirror this `@` alias or imports break in tests.

**`.gitignore`.** Already ignores `/node_modules`, `/.next/`, `/coverage`, `.env*`, `*.tsbuildinfo`,
`next-env.d.ts`. We will add Playwright artifacts (`/test-results`, `/playwright-report`, `/.playwright`)
and the MSW-generated worker file (`/public/mockServiceWorker.js`).

**`.github/workflows/ci.yml` (from M0).** A skeleton exists: checkout → setup-node → `npm ci` → lint →
typecheck. M5 **hardens and extends** it (does not replace it) with coverage, build, and the Playwright
e2e job, plus an optional docker-build job.

**Source contracts to honor (`services/api.ts`, `types/index.ts`).** The backend (today) is **blocking
JSON**:
- `POST /chat` ← `{ message, session_id, web_search_allowed }` → `{ answer, route, context_count, session_id }`
  where `route: RouteType` is one of `RAG | WEB | DIRECT | WEB+RAG | DIRECT+WEB | DIRECT+RAG | ERROR`.
- `POST /upload` ← multipart (`file`, `session_id`) → JSON (filename/status).
- `POST /cleanup` ← `{ session_id, file_keys }` → JSON ok.
- The API base is `process.env.NEXT_PUBLIC_API_URL` (default the Render URL). MSW handlers must match
  whatever base the test env sets (see §6).

The future SSE contract (backend P6, frontend M9; authoritative in
`Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md`) — which our streaming MSW stub
mimics — is `text/event-stream` emitting `event: status` with
`data: {"stage": "routing"|"retrieving"|"searching web"|"synthesizing"}`, `event: token` with
`data: {"text": "..."}`, zero-or-more `event: component` with
`data: {"type": "table"|"chart"|"citation"|"code"|"callout"|"media", ...}` (a whole rich-output block,
rendered by M10), and a terminal **typed** `event: done` with
`data: {"answer": "...", "route": "RAG"|"WEB"|"BOTH"|"DIRECT"}` (the flat route enum; `BOTH → "WEB+RAG"`).
A bare `[DONE]` sentinel is also tolerated defensively by the M2 parser.

---

## 4. Target File Tree (delta)

```
typescript-agentic-rag-frontend/
├─ vitest.config.ts                     # NEW — jsdom env, setup, @ alias, v8 coverage
├─ playwright.config.ts                 # NEW — webServer, baseURL, chromium project
├─ test/
│  ├─ setup.ts                          # NEW — jest-dom, MSW lifecycle, jsdom polyfills
│  └─ msw/
│     ├─ handlers.ts                    # NEW — /chat /upload /cleanup + SSE stub
│     ├─ server.ts                      # NEW — setupServer (Node, for Vitest)
│     └─ browser.ts                     # NEW — setupWorker (browser, for E2E dev harness)
├─ e2e/
│  └─ chat.spec.ts                      # NEW — load→send→assistant→upload→theme→reset
├─ features/chat/
│  ├─ store/chat.store.test.ts          # NEW (or extend M1's) — store actions/reducers
│  ├─ hooks/use-blocking-chat.test.tsx  # NEW (or extend M1's) — mutation→store write
│  └─ components/
│     ├─ chat-message.test.tsx          # NEW
│     ├─ chat-input.test.tsx            # NEW
│     ├─ thinking-steps.test.tsx        # NEW
│     ├─ sources-panel.test.tsx         # NEW
│     ├─ route-badge.test.tsx           # NEW
│     └─ message-actions.test.tsx       # NEW
├─ lib/sse/parser.test.ts               # NEW (or extend M2's) — multi-line/partial/[DONE]
├─ Dockerfile                           # REWRITE — multi-stage, standalone, non-root
├─ .dockerignore                        # NEW
├─ next.config.ts                       # EDIT — add output:'standalone'
├─ .gitignore                           # EDIT — playwright + msw worker artifacts
├─ .github/workflows/ci.yml             # HARDEN — lint→typecheck→test→build→e2e (+docker)
└─ package.json                         # EDIT — dev deps + test scripts
```

---

## 5. Tasks (ordered)

### Task 1 — Install dev dependencies

**Goal:** add the test toolchain without touching runtime `dependencies`.

**Files:** `package.json` (+ `package-lock.json`).

```bash
npm install -D \
  vitest@^3 \
  @vitejs/plugin-react@^4 \
  @vitest/coverage-v8@^3 \
  jsdom@^25 \
  @testing-library/react@^16 \
  @testing-library/dom@^10 \
  @testing-library/jest-dom@^6 \
  @testing-library/user-event@^14 \
  msw@^2 \
  @playwright/test@^1

# Install the Chromium browser binary + OS deps for Playwright (local + CI):
npx playwright install --with-deps chromium

# Generate the MSW browser service worker into /public (used by the E2E dev harness):
npx msw init public/ --save
```

Notes:
- `@vitest/coverage-v8` matches the Vitest major. `@testing-library/dom` is a peer of
  `@testing-library/react@16` and must be explicit.
- `msw init public/ --save` writes `public/mockServiceWorker.js` and records the public dir in
  `package.json` (`msw.workerDirectory`). That file is git-ignored (Task 12) and regenerated in CI.

### Task 2 — `vitest.config.ts`

**Goal:** jsdom environment, the shared setup file, the `@` path alias mirroring `tsconfig.json`, and a
v8 coverage report with thresholds.

**Files:** `vitest.config.ts` (NEW).

```ts
// vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // mirror tsconfig.json "paths": { "@/*": ["./*"] }
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    globals: true, // describe/it/expect/vi without imports
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    css: false, // don't process Tailwind/PostCSS in unit tests
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e/**"], // Playwright owns e2e/
    clearMocks: true,
    restoreMocks: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      reportsDirectory: "./coverage",
      include: [
        "features/**/*.{ts,tsx}",
        "lib/**/*.{ts,tsx}",
        "hooks/**/*.{ts,tsx}",
      ],
      exclude: [
        "**/*.d.ts",
        "**/*.test.{ts,tsx}",
        "**/*.spec.{ts,tsx}",
        "**/index.ts", // barrels
        "test/**",
        "e2e/**",
      ],
      thresholds: {
        // Baseline floor — ratchet up as suites grow (mirrors the backend's
        // "set a floor, raise it" policy). Fail CI below these numbers.
        statements: 70,
        branches: 65,
        functions: 70,
        lines: 70,
      },
    },
  },
});
```

> **Ratchet policy:** start at the floor above; once the suite stabilizes, raise thresholds toward
> 85/80. Never lower them to make a red build green — add tests instead.

### Task 3 — `test/setup.ts`

**Goal:** register `jest-dom` matchers, wire the MSW Node server lifecycle, and polyfill the browser
APIs jsdom lacks but framer-motion (M4), `react-textarea-autosize` (M3), and Radix rely on.

**Files:** `test/setup.ts` (NEW).

```ts
// test/setup.ts
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { server } from "./msw/server";

// ---- MSW lifecycle (Node) -------------------------------------------------
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup(); // unmount React trees between tests
  server.resetHandlers(); // drop per-test server.use(...) overrides
});
afterAll(() => server.close());

// ---- jsdom polyfills ------------------------------------------------------
// matchMedia: next-themes, prefers-reduced-motion (use-reduced-motion), Tailwind queries.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // deprecated, kept for compat
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

// ResizeObserver: react-textarea-autosize + framer-motion layout measurement.
class ResizeObserverStub {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

// IntersectionObserver: scroll-into-view / lazy reveal in message list.
class IntersectionObserverStub {
  root = null;
  rootMargin = "";
  thresholds = [];
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
}
vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);

// scrollIntoView: auto-scroll on new message (jsdom doesn't implement it).
Element.prototype.scrollIntoView = vi.fn();

// Clipboard: message/code copy actions.
Object.assign(navigator, {
  clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
});
```

### Task 4 — MSW handlers + servers

**Goal:** one handler set matching the backend contract, exposed as a Node `setupServer` (Vitest) and a
browser `setupWorker` (E2E dev harness).

**Files:** `test/msw/handlers.ts`, `test/msw/server.ts`, `test/msw/browser.ts` (all NEW).

The handlers match `${NEXT_PUBLIC_API_URL}` — in tests that env is set to a stable base (see §6). We use
a relative-friendly base so the same handlers match whether the app calls an absolute or relative URL.

```ts
// test/msw/handlers.ts
import { http, HttpResponse, delay } from "msw";

// Tests/E2E pin NEXT_PUBLIC_API_URL to this; '*' wildcard keeps it host-agnostic.
const API = "*/api"; // matches "http://localhost:8000/api", "/api", the Render URL, etc.

export const handlers = [
  // --- Blocking chat (today's contract) ---
  http.post(`${API}/chat`, async ({ request }) => {
    const body = (await request.json()) as {
      message: string;
      session_id: string;
      web_search_allowed: boolean;
    };
    await delay(20); // exercise loading states deterministically
    return HttpResponse.json({
      answer: `Echo: ${body.message}`,
      route: body.web_search_allowed ? "WEB" : "RAG",
      context_count: 2,
      session_id: body.session_id || "msw-session",
    });
  }),

  // --- File upload (multipart) ---
  http.post(`${API}/upload`, async ({ request }) => {
    const form = await request.formData();
    const file = form.get("file") as File | null;
    return HttpResponse.json({
      filename: file?.name ?? "unknown",
      status: "uploaded",
      file_key: "msw/uploads/mock-key",
    });
  }),

  // --- Session cleanup / reset ---
  http.post(`${API}/cleanup`, async () => {
    return HttpResponse.json({ ok: true });
  }),

  // --- Streaming SSE stub (dark today; mimics backend P6 contract) ---
  // Drives useStreamingChat when NEXT_PUBLIC_FEATURE_STREAMING=true is exercised.
  http.post(`${API}/chat/stream`, async () => {
    const encoder = new TextEncoder();
    const frames = [
      `event: status\ndata: ${JSON.stringify({ stage: "routing" })}\n\n`,
      `event: status\ndata: ${JSON.stringify({ stage: "retrieving" })}\n\n`,
      `event: status\ndata: ${JSON.stringify({ stage: "synthesizing" })}\n\n`,
      `event: token\ndata: ${JSON.stringify({ text: "Hello" })}\n\n`,
      `event: token\ndata: ${JSON.stringify({ text: ", world." })}\n\n`,
      // 09_Phase6 `component` event — a whole rich-output block (citation = sources channel). M10 renders it.
      `event: component\ndata: ${JSON.stringify({
        type: "citation",
        items: [{ label: "doc.pdf · p.1", source_id: "chunk_1", snippet: "…relevant excerpt…" }],
      })}\n\n`,
      // Typed terminal `done` with the FLAT route enum (09_Phase6); the M2 parser also tolerates a bare [DONE].
      `event: done\ndata: ${JSON.stringify({ answer: "Hello, world.", route: "RAG" })}\n\n`,
    ];
    const stream = new ReadableStream({
      async start(controller) {
        for (const f of frames) {
          controller.enqueue(encoder.encode(f));
          await delay(10);
        }
        controller.close();
      },
    });
    return new HttpResponse(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }),
];

// Reusable error-path overrides for negative tests:
//   server.use(chatError(500))  /  server.use(chatError(403, "Quota exceeded"))
export function chatError(status: number, detail = "Backend error") {
  return http.post(`${API}/chat`, () =>
    HttpResponse.json({ detail }, { status }),
  );
}
```

```ts
// test/msw/server.ts  (Node — consumed by test/setup.ts / Vitest)
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
```

```ts
// test/msw/browser.ts  (Browser — for an MSW-backed dev harness used by Playwright)
import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";

export const worker = setupWorker(...handlers);
```

> Browser wiring for E2E (how `worker.start()` is mounted) is described in §6 — it is started from a
> client component gated on `NEXT_PUBLIC_API_MOCKING==='enabled'` so production bundles never include it.

### Task 5 — Representative unit/component tests

**Goal:** real, copy-pasteable examples for the highest-value units; list the remainder.

**Files:** the `*.test.ts(x)` files in the tree above. (M1's store/blocking and M2's `parseSSE` tests are
moved/extended here so all test files live beside their subjects.)

**5a. Zustand store — `features/chat/store/chat.store.test.ts`**

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { act } from "@testing-library/react";
import { useChatStore } from "@/features/chat/store/chat.store";

const reset = () => useChatStore.getState().resetSession?.();

describe("chat.store", () => {
  beforeEach(() => {
    // Reset Zustand state between tests (store is a module singleton).
    act(() => reset());
  });

  it("appends a user message then an assistant message", () => {
    act(() => {
      useChatStore.getState().addUserMessage("hello");
      useChatStore.getState().addAssistantMessage({
        content: "hi there",
        route: "RAG",
        sourcesCount: 2,
      });
    });
    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ role: "user", content: "hello" });
    expect(messages[1]).toMatchObject({
      role: "assistant",
      content: "hi there",
      route: "RAG",
      sourcesCount: 2,
    });
  });

  it("resetSession clears messages and draft", () => {
    act(() => {
      useChatStore.getState().addUserMessage("keep nothing");
      useChatStore.getState().setDraft("typing...");
      reset();
    });
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().draft).toBe("");
  });

  it("toggles webSearchAllowed", () => {
    const before = useChatStore.getState().webSearchAllowed;
    act(() => useChatStore.getState().toggleWebSearch());
    expect(useChatStore.getState().webSearchAllowed).toBe(!before);
  });
});
```

**5b. `use-blocking-chat` hook — `features/chat/hooks/use-blocking-chat.test.tsx`**

Wraps the hook in a `QueryClientProvider`; MSW serves `/chat`; asserts the mutation writes the
synthesized assistant message into the store.

```tsx
import { describe, expect, it } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useBlockingChat } from "@/features/chat/hooks/use-blocking-chat";
import { useChatStore } from "@/features/chat/store/chat.store";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useBlockingChat", () => {
  it("sends a message and writes the assistant reply into the store", async () => {
    act(() => useChatStore.getState().resetSession?.());
    const { result } = renderHook(() => useBlockingChat(), { wrapper });

    await act(async () => {
      await result.current.sendMessage("what is RAG?");
    });

    await waitFor(() => {
      const msgs = useChatStore.getState().messages;
      const assistant = msgs.find((m) => m.role === "assistant");
      expect(assistant?.content).toBe("Echo: what is RAG?");
      expect(assistant?.route).toBeDefined();
    });
  });
});
```

**5c. `parseSSE` — `lib/sse/parser.test.ts`** (extends M2)

```ts
import { describe, expect, it } from "vitest";
import { parseSSE } from "@/lib/sse/parser";

// Build a ReadableStream<Uint8Array> from raw SSE chunks (possibly split mid-frame).
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const out: { event: string; data: string }[] = [];
  for await (const evt of parseSSE(stream)) out.push(evt);
  return out;
}

describe("parseSSE", () => {
  it("parses status + token events", async () => {
    const events = await collect(
      streamOf([
        'event: status\ndata: {"stage":"routing"}\n\n',
        'event: token\ndata: {"text":"Hi"}\n\n',
      ]),
    );
    expect(events.map((e) => e.event)).toEqual(["status", "token"]);
  });

  it("reassembles a frame split across chunk boundaries", async () => {
    const events = await collect(
      streamOf(['event: token\ndata: {"text', '":"split"}\n\n']),
    );
    expect(events).toHaveLength(1);
    expect(JSON.parse(events[0].data)).toEqual({ text: "split" });
  });

  it("handles multi-line data and terminates on [DONE]", async () => {
    const events = await collect(
      streamOf(["data: line1\ndata: line2\n\n", "data: [DONE]\n\n"]),
    );
    expect(events[0].data).toBe("line1\nline2");
    // [DONE] terminates the generator (no event yielded for the sentinel).
    expect(events).toHaveLength(1);
  });
});
```

**5d. `chat-message` component — `features/chat/components/chat-message.test.tsx`**

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMessage } from "@/features/chat/components/chat-message";
import type { Message } from "@/types";

const base: Message = {
  id: "m1",
  role: "assistant",
  content: "The answer is **42**.",
  route: "RAG",
  sourcesCount: 3,
  timestamp: new Date("2026-01-01T00:00:00Z"),
};

describe("ChatMessage", () => {
  it("renders assistant markdown content", () => {
    render(<ChatMessage message={base} />);
    expect(screen.getByText(/the answer is/i)).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument(); // bold rendered
  });

  it("shows the route badge for assistant messages", () => {
    render(<ChatMessage message={base} />);
    expect(screen.getByText(/RAG/)).toBeInTheDocument();
  });

  it("omits the route badge for user messages", () => {
    render(
      <ChatMessage
        message={{ ...base, role: "user", route: undefined, content: "hi" }}
      />,
    );
    expect(screen.queryByText(/RAG/)).not.toBeInTheDocument();
  });
});
```

**5e. `chat-input` component — `features/chat/components/chat-input.test.tsx`** (behavior with `userEvent`)

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInput } from "@/features/chat/components/chat-input";

describe("ChatInput", () => {
  it("submits typed text and clears the field", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatInput onSend={onSend} disabled={false} />);

    const box = screen.getByRole("textbox");
    await user.type(box, "hello world");
    await user.keyboard("{Enter}"); // Enter sends; Shift+Enter newlines

    expect(onSend).toHaveBeenCalledWith("hello world");
    expect(box).toHaveValue("");
  });

  it("does not submit when disabled", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatInput onSend={onSend} disabled />);
    await user.type(screen.getByRole("textbox"), "x{Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });
});
```

**Remaining unit/component tests to author (same patterns):**
- `thinking-steps.test.tsx` — renders synthesized `steps[]`, expand/collapse toggles content
  (`getByRole("button")` → click → assert region visible).
- `sources-panel.test.tsx` — renders `sourcesCount`/sources, collapsed by default.
- `route-badge.test.tsx` — one assertion per `RouteType` → correct label/variant.
- `message-actions.test.tsx` — copy button calls `navigator.clipboard.writeText` (mocked in setup),
  retry invokes the callback.
- Optional: a `use-reduced-motion.test.ts` asserting it reads `matchMedia('(prefers-reduced-motion: reduce)')`.
- **(M10-owned)** The rich-component renderer tests (`features/chat/components/rich/*.test.tsx`) and the
  `component.schemas` discriminated-union tests are authored in **M10**, not here. M5's MSW SSE stub
  already emits a `component` event (Task 4) so M10's renderers and the streaming E2E have a contract to
  exercise; M5 does not test the renderers themselves.

### Task 6 — npm scripts

**Goal:** stable test entrypoints for local dev and CI.

**Files:** `package.json` (`scripts`).

```jsonc
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage",
    "test:e2e": "playwright test"
  }
}
```

(`typecheck` is added here if M0 didn't already define it; CI calls `npm run typecheck`.)

### Task 7 — `playwright.config.ts`

**Goal:** Playwright builds and serves the real app with MSW enabled, then runs the chromium suite.

**Files:** `playwright.config.ts` (NEW).

```ts
// playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

const PORT = 3000;
const baseURL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI, // no stray .only() in CI
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry", // trace viewer artifact on flake
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Build once, then serve the production app with MSW turned on in the browser.
  webServer: {
    command: "npm run build && npm run start",
    url: baseURL,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    env: {
      // Turn on the in-browser MSW worker (see §6) and pin the API base so
      // handlers' "*/api" wildcard matches the app's requests.
      NEXT_PUBLIC_API_MOCKING: "enabled",
      NEXT_PUBLIC_API_URL: "http://localhost:3000/api",
      NEXT_PUBLIC_FEATURE_STREAMING: "false", // default flow is blocking
    },
  },
});
```

> If the in-browser worker proves flaky under the production server, fall back to
> `command: "npm run dev"` (`reuseExistingServer` already differs by CI) — dev is slower but the worker
> mounts identically. The production-server path is preferred because it tests the artifact we ship.

### Task 8 — `e2e/chat.spec.ts`

**Goal:** the full core flow against MSW: load → send → assistant renders → upload → theme toggle → reset.

**Files:** `e2e/chat.spec.ts` (NEW).

```ts
// e2e/chat.spec.ts
import { test, expect } from "@playwright/test";

test.describe("core chat flow (MSW-mocked backend)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    // Empty state is visible on load.
    await expect(
      page.getByRole("heading", { name: /agentic rag|start a conversation/i }),
    ).toBeVisible();
  });

  test("send → assistant renders → upload → theme toggle → reset", async ({
    page,
  }) => {
    // 1) Send a message.
    const input = page.getByRole("textbox");
    await input.fill("What is retrieval-augmented generation?");
    await input.press("Enter");

    // User bubble appears immediately.
    await expect(
      page.getByText("What is retrieval-augmented generation?"),
    ).toBeVisible();

    // 2) Assistant reply (MSW echoes) + route badge render.
    await expect(
      page.getByText(/Echo: What is retrieval-augmented generation\?/),
    ).toBeVisible();
    await expect(page.getByText(/RAG/)).toBeVisible();

    // 3) Upload a file (MSW /upload responds "uploaded").
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("hello from a test document"),
    });
    // A success toast / filename chip confirms the upload.
    await expect(page.getByText(/notes\.txt|uploaded/i)).toBeVisible();

    // 4) Theme toggle: html.dark flips.
    const html = page.locator("html");
    const wasDark = await html.evaluate((el) => el.classList.contains("dark"));
    await page.getByRole("button", { name: /toggle theme|theme/i }).click();
    // next-themes may render a menu; pick the opposite mode if present.
    const target = wasDark ? /light/i : /dark/i;
    const menuItem = page.getByRole("menuitem", { name: target });
    if (await menuItem.isVisible().catch(() => false)) await menuItem.click();
    await expect
      .poll(() => html.evaluate((el) => el.classList.contains("dark")))
      .toBe(!wasDark);

    // 5) Reset session: messages clear back to empty state.
    await page.getByRole("button", { name: /reset|new chat|clear/i }).click();
    await expect(
      page.getByText(/Echo: What is retrieval-augmented generation\?/),
    ).toHaveCount(0);
  });
});
```

> Selectors prefer accessible roles/names. If M3's components don't yet expose these names, add
> `aria-label`s in those components (allowed under M3 polish) rather than reaching for brittle CSS/test-id
> selectors here. Where a stable hook is unavoidable, use `data-testid` consistently.

### Task 9 — `next.config.ts`: enable standalone output

**Goal:** make `next build` emit `.next/standalone` (self-contained server + minimal traced
`node_modules` + `server.js`).

**Files:** `next.config.ts` (EDIT).

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
};

export default nextConfig;
```

### Task 10 — Slim multi-stage `Dockerfile`

**Goal:** replace the fat image with deps → builder → runner; the runner ships only the standalone
bundle + static + public, runs non-root, boots `node server.js`.

**Files:** `Dockerfile` (REWRITE).

```dockerfile
# syntax=docker/dockerfile:1

# ---- 1) deps: install full deps once, cached on lockfile ----
FROM node:22.11.0-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

# ---- 2) builder: build with output:'standalone' ----
FROM node:22.11.0-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- 3) runner: minimal runtime, non-root ----
FROM node:22.11.0-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Non-root user.
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

# public/ is NOT bundled into standalone — copy it explicitly.
COPY --from=builder /app/public ./public
# Standalone server + its traced minimal node_modules (includes server.js).
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# Static assets (CSS/JS/chunks) — also NOT bundled into standalone.
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# server.js lives at the bundle root after the standalone copy above.
CMD ["node", "server.js"]
```

> **Critical:** `output:'standalone'` traces *server* modules only — it does **not** copy `public/` or
> `.next/static`. Both copies above are mandatory or you ship an app with no CSS/JS and 404'd assets.
> `HOSTNAME=0.0.0.0` is required so the standalone server binds outside the container.

### Task 11 — `.dockerignore`

**Goal:** keep the build context tiny and prevent local `node_modules`/`.next`/secrets from poisoning
the image.

**Files:** `.dockerignore` (NEW).

```gitignore
node_modules
.next
out
coverage
playwright-report
test-results
.git
.github
.husky
.vscode
Dockerfile
.dockerignore
npm-debug.log*
*.tsbuildinfo
.env*
docs
e2e
test
**/*.test.ts
**/*.test.tsx
**/*.spec.ts
**/*.spec.tsx
```

### Task 12 — `.gitignore` additions

**Goal:** ignore Playwright artifacts and the MSW-generated worker.

**Files:** `.gitignore` (EDIT — append).

```gitignore
# playwright
/test-results
/playwright-report
/blob-report
/.playwright

# msw (generated worker — regenerated via `msw init`)
/public/mockServiceWorker.js
```

### Task 13 — Harden `.github/workflows/ci.yml`

**Goal:** extend the M0 skeleton into a fast-fail, parallel-where-safe pipeline with shared npm cache.

**Files:** `.github/workflows/ci.yml` (HARDEN).

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  NODE_VERSION: "22.11.0"
  # Dummy/public env so Zod env validation (lib/env.ts) doesn't fail at build.
  NEXT_PUBLIC_API_URL: "http://localhost:3000/api"
  NEXT_TELEMETRY_DISABLED: "1"

jobs:
  install:
    name: Install (prime cache)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
      - run: npm ci

  lint:
    name: Lint + format
    runs-on: ubuntu-latest
    needs: install
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm }
      - run: npm ci
      - run: npm run lint
      - run: npx prettier --check .

  typecheck:
    name: Typecheck
    runs-on: ubuntu-latest
    needs: install
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm }
      - run: npm ci
      - run: npm run typecheck

  unit:
    name: Unit + coverage
    runs-on: ubuntu-latest
    needs: [lint, typecheck]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm }
      - run: npm ci
      - run: npm run test:coverage
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: coverage, path: coverage/ }

  build:
    name: Next build (standalone)
    runs-on: ubuntu-latest
    needs: [lint, typecheck]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm }
      - run: npm ci
      - run: npm run build
      - uses: actions/upload-artifact@v4
        with:
          name: next-build
          path: |
            .next/standalone
            .next/static
          retention-days: 1

  e2e:
    name: Playwright E2E (vs MSW)
    runs-on: ubuntu-latest
    needs: [unit, build]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm }
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npx msw init public/ --save # regenerate ignored worker
      - run: npm run test:e2e
        env:
          NEXT_PUBLIC_API_MOCKING: "enabled"
          NEXT_PUBLIC_FEATURE_STREAMING: "false"
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: playwright-report, path: playwright-report/ }

  docker:
    name: Docker build (smoke)
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Build image
        uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: rag-frontend:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - name: Boot smoke test
        run: |
          docker run -d --name web -p 3000:3000 \
            -e NEXT_PUBLIC_API_URL=http://localhost:3000/api rag-frontend:ci
          for i in $(seq 1 30); do
            if curl -fsS http://localhost:3000 >/dev/null; then echo "up"; exit 0; fi
            sleep 2
          done
          echo "server did not boot"; docker logs web; exit 1
```

> `npm ci` repeats per job because each runner is fresh; `cache: npm` makes it fast. The `docker` job is
> optional/non-blocking-by-policy — keep it required only once it's reliably green.

---

## 6. MSW Strategy

**One contract, two runtimes.** `test/msw/handlers.ts` is the single source of truth. It is consumed by:

- **Node (Vitest):** `test/msw/server.ts` → `setupServer(...handlers)`, started in `test/setup.ts`
  (`server.listen({ onUnhandledRequest: "error" })`). Any request not covered by a handler **fails the
  test**, so the contract can't silently drift. MSW v2 intercepts the global `fetch` (Node 18+/undici),
  so `lib/api/http-client.ts` runs for real — we test our actual request building, not a stub.

- **Browser (Playwright E2E):** `test/msw/browser.ts` → `setupWorker(...handlers)`. The worker is mounted
  by a small client-only initializer gated on an env flag, so it is tree-shaken out of normal builds:

  ```ts
  // app/providers.tsx (or a dedicated mock-init client component) — sketch
  if (
    typeof window !== "undefined" &&
    process.env.NEXT_PUBLIC_API_MOCKING === "enabled"
  ) {
    const { worker } = await import("@/test/msw/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  }
  ```

  Playwright's `webServer.env` sets `NEXT_PUBLIC_API_MOCKING=enabled` (Task 7), so the same handlers that
  back unit tests now intercept the app's `fetch` **inside the browser**. The E2E suite therefore exercises
  the real React tree, real router, real store, real `http-client` — only the network is mocked, and it's
  mocked with the identical contract the unit tests use.

**Streaming endpoint.** The SSE stub returns a `ReadableStream` over a `text/event-stream` response
(Task 4). In Node this drives `parseSSE`/`useStreamingChat` tests directly; in the browser it lets us flip
`NEXT_PUBLIC_FEATURE_STREAMING=true` in a dedicated E2E and watch tokens/`status` events animate the
thinking-steps panel — all without a backend. The default E2E run keeps streaming **off** (blocking path)
because that's today's shipping behavior.

**Env wiring.** Handlers match the wildcard base `*/api`, so they intercept whether the app calls the
absolute Render URL, `http://localhost:3000/api`, or a relative `/api`. Tests/CI/Playwright pin
`NEXT_PUBLIC_API_URL` to a stable value; nothing ever reaches a real server.

---

## 7. Testing & Verification

**Local gate (must all pass):**
```bash
npm run lint
npx prettier --check .
npm run typecheck
npm run test:coverage     # unit/component + v8 coverage >= thresholds
npm run build             # emits .next/standalone
npm run test:e2e          # Playwright vs MSW (builds+serves, runs chromium)
```

**Coverage targets.** Floors enforced in `vitest.config.ts`: statements 70 / branches 65 / functions 70 /
lines 70. Hot modules (`chat.store`, `use-blocking-chat`, `parseSSE`) should sit well above floor. Ratchet
upward as suites grow; never lower to pass.

**Docker — small image that boots via `node server.js`:**
```bash
docker build -t rag-frontend:local .
docker images rag-frontend:local            # confirm the size drop (see note below)
docker run --rm -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=https://python-agentic-rag-backend.onrender.com/api \
  rag-frontend:local
# in another shell:
curl -fsS http://localhost:3000 >/dev/null && echo "OK: server up"
```
The container must serve the homepage and start with `node server.js` (not `npm run start`).

**Image-size expectation (before/after).** The current Dockerfile ships a full production `node_modules`
plus source: expect roughly **800 MB – 1.2 GB**. The standalone runner ships only Next's traced minimal
`node_modules` + `server.js` + `.next/static` + `public` on `node:22-alpine`: expect roughly
**150 – 250 MB** — a multiple-fold reduction. The exact number depends on the dependency graph; the point
is a sharp drop and a faster cold start.

**E2E green against MSW.** `e2e/chat.spec.ts` passes: empty state on load, user+assistant bubbles, route
badge, upload confirmation, theme class flip, and a clean reset — zero real network calls (MSW
`onUnhandledRequest` would otherwise surface them).

**CI green.** All jobs (`lint`, `typecheck`, `unit`, `build`, `e2e`, and the `docker` smoke) pass on the
branch; coverage and Playwright HTML report are uploaded as artifacts.

---

## 8. Risks & Gotchas

- **MSW v2 API + Node fetch.** v2 changed the API (`http.post`, `HttpResponse`, resolver signature). Pin
  `msw@^2`; use the v2 imports shown. MSW v2 needs the global `fetch`/`Request`/`Response` (Node 18+);
  Vitest's jsdom env provides them, but if you swap to the `node` env ensure undici globals exist.
- **jsdom gaps.** `matchMedia`, `ResizeObserver`, `IntersectionObserver`, and `scrollIntoView` are **not**
  implemented by jsdom — Task 3 polyfills all four. Missing `matchMedia` crashes `next-themes` and
  `use-reduced-motion`; missing `ResizeObserver` crashes `react-textarea-autosize` and framer-motion's
  layout measurement.
- **framer-motion (M4) in jsdom.** Animations don't actually run in jsdom; assert **end state** (final
  text/visibility), never intermediate frames. `AnimatePresence` exit animations can delay unmount —
  prefer `findBy*`/`waitFor` over synchronous `queryBy*` when asserting removal, or test the reduced-motion
  path. Keep `css: false` in Vitest so Tailwind/PostCSS never runs in unit tests.
- **Playwright in CI.** Browsers must be installed (`npx playwright install --with-deps chromium`) and the
  run is headless by default. `forbidOnly: true` under CI catches stray `.only()`. Use `workers: 1` in CI
  if the single MSW-backed server can't handle parallel pages; enable `retries` + `trace: on-first-retry`
  for flake triage.
- **Standalone missing `public`/`static`.** The #1 standalone footgun: `output:'standalone'` does **not**
  copy `public/` or `.next/static`. If the homepage renders but has no styles/JS or 404s `/_next/static/*`,
  you forgot one of the two explicit `COPY` lines in the runner stage.
- **Next 16 standalone server path & host.** After copying `.next/standalone` to the WORKDIR root,
  `server.js` is at the root → `CMD ["node","server.js"]`. The standalone server reads `PORT` and
  `HOSTNAME`; set `HOSTNAME=0.0.0.0` or it may bind to localhost only and be unreachable from outside the
  container.
- **Env at build vs runtime.** `NEXT_PUBLIC_*` vars are **inlined at build time**. `NEXT_PUBLIC_API_URL`
  baked during `docker build` is what the client uses; passing a different `-e NEXT_PUBLIC_API_URL` only to
  `docker run` will **not** change already-inlined client values. For multi-environment images either build
  per-environment or pass `--build-arg` and re-expose at build time. The same applies to
  `NEXT_PUBLIC_API_MOCKING` — it must be set for the E2E build, not just at run.
- **MSW worker file in git.** `public/mockServiceWorker.js` is generated and git-ignored; CI regenerates it
  with `npx msw init public/ --save` before E2E. Forgetting this step makes the browser worker fail to
  register and the E2E run hits real network (and fails on `onUnhandledRequest`).
- **Zustand store singletons in tests.** The store is a module singleton; reset it in `beforeEach`
  (`resetSession()`), or state leaks across tests. `clearMocks`/`restoreMocks` in Vitest config reset spies
  but not store state.

---

## 9. Exit Criteria (checkable)

- [ ] `npm run test` runs the Vitest suite (store, `use-blocking-chat`, `parseSSE`, `chat-message`,
      `chat-input`, `thinking-steps`, `sources-panel`, `route-badge`, `message-actions`) and is green.
- [ ] `npm run test:coverage` meets the configured thresholds (≥ 70/65/70/70); coverage report emitted.
- [ ] MSW handlers cover `/chat`, `/upload`, `/cleanup`, and the SSE stub; `onUnhandledRequest: "error"`
      catches any uncovered request in unit tests.
- [ ] `npm run test:e2e` passes `e2e/chat.spec.ts` (load → send → assistant → upload → theme → reset) with
      zero real network calls.
- [ ] `next.config.ts` sets `output: "standalone"`; `npm run build` emits `.next/standalone`.
- [ ] `docker build -t rag-frontend:local .` succeeds; `docker run` serves the homepage; container starts
      with `node server.js` as a non-root user.
- [ ] Final image is dramatically smaller than the pre-M5 image (target ~150–250 MB vs ~800 MB–1.2 GB).
- [ ] `.dockerignore` present; `.gitignore` ignores Playwright artifacts + the MSW worker.
- [ ] CI (`.github/workflows/ci.yml`) runs install → lint → typecheck → unit+coverage → build → e2e
      (+ optional docker smoke) and is **green on the branch**; coverage + Playwright report uploaded.
- [ ] No product behavior changed; `NEXT_PUBLIC_FEATURE_STREAMING` remains `false` for the default flow.

---

## 10. Commit Plan

Milestone-sized commits on `claude/frontend-improvements-planning-1aX4u` (Conventional Commits):

1. `chore(test): add vitest + RTL + jsdom + msw + playwright dev deps and scripts` — Task 1, 6.
2. `test(config): vitest.config.ts (jsdom, @ alias, v8 coverage thresholds) + test/setup.ts polyfills` — Task 2, 3.
3. `test(msw): shared handlers (chat/upload/cleanup + SSE stub), node server, browser worker` — Task 4.
4. `test(unit): store, use-blocking-chat, parseSSE, and chat component suites` — Task 5.
5. `test(e2e): playwright.config.ts + e2e/chat.spec.ts core flow against MSW` — Task 7, 8.
6. `build(next): enable output:'standalone'` — Task 9.
7. `build(docker): slim multi-stage Dockerfile (standalone runner, non-root) + .dockerignore` — Task 10, 11.
8. `chore(git): ignore playwright artifacts and generated msw worker` — Task 12.
9. `ci: harden pipeline — lint, typecheck, unit+coverage, build, playwright e2e, docker smoke` — Task 13.

Push: `git push -u origin claude/frontend-improvements-planning-1aX4u`. Open a PR; merge once CI is green.
