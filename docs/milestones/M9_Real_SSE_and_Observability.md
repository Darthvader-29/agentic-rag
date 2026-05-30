# M9 — Real SSE Activation + Rich Markdown & Observability (Backend Phase P6)

This is the capstone milestone: the dark-launched streaming architecture built in M2 (SSE parser + strategy + `useChat` facade) and animated in M4 (caret + thinking-steps motion) finally **switches on** against the real LangGraph/SSE backend shipped in Phase 6. The only code change required to enable streaming itself is flipping `NEXT_PUBLIC_FEATURE_STREAMING=true` — everything else here is *reconciliation* against the real P6 wire format, a `next.config.ts` images allowlist so rich Markdown images render, and *opt-in* observability (Sentry + analytics) that ships dark when env/DSN are unset.

**Status:** backend-dependent (needs P6 streaming shipped — see `Python-Agentic-RAG-Backend/docs/07_Phase6_LangGraph_and_Streaming.md`) / depends on (M2 streaming core, M4 motion layer) / capstone of M0–M9.

---

## 1. Objective & Scope

### In scope
- **Flip the streaming flag** (`NEXT_PUBLIC_FEATURE_STREAMING=true`) and **verify the dark-launched pipeline end-to-end** against the real P6 SSE backend: `status` events drive live thinking-steps (with M4 stagger), `token` events stream the body with the blinking caret, `done` finalizes route badge + sources.
- **Reconcile** `lib/sse/parser.ts` / `lib/sse/types.ts` / `features/chat/api/chat.schemas.ts` with the **exact P6 event shapes** where they differ from the contract M2 designed against (status stage strings, `done`-carried route/answer, `error` event).
- **Rich-markdown image allowlist**: add `images.remotePatterns` to `next.config.ts` so `![alt](url)` images emitted by the synthesis node render through `next/image` / the Markdown renderer.
- **Sentry enablement**: install + configure `@sentry/nextjs`, gated by `SENTRY_DSN` (no-op when unset), with `tracesSampleRate`, event filtering, source-map upload, and a test-error trigger.
- **Analytics enablement**: a provider component (Vercel Web Analytics or PostHog) mounted in `app/providers.tsx`, gated by an env key (ships dark when unset).
- **Env schema additions** for the new optional vars in `lib/env.ts` (Zod, all `.optional()`) + `.env.example`.

### Out of scope (already delivered — do **not** rebuild)
- **The SSE parser, the streaming strategy, the `useChat` facade switch** — built and unit-tested in **M2** (`lib/sse/parser.ts`, `lib/sse/stream-chat.ts`, `features/chat/hooks/use-streaming-chat.ts`, `use-chat.ts`).
- **The streaming caret, thinking-steps stagger/expand-collapse, reduced-motion gate** — built in **M4** (`features/chat/components/thinking-steps.tsx`, the caret in `chat-message.tsx`, `hooks/use-reduced-motion.ts`).
- **The store actions** `appendContent` / `pushStep` / `finalize` and the unified `Message` shape (`steps`/`sources`/`status`) — built in **M2/M1** (`features/chat/store/chat.store.ts`).
- **Backend SSE itself** — P6 (`07_Phase6...md`). M9 consumes it; it does not implement it.

This milestone is **mostly activation + verification + observability wiring**, not building streaming from scratch.

---

## 2. Production SSE Contract (P6)

Source of truth: `Python-Agentic-RAG-Backend/docs/07_Phase6_LangGraph_and_Streaming.md` — **Appendix C (event-type catalog + SSE helper)** and **Appendix F (parity / event-sequence test)**.

### Wire format
The backend emits over `Content-Type: text/event-stream` using the hand-rolled framing helper (07_Phase6, Appendix C):

```python
def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
```

So every frame is exactly an `event:` line + a single-line JSON `data:` line, terminated by a blank line (`\n\n`). **There is no multi-line `data:` and no `[DONE]` sentinel** — completion is signalled by a typed `event: done`.

### Event catalog (verbatim from 07_Phase6 Appendix C)

| `event:` | `data:` JSON payload | Emitted when |
|----------|----------------------|--------------|
| `status` | `{"stage": "routing"}` | supervisor node starts (route + relevance decision) |
| `status` | `{"stage": "searching web"}` | web node starts |
| `status` | `{"stage": "retrieving"}` | vector node starts |
| `status` | `{"stage": "synthesizing"}` | synthesis node starts |
| `token`  | `{"text": "..."}` | each generated chunk (or one final chunk if the provider can't stream) |
| `done`   | `{"answer": "...", "route": {...}}` | stream complete; final answer + route decision |
| `error`  | `{"detail": "..."}` | any node raised; closes the stream cleanly |

### Status stage ordering
From Appendix F's event-sequence assertion (`stages == ["routing", "retrieving", "synthesizing"]`) and the endpoint sketch (Appendix C), the observable stage progression is:

```
routing → (retrieving | searching web | both)* → synthesizing → [token]* → done
```

- `routing` is always first (supervisor).
- The middle stage(s) depend on the route decision: `retrieving` (vectorstore), `searching web` (web_search), or **both** on a parallel fan-out (07_Phase6 Appendix A — disjoint `web_result`/`vector_result` keys). When both branches run, **both** `status` events arrive (order not guaranteed between them).
- `synthesizing` precedes the `token` stream.
- The stream terminates with **exactly one** `done` *or* one `error`.

### Where route + sources are delivered
**This is the load-bearing detail and the biggest delta from M2's design.** Route and the final answer arrive in the **`done` event payload** (`{"answer", "route"}`), *not* as standalone events and *not* as a trailing `status`:

- **`route`** is a backend `RouteDecision` object: `{"destination": "web_search" | "vectorstore", "relevant": bool}` (07_Phase6, Appendix B). It is **not** the frontend's flat `RouteType` enum (`"RAG" | "WEB" | "DIRECT" | ...`). M9 must **map** the backend object → the frontend badge label (see Task 6a).
- **Sources / `context_count`** are **not** explicitly enumerated in the P6 SSE catalog. The retrieval node concatenates context (`vector_result`/`context`) but the streaming contract surfaces only `route` + `answer` in `done`. **Reconciliation decision (Task 6a):** derive the thinking-step / sources signal from the **status events actually observed** (a `retrieving` event ⇒ the vector path ran ⇒ surface a "retrieved context" step), and treat a precise numeric `sourcesCount` as **best-effort/optional** until the backend adds a `sources` field to `done`. Do **not** invent a count. The blocking path's synthesized `context_count` (M2) has no streaming equivalent yet; the UI must render gracefully when `sourcesCount` is `undefined`.

### Delta vs. the contract M2 designed against
M2 (per `FRONTEND_IMPROVEMENT_PLAN.md` §"SSE design") designed `parseSSE` to handle `event:`/`data:` with **multi-line data, partial buffer, and a `[DONE]` sentinel**, and `streamChat(payload, {onStatus, onToken, onError})`. Reconcile as follows:

| M2 assumption | Real P6 behaviour | Reconciliation |
|---|---|---|
| `[DONE]` sentinel terminates stream | Typed `event: done` with `{answer, route}` payload | Add an `onDone(answer, route)` callback / `done` branch; keep `[DONE]` handling as a tolerated no-op (defensive). |
| Status payload shape unspecified | `{"stage": <string>}` with specific strings incl. `"searching web"` (a space) | Pin the stage union; map `"searching web"`/`"retrieving"` → step labels; do not assume an underscore. |
| `onError` only as a transport/parse error | Backend also emits an in-band `event: error` `{"detail"}` mid-stream | Route `event: error` through `onError(new Error(detail))`; finalize the partial message, don't throw past the boundary. |
| Sources arrive somehow | Only `route` + `answer` in `done`; no `sources`/count field | Derive steps from observed `status` events; `sourcesCount` stays optional/undefined. |
| Route is a string enum | `route` is `{destination, relevant}` object | Map object → `RouteType` label in `finalize` (Task 6a). |

These are **small, additive guards** in the parser/types/schemas — not a rewrite. M2's multi-line/partial-buffer handling stays (harmless; robust against chunk boundaries even though P6 data is single-line).

---

## 3. Decisions & Rationale

| Decision | Rationale | Alternatives considered |
|---|---|---|
| **Flag flip is the only activation** | M2 built the strategy switch (`flags.streaming ? useStreamingChat : useBlockingChat`) and M4 built the motion; both strategies write the same `Message` shape through the same store actions, so flipping `NEXT_PUBLIC_FEATURE_STREAMING=true` lights up streaming with **zero component rewrites**. The architecture pays off here. | Conditionals scattered in components (defeats the facade); a parallel streaming UI (duplicate surface). |
| **Observability is opt-in via env/DSN** | Sentry/analytics must **ship dark** when `SENTRY_DSN` / analytics key are unset — no errors, no network calls, no bundle cost beyond a guard. Mirrors the backend's posture (08_Phase7: OTEL/LangSmith gated behind flags, default off in CI; keys as secrets, never logged). | Always-on Sentry (PII risk, noise in dev/CI); build-time-only gating (can't toggle per environment). |
| **Sentry tunneling + source maps for Next 16** | Next 16 uses the `instrumentation`/`instrumentation-client` model; `withSentryConfig` wraps `next.config.ts`, uploads source maps at build, and a `tunnelRoute` proxies events past ad-blockers. Source-map auth token comes from CI secret `SENTRY_AUTH_TOKEN`, never committed. | Manual `@sentry/browser` (loses Next integration, server/edge spans, tunneling); no source maps (unreadable stack traces). |
| **Analytics: Vercel Web Analytics (default), PostHog optional** | Vercel `@vercel/analytics` is zero-config, privacy-friendly, and a one-line `<Analytics/>` component gated by an env flag; PostHog is the swap-in when product analytics/funnels are needed. Either is mounted in `providers.tsx` behind an env key so it ships dark. | GA4 (heavier, consent burden); roll-our-own (no value). |
| **Images allowlist over disabling optimization** | Rich Markdown from the synthesis node contains `![alt](url)` to arbitrary hosts. Use `images.remotePatterns` with an **explicit trusted host allowlist** rather than `unoptimized: true` or a `**` wildcard — keeps optimization + SSRF/abuse surface bounded. | `unoptimized:true` (loses optimization, still no host control); `remotePatterns: [{hostname:'**'}]` (open proxy / SSRF risk — forbidden). |

---

## 4. Pre-Flight Checklist (entry gate — what M2/M4 already delivered)

**Do not flip the flag until every item below is GREEN.** These are the M2/M4 deliverables this milestone activates; treat this as the entry gate.

- [ ] `lib/sse/parser.ts` — `parseSSE(stream)` async generator exists; **unit tests pass** for multi-line `data:`, partial-buffer across chunk boundaries, and `[DONE]` tolerance.
- [ ] `lib/sse/stream-chat.ts` — `streamChat(payload, {signal, onStatus, onToken, onError})` fetches with `Accept: text/event-stream` and iterates `parseSSE`.
- [ ] `features/chat/hooks/use-streaming-chat.ts` — maps `onStatus → pushStep`, `onToken → appendContent`, completion `→ finalize`; `AbortController` wired to `stop()`.
- [ ] `features/chat/hooks/use-chat.ts` — facade reads `flags.streaming` and delegates to `useStreamingChat` **or** `useBlockingChat`; exposes stable `{ messages, isStreaming, sendMessage, stop, retry }`.
- [ ] `features/chat/store/chat.store.ts` — `appendContent(id, chunk)`, `pushStep(id, step)`, `finalize(id, {route, sources})` actions exist and write the unified `Message` (`steps`/`sources`/`status`).
- [ ] `features/chat/components/thinking-steps.tsx` — renders `steps[]` with **M4 stagger / expand-collapse**; honors reduced-motion.
- [ ] `features/chat/components/chat-message.tsx` — renders the **blinking caret** while `status==='streaming'`; transform/opacity-only animation; memoized.
- [ ] `hooks/use-reduced-motion.ts` — reduced-motion gate used by all motion components.
- [ ] `lib/flags.ts` + `lib/env.ts` — `flags.streaming` derives from `env.NEXT_PUBLIC_FEATURE_STREAMING`; Zod-validated.
- [ ] Mock-SSE strategy test (M2) green: with the flag forced on against a mock server, tokens stream and status events populate steps.

If any box is unchecked, **stop** — that is an M2/M4 regression, fix there first.

---

## 5. Target File Tree (delta)

Only the deltas this milestone introduces or touches (assumes M0–M8 landed):

```
.env                              # FLIP: NEXT_PUBLIC_FEATURE_STREAMING=true (local/runtime)
.env.example                      # FLIP + document new SENTRY_/analytics vars
next.config.ts                    # ADD images.remotePatterns; WRAP with withSentryConfig
instrumentation.ts                # NEW (Next 16): registers Sentry server/edge init
instrumentation-client.ts         # NEW (Next 16): Sentry browser init (replaces sentry.client.config.ts)
sentry.server.config.ts           # NEW: server Sentry.init, gated by SENTRY_DSN
sentry.edge.config.ts             # NEW: edge Sentry.init, gated by SENTRY_DSN
app/global-error.tsx              # EDIT: report render errors to Sentry (Sentry.captureException)
app/providers.tsx                 # EDIT: mount <Analytics/> (gated) under existing providers
lib/env.ts                        # EDIT: add SENTRY_DSN, NEXT_PUBLIC_SENTRY_DSN, analytics key (all optional)
lib/observability/sentry.ts       # NEW: shared buildSentryInit(dsn) + isSentryEnabled guard
lib/observability/analytics.tsx   # NEW: <AnalyticsProvider/> wrapper gated by env
app/(debug)/sentry-test/page.tsx  # NEW (dev/staging only): button that throws a test error

# Reconciliation (ONLY if real P6 shapes differ from M2's design — they do; see §2):
lib/sse/parser.ts                 # EDIT: tolerate event: done / error; keep [DONE] no-op
lib/sse/types.ts                  # EDIT: pin SseEvent union to P6 (status/token/done/error)
lib/sse/stream-chat.ts            # EDIT: add onDone(answer, route); route error event → onError
features/chat/api/chat.schemas.ts # EDIT: Zod schemas for status/token/done/error payloads
features/chat/hooks/use-streaming-chat.ts  # EDIT: onDone → finalize(map route, derive sources)
```

No source file outside this list should change. If your repo used `sentry.client.config.ts` in an earlier scaffold, the Next 16 equivalent is `instrumentation-client.ts` (see Task 5).

---

## 6. Tasks (ordered)

> Code below is copy-pasteable. Adjust import aliases only if your `tsconfig` `paths` differ from `@/*`.

### Task 1 — Reconcile SSE types with the real P6 event shapes

**Goal:** make the TypeScript event union and Zod schemas exactly match P6 Appendix C, so the parser/strategy are type-safe against the real wire format.

**Files:** `lib/sse/types.ts`, `features/chat/api/chat.schemas.ts`.

`lib/sse/types.ts`:

```ts
// lib/sse/types.ts
// Mirrors Python-Agentic-RAG-Backend/docs/07_Phase6_LangGraph_and_Streaming.md Appendix C.

/** Backend status stages, verbatim (note the SPACE in "searching web"). */
export type SseStage = "routing" | "retrieving" | "searching web" | "synthesizing";

/** Backend RouteDecision (Appendix B) — NOT the frontend flat RouteType. */
export interface SseRouteDecision {
  destination: "web_search" | "vectorstore";
  relevant: boolean;
}

export type SseEvent =
  | { event: "status"; data: { stage: SseStage } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { answer: string; route: SseRouteDecision | null } }
  | { event: "error"; data: { detail: string } };

export type SseEventName = SseEvent["event"];
```

`features/chat/api/chat.schemas.ts` (add the streaming schemas alongside the existing blocking `ChatResponse` schema; do not remove the blocking one):

```ts
// features/chat/api/chat.schemas.ts  (additions)
import { z } from "zod";

export const sseRouteDecisionSchema = z.object({
  destination: z.enum(["web_search", "vectorstore"]),
  relevant: z.boolean(),
});

export const sseStatusSchema = z.object({
  stage: z.enum(["routing", "retrieving", "searching web", "synthesizing"]),
});
export const sseTokenSchema = z.object({ text: z.string() });
export const sseDoneSchema = z.object({
  answer: z.string(),
  // route may be null if the supervisor short-circuited; tolerate it.
  route: sseRouteDecisionSchema.nullable().optional().default(null),
  // best-effort: backend MAY add a sources/count field later (see §2). Optional today.
  sources: z.array(z.string()).optional(),
  context_count: z.number().int().nonnegative().optional(),
});
export const sseErrorSchema = z.object({ detail: z.string() });

export type SseDonePayload = z.infer<typeof sseDoneSchema>;
```

**Acceptance:** types compile; the `done` schema tolerates a missing/`null` route and absent sources without throwing.

---

### Task 2 — Reconcile the parser + `streamChat` (add `done`/`error`, keep `[DONE]` no-op)

**Goal:** surface the typed `done`/`error` events to the strategy; keep M2's robustness (partial buffer, multi-line, `[DONE]` tolerance).

**Files:** `lib/sse/parser.ts`, `lib/sse/stream-chat.ts`.

In `lib/sse/parser.ts`, the generator already yields `{ event, data }` frames (M2). Ensure unknown/extra events pass through and the `[DONE]` sentinel (if a future backend emits one) is yielded as a recognizable marker rather than crashing. **Minimal guard** — add to the frame-dispatch site:

```ts
// lib/sse/parser.ts  (within the per-frame loop, after splitting event:/data:)
// Defensive: tolerate a bare "[DONE]" data line even though P6 uses event: done.
if (dataRaw.trim() === "[DONE]") {
  yield { event: "done", data: { answer: "", route: null } } satisfies SseEvent;
  continue;
}
// JSON-parse data; on malformed JSON, skip the frame rather than throw mid-stream.
let parsed: unknown;
try {
  parsed = JSON.parse(dataRaw);
} catch {
  continue; // never break the stream on one bad frame
}
yield { event: eventName, data: parsed } as SseEvent;
```

In `lib/sse/stream-chat.ts`, extend the callback surface with `onDone` and route the in-band `error` event:

```ts
// lib/sse/stream-chat.ts
import { parseSSE } from "@/lib/sse/parser";
import {
  sseStatusSchema,
  sseTokenSchema,
  sseDoneSchema,
  sseErrorSchema,
  type SseDonePayload,
} from "@/features/chat/api/chat.schemas";
import type { SseStage } from "@/lib/sse/types";
import { env } from "@/lib/env";

export interface StreamChatHandlers {
  signal: AbortSignal;
  onStatus: (stage: SseStage) => void;
  onToken: (text: string) => void;
  onDone: (payload: SseDonePayload) => void;
  onError: (err: Error) => void;
}

export async function streamChat(
  payload: { message: string; session_id: string; web_search_allowed: boolean },
  { signal, onStatus, onToken, onDone, onError }: StreamChatHandlers,
): Promise<void> {
  try {
    const res = await fetch(`${env.NEXT_PUBLIC_API_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(payload),
      signal,
    });

    if (!res.ok || !res.body) {
      throw new Error(`Backend error: ${res.status}`);
    }

    for await (const frame of parseSSE(res.body)) {
      switch (frame.event) {
        case "status": {
          const { stage } = sseStatusSchema.parse(frame.data);
          onStatus(stage);
          break;
        }
        case "token": {
          const { text } = sseTokenSchema.parse(frame.data);
          onToken(text);
          break;
        }
        case "done": {
          onDone(sseDoneSchema.parse(frame.data));
          return; // terminal
        }
        case "error": {
          const { detail } = sseErrorSchema.parse(frame.data);
          onError(new Error(detail));
          return; // terminal — backend closed the stream cleanly
        }
        default:
          break; // ignore unknown events forward-compatibly
      }
    }
  } catch (err) {
    if ((err as Error)?.name === "AbortError") return; // Stop button — not an error
    onError(err instanceof Error ? err : new Error(String(err)));
  }
}
```

**Acceptance:** unit test — feed a recorded P6 stream (`status×N → token×N → done`) and assert `onStatus`/`onToken`/`onDone` fire in order; an injected `event: error` calls `onError` and stops; an `AbortError` is swallowed.

---

### Task 3 — Map `done` → `finalize` in the streaming strategy (route mapping + sources derivation)

**Goal:** translate the backend `RouteDecision` object and observed status events into the frontend's unified `Message` (route badge label + best-effort sources), via the existing `finalize` store action.

**Files:** `features/chat/hooks/use-streaming-chat.ts`, plus a small mapper.

Add a route mapper (co-locate in `features/chat/api/chat.schemas.ts` or a `route.ts` util):

```ts
// features/chat/lib/map-route.ts
import type { SseRouteDecision } from "@/lib/sse/types";
import type { RouteType } from "@/types";

/**
 * Map the backend RouteDecision (07_Phase6 Appendix B) + observed stages to the
 * frontend badge label. The backend object only distinguishes web_search vs
 * vectorstore; if BOTH a "retrieving" and "searching web" status fired this turn,
 * surface the combined label.
 */
export function mapRoute(
  decision: SseRouteDecision | null,
  observed: { retrieved: boolean; searchedWeb: boolean },
): RouteType {
  if (observed.retrieved && observed.searchedWeb) return "WEB+RAG";
  if (decision?.destination === "web_search" || observed.searchedWeb) return "WEB";
  if (decision?.destination === "vectorstore" || observed.retrieved) return "RAG";
  return "DIRECT";
}
```

In `use-streaming-chat.ts`, accumulate observed stages and wire callbacks to the store actions (M2). Sketch:

```ts
// features/chat/hooks/use-streaming-chat.ts  (callback wiring — strategy already exists from M2)
import { mapRoute } from "@/features/chat/lib/map-route";

// inside sendMessage, after creating the assistant message `id` and AbortController:
const observed = { retrieved: false, searchedWeb: false };

await streamChat(payload, {
  signal: controller.signal,
  onStatus: (stage) => {
    if (stage === "retrieving") observed.retrieved = true;
    if (stage === "searching web") observed.searchedWeb = true;
    pushStep(id, stage); // M4 ThinkingSteps renders this live with stagger
  },
  onToken: (text) => appendContent(id, text), // body grows + blinking caret (M4)
  onDone: ({ answer, route, sources, context_count }) => {
    // If tokens were dropped/none streamed, fall back to the full answer.
    finalize(id, {
      route: mapRoute(route, observed),
      // best-effort: prefer an explicit count if the backend ever sends one (§2)
      sources: sources ?? undefined,
      sourcesCount: context_count ?? sources?.length,
      fallbackContent: answer, // store uses this only if streamed content is empty
    });
  },
  onError: (err) => {
    // finalize the partial message in an error state; surface a toast.
    finalize(id, { route: "ERROR", error: err.message });
  },
});
```

> The exact `finalize` signature is M2's; this task only feeds it mapped values. If M2's `finalize` doesn't yet accept `fallbackContent`/`error`, that is the **one** small additive change permitted to the store action here (additive, optional params — no breaking change to the blocking path).

**Acceptance:** a streamed turn ends with the correct route badge; an error mid-stream finalizes a visible error message and does not throw past the hook; `sourcesCount` is `undefined` (no fabricated number) when the backend sends none.

---

### Task 4 — Flip `NEXT_PUBLIC_FEATURE_STREAMING=true`

**Goal:** activate streaming. This is the only change needed to switch strategies — the facade (M2) does the rest.

**Files:** `.env`, `.env.example`.

`.env` (local/runtime) and `.env.example`:

```bash
# .env / .env.example
NEXT_PUBLIC_API_URL=https://python-agentic-rag-backend.onrender.com/api
NEXT_PUBLIC_FEATURE_STREAMING=true   # M9: real SSE backend (P6) is live — switch ON
```

Confirm `lib/flags.ts` routes correctly (no edit expected — verification only):

```ts
// lib/flags.ts (existing, from M0/M2) — confirm this shape
import { env } from "@/lib/env";
export const flags = {
  streaming: env.NEXT_PUBLIC_FEATURE_STREAMING === true,
  // ...auth, byok, presignedUpload
} as const;
```

And the facade:

```ts
// features/chat/hooks/use-chat.ts (existing, from M2) — confirm the switch
import { flags } from "@/lib/flags";
export function useChat() {
  return flags.streaming ? useStreamingChat() : useBlockingChat();
}
```

**Acceptance:** with the flag on, `useChat` returns the streaming strategy; with it off (regression guard), the blocking strategy still works unchanged.

---

### Task 5 — Install + configure `@sentry/nextjs` (gated by `SENTRY_DSN`, Next 16 model)

**Goal:** opt-in error + performance monitoring that **no-ops when `SENTRY_DSN` is unset**, with source maps and tunneling, on the Next 16 instrumentation model.

**Files:** `next.config.ts`, `instrumentation.ts`, `instrumentation-client.ts`, `sentry.server.config.ts`, `sentry.edge.config.ts`, `app/global-error.tsx`, `lib/observability/sentry.ts`, `app/(debug)/sentry-test/page.tsx`.

Install:

```bash
npm install @sentry/nextjs
```

Shared guard + init builder:

```ts
// lib/observability/sentry.ts
import type { BrowserOptions, NodeOptions } from "@sentry/nextjs";

/** Sentry ships DARK unless a DSN is present. */
export const serverDsn = process.env.SENTRY_DSN ?? "";
export const clientDsn = process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";
export const isSentryServerEnabled = serverDsn.length > 0;
export const isSentryClientEnabled = clientDsn.length > 0;

const tracesSampleRate = process.env.NODE_ENV === "production" ? 0.1 : 1.0;

export function buildServerInit(): NodeOptions {
  return {
    dsn: serverDsn,
    enabled: isSentryServerEnabled,
    tracesSampleRate,
    environment: process.env.NODE_ENV,
    // Drop noisy/expected errors; never report AbortError from the Stop button.
    ignoreErrors: ["AbortError", "The user aborted a request."],
  };
}

export function buildClientInit(): BrowserOptions {
  return {
    dsn: clientDsn,
    enabled: isSentryClientEnabled,
    tracesSampleRate,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0, // no Session Replay unless explicitly enabled (privacy)
    environment: process.env.NODE_ENV,
    ignoreErrors: ["AbortError", "The user aborted a request."],
  };
}
```

`instrumentation-client.ts` (Next 16 replaces `sentry.client.config.ts`):

```ts
// instrumentation-client.ts
import * as Sentry from "@sentry/nextjs";
import { buildClientInit, isSentryClientEnabled } from "@/lib/observability/sentry";

if (isSentryClientEnabled) {
  Sentry.init(buildClientInit());
}

// Required by Sentry for navigation/transaction instrumentation on the App Router.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
```

`sentry.server.config.ts`:

```ts
// sentry.server.config.ts
import * as Sentry from "@sentry/nextjs";
import { buildServerInit, isSentryServerEnabled } from "@/lib/observability/sentry";

if (isSentryServerEnabled) {
  Sentry.init(buildServerInit());
}
```

`sentry.edge.config.ts`:

```ts
// sentry.edge.config.ts
import * as Sentry from "@sentry/nextjs";
import { buildServerInit, isSentryServerEnabled } from "@/lib/observability/sentry";

if (isSentryServerEnabled) {
  Sentry.init(buildServerInit());
}
```

`instrumentation.ts` (Next 16 server/edge bootstrap + nested-RSC error capture):

```ts
// instrumentation.ts
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

export async function onRequestError(...args: unknown[]) {
  // Only forward to Sentry when a DSN is configured.
  if (process.env.SENTRY_DSN) {
    const Sentry = await import("@sentry/nextjs");
    // @ts-expect-error — Sentry types the hook; args are passed through verbatim.
    return Sentry.captureRequestError(...args);
  }
}
```

`app/global-error.tsx` (report render errors; the file already exists from M0 — add the capture):

```tsx
// app/global-error.tsx
"use client";
import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_SENTRY_DSN) Sentry.captureException(error);
  }, [error]);
  return (
    <html>
      <body>
        <p>Something went wrong.</p>
      </body>
    </html>
  );
}
```

Wrap `next.config.ts` with `withSentryConfig` (combined with Task 6's images block):

```ts
// next.config.ts
import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  devIndicators: false,
  images: {
    // Task 6: rich-Markdown image hosts — explicit allowlist (NO wildcards).
    remotePatterns: [
      { protocol: "https", hostname: "upload.wikimedia.org" },
      { protocol: "https", hostname: "raw.githubusercontent.com" },
      { protocol: "https", hostname: "images.unsplash.com" },
      // Backend-served S3/CDN assets (Phase 5 presigned uploads):
      { protocol: "https", hostname: "**.amazonaws.com" },
    ],
  },
};

export default withSentryConfig(nextConfig, {
  // Source-map upload — auth token from CI secret, never committed.
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  silent: !process.env.CI,
  // Proxy events past ad-blockers; also reduces client-side DSN exposure.
  tunnelRoute: "/monitoring",
  // Upload source maps but hide them from the public bundle.
  sourcemaps: { deleteSourcemapsAfterUpload: true },
  // Don't fail the build if Sentry CLI can't reach the server (e.g. no token in dev).
  disableLogger: true,
});
```

Test-error trigger (dev/staging only — guard so it never ships to prod nav):

```tsx
// app/(debug)/sentry-test/page.tsx
"use client";
export default function SentryTestPage() {
  return (
    <button
      onClick={() => {
        throw new Error("M9 Sentry test error — manual trigger");
      }}
    >
      Throw test error
    </button>
  );
}
```

**Acceptance:** with `SENTRY_DSN`/`NEXT_PUBLIC_SENTRY_DSN` set, clicking the test button delivers an event to the Sentry project; with them unset, no Sentry network calls fire and the app behaves identically (dark).

---

### Task 6 — `next.config.ts` images allowlist for rich Markdown

**Goal:** let synthesis-node `![alt](url)` images render via `next/image` without opening an SSRF/abuse surface.

**Files:** `next.config.ts` (the `images.remotePatterns` block shown in Task 5).

Rationale + how to extend, document inline in the config as a comment:

```ts
// To add a trusted image host: append a { protocol, hostname } entry.
// NEVER use hostname: "**" (open proxy / SSRF). Prefer the narrowest host or a
// "**.cdn.example.com" subdomain wildcard tied to a host you control.
```

If the Markdown renderer (`react-markdown`) uses plain `<img>` rather than `next/image`, the allowlist still matters for any component that *does* route through `next/image`; either way, keep the host list explicit. For non-`next/image` `<img>`, also ensure the renderer is configured (M3) to only allow `http(s)` URLs and to set `referrerPolicy="no-referrer"`.

**Acceptance:** a Markdown answer containing `![diagram](https://raw.githubusercontent.com/.../x.png)` renders the image; an image from a non-allowlisted host fails closed (broken image, no crash).

---

### Task 7 — Analytics provider (gated by env key)

**Goal:** opt-in product/web analytics mounted in `providers.tsx`, dark when the key is unset.

**Files:** `lib/observability/analytics.tsx`, `app/providers.tsx`.

Default: Vercel Web Analytics (`npm install @vercel/analytics`).

```tsx
// lib/observability/analytics.tsx
"use client";
import { Analytics } from "@vercel/analytics/react";

const analyticsEnabled = process.env.NEXT_PUBLIC_ANALYTICS_ENABLED === "true";

export function AnalyticsProvider() {
  if (!analyticsEnabled) return null; // ships dark
  return <Analytics />;
}
```

PostHog alternative (swap-in behind the same component name) when product funnels are needed:

```tsx
// lib/observability/analytics.tsx  (PostHog variant)
"use client";
import { useEffect } from "react";
import posthog from "posthog-js";

const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;

export function AnalyticsProvider() {
  useEffect(() => {
    if (key) {
      posthog.init(key, {
        api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com",
        capture_pageview: true,
        persistence: "memory", // privacy-leaning default; switch to cookies post-consent
      });
    }
  }, []);
  return null;
}
```

Mount in `providers.tsx` (under the existing Query/Theme providers — render order doesn't matter, it's side-effect only):

```tsx
// app/providers.tsx  (addition)
import { AnalyticsProvider } from "@/lib/observability/analytics";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider {...themeProps}>
        {children}
        <AnalyticsProvider />
        <Toaster />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
```

**Acceptance:** with the analytics env key/flag set, a pageview is logged in the provider dashboard; unset, no analytics network calls fire.

---

### Task 8 — Add new env vars to the Zod schema + `.env.example`

**Goal:** keep `lib/env.ts` the single validated source of truth; all new observability vars are **optional** so unset = dark.

**Files:** `lib/env.ts`, `.env.example`.

```ts
// lib/env.ts  (additions to the existing Zod schema)
import { z } from "zod";

const envSchema = z.object({
  // ...existing: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_FEATURE_STREAMING, etc.

  // --- Observability (all optional → app ships dark when unset) ---
  SENTRY_DSN: z.string().url().optional(),
  NEXT_PUBLIC_SENTRY_DSN: z.string().url().optional(),
  SENTRY_ORG: z.string().optional(),
  SENTRY_PROJECT: z.string().optional(),
  SENTRY_AUTH_TOKEN: z.string().optional(), // build-time only; never exposed to client
  NEXT_PUBLIC_ANALYTICS_ENABLED: z
    .enum(["true", "false"])
    .optional()
    .transform((v) => v === "true"),
  NEXT_PUBLIC_POSTHOG_KEY: z.string().optional(),
  NEXT_PUBLIC_POSTHOG_HOST: z.string().url().optional(),
});

export const env = envSchema.parse({
  // map process.env keys here as the existing pattern does
  // ...
});
```

> Note: `SENTRY_DSN`/`SENTRY_ORG`/`SENTRY_PROJECT`/`SENTRY_AUTH_TOKEN` are **server/build-only** (no `NEXT_PUBLIC_` prefix) so they are not bundled into client JS. Only `NEXT_PUBLIC_SENTRY_DSN` and the `NEXT_PUBLIC_*` analytics keys reach the browser. Sentry's `*.config.ts` files read `process.env` directly (they run at init before `env.ts` parse), but keeping the schema in sync documents and validates them in CI.

`.env.example` (append):

```bash
# --- Observability (optional; leave unset to ship dark) ---
# SENTRY_DSN=                       # server DSN (do not prefix NEXT_PUBLIC_)
# NEXT_PUBLIC_SENTRY_DSN=           # client DSN
# SENTRY_ORG=
# SENTRY_PROJECT=
# SENTRY_AUTH_TOKEN=                # CI secret only — never commit
# NEXT_PUBLIC_ANALYTICS_ENABLED=false
# NEXT_PUBLIC_POSTHOG_KEY=
# NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
```

**Acceptance:** `tsc --noEmit` + `next build` pass with all observability vars unset; setting an invalid `SENTRY_DSN` (non-URL) fails the Zod parse loudly.

---

## 7. End-to-End Verification (real backend)

Run against the **real P6 streaming backend** (local `:8000/api` or the deployed Render URL once P6 is merged).

### Streaming happy path
1. `NEXT_PUBLIC_API_URL=http://localhost:8000/api`, `NEXT_PUBLIC_FEATURE_STREAMING=true`, `npm run dev`.
2. Open the chat, send: *"What does the uploaded document say about X?"*
3. **Observe the thinking-steps animate in real order**, driven by real `status` events: `routing` → `retrieving` (and/or `searching web`) → `synthesizing` — each step entering with the M4 stagger.
4. **Tokens stream into the assistant body** with the **blinking caret** trailing the text (M4), growing chunk by chunk.
5. On `done`: caret disappears, the **route badge** renders the mapped label (e.g. `RAG` / `WEB` / `WEB+RAG`), and the sources panel renders gracefully (count present if the backend supplied one, hidden/empty otherwise — no fabricated number).

### Stop mid-stream
6. Send a long query; click **Stop** while tokens stream. The `AbortController` aborts the fetch; the partial message is finalized cleanly; **no error toast, no Sentry event** (AbortError is swallowed per Task 2/5).

### Reduced-motion
7. Enable OS reduced-motion (`prefers-reduced-motion: reduce`). Resend. **Tokens still stream and steps still appear**, but **no transform/stagger animations fire** — content updates without movement (M4 gate). Caret may render static or omit blink.

### Error path
8. Force a backend `event: error` (e.g. invalid provider key). The stream closes cleanly: the partial assistant message finalizes in an error state with a toast; the UI does not crash; the next message works.

### Observability
9. With `NEXT_PUBLIC_SENTRY_DSN`/`SENTRY_DSN` set, visit `/(debug)/sentry-test`, click the button → **a test error appears in the Sentry project** within ~1 min (delivered via the `/monitoring` tunnel).
10. With the analytics key/flag set, load a page → **a pageview is recorded** in the analytics dashboard.

### Fallback / regression (BLOCKING)
11. Set `NEXT_PUBLIC_FEATURE_STREAMING=false`, restart. The **blocking path still works** end-to-end (send → full JSON answer renders → synthesized single "done" step + `context_count` sources). This proves the facade switch is intact and M9 didn't regress the blocking strategy.
12. With **all observability env unset**, confirm zero Sentry/analytics network requests in the Network tab — the app ships fully dark.

---

## 8. Risks & Gotchas

- **Real protocol drift from M2's assumptions.** P6 uses `event: done` (not `[DONE]`), `status.stage` includes a space (`"searching web"`), `route` is an object not a string, and there is **no sources count in the stream**. Mitigated by the Task 1–3 guards (pinned union, route mapper, `[DONE]` tolerated as a no-op). **Version-guard**: if the backend later adds a `sources` field to `done`, the schema's `.optional()` absorbs it without a breaking change.
- **Partial / last token + termination.** Some providers emit the whole answer as one `token` event (07_Phase6: "one final chunk if the provider can't stream"); the UI must render identically whether it's 1 chunk or 500. If zero tokens stream, `onDone.answer` is the fallback content (Task 3) — never show an empty assistant bubble.
- **Error event mid-stream UX.** An `event: error` after some tokens already streamed must finalize the *partial* message in an error state (not discard it) and surface a toast — don't throw past the hook boundary or you lose the partial answer and crash the list.
- **Proxy / CDN buffering breaks SSE.** Render/NGINX/Cloudflare may buffer `text/event-stream`, delaying or batching events so steps appear all-at-once or tokens arrive in one lump. The backend should set `X-Accel-Buffering: no` and disable proxy buffering; the frontend should not assume timing. If streaming looks "blocking" against the deployed backend, check buffering before suspecting the parser.
- **Next 16 + Sentry instrumentation API.** Next 16 uses `instrumentation.ts` (`register`/`onRequestError`) and `instrumentation-client.ts` (with `onRouterTransitionStart`) — the older `sentry.client.config.ts` auto-load is gone. Use the files exactly as in Task 5 or client-side navigation traces and RSC errors won't be captured.
- **Source-map upload secrets in CI.** `SENTRY_AUTH_TOKEN` must come from a CI secret, never committed; `deleteSourcemapsAfterUpload: true` keeps maps out of the public bundle. The build must not fail when the token is absent (dev) — `disableLogger`/`silent` handle that.
- **Analytics + privacy/consent.** Default to privacy-leaning settings (no Session Replay; PostHog `persistence: "memory"` until consent). If operating under GDPR/CCPA, gate cookie-based tracking behind a consent banner — the env flag already lets analytics ship dark by default.
- **Images allowlist security.** Never use `hostname: "**"` — it turns the Next image optimizer into an open SSRF proxy. Keep the host list explicit (Task 6); for `<img>` rendered by `react-markdown`, restrict to `http(s)` and set `referrerPolicy="no-referrer"`.
- **Caret / animation cost at high token rate.** At hundreds of tokens/sec, re-rendering the whole message per chunk is the perf trap. Reaffirm M4's mitigations: animate **transform/opacity only**, memoize message components, and batch `appendContent` writes (the store should coalesce rapid chunks). Reduced-motion must short-circuit all of this.
- **Abort during finalize.** If the user clicks Stop in the narrow window between the last `token` and `done`, ensure `finalize` is idempotent and the AbortError path doesn't double-finalize or leave the message stuck in `streaming`. Guard `finalize` against being called twice for one message id.

---

## 9. Exit Criteria (checkable)

- [ ] **Streaming works end-to-end vs the real P6 backend**: status events animate thinking-steps in order (`routing → retrieving`/`searching web` `→ synthesizing`), tokens stream into the body with the caret, `done` finalizes route badge + sources.
- [ ] `lib/sse/types.ts` / `chat.schemas.ts` / `stream-chat.ts` reconciled to the real P6 shapes; SSE unit tests green for `status`/`token`/`done`/`error` and a recorded real stream.
- [ ] `NEXT_PUBLIC_FEATURE_STREAMING=true` in `.env`/`.env.example`; `flags.streaming` routes `useChat` → `useStreamingChat`.
- [ ] **Stop mid-stream** aborts cleanly (partial message finalized, no error toast, no Sentry event).
- [ ] **Reduced-motion clean**: tokens still stream but no transform/stagger animations fire.
- [ ] **Error event** finalizes a partial message in an error state without crashing; next message works.
- [ ] `next.config.ts` `images.remotePatterns` allowlist present (explicit hosts, **no wildcard**); a Markdown image from an allowlisted host renders.
- [ ] **Sentry test error received** in the project when DSN set; **zero Sentry calls** when DSN unset (ships dark).
- [ ] Analytics pageview logged when key set; zero analytics calls when unset.
- [ ] New env vars added to `lib/env.ts` (all `.optional()`) + `.env.example`; `tsc --noEmit` + `next build` pass with everything unset.
- [ ] **Flag-off blocking path still green** (regression gate): blocking strategy renders answer + synthesized step + `context_count` sources.

---

## 10. Commit Plan

Milestone-sized, conventional commits on the working branch (`claude/frontend-improvements-planning-1aX4u`):

1. `fix(sse): reconcile parser/types/schemas with real P6 event shapes (done/error/stage)` — Tasks 1–2.
2. `feat(chat): map RouteDecision + derive sources in streaming finalize` — Task 3.
3. `feat(flags): flip NEXT_PUBLIC_FEATURE_STREAMING on for real SSE backend` — Task 4.
4. `feat(observability): add @sentry/nextjs gated by SENTRY_DSN (Next 16 instrumentation)` — Task 5.
5. `feat(next): images remotePatterns allowlist for rich-markdown images` — Task 6 (may fold into #4's `next.config.ts` change; keep the diff reviewable).
6. `feat(observability): opt-in analytics provider gated by env key` — Task 7.
7. `chore(env): add optional observability vars to Zod schema + .env.example` — Task 8.
8. `test(m9): e2e streaming + reduced-motion + sentry/analytics dark-launch gates` — Section 7 automation where feasible.

Each commit independently builds and type-checks; observability commits are no-ops at runtime until env/DSN are provided, so they are safe to land ahead of secrets being configured.

> Citations: backend SSE contract — `Python-Agentic-RAG-Backend/docs/07_Phase6_LangGraph_and_Streaming.md` (Appendix B GraphState/RouteDecision, Appendix C event catalog + SSE helper, Appendix F event-sequence test). Backend observability posture (opt-in, secrets-as-secrets, default-off-in-CI) aligned from `Python-Agentic-RAG-Backend/docs/08_Phase7_Memory_and_Observability.md` (§2 decisions, §4 gotchas). Frontend architecture/flag/facade conventions — `docs/FRONTEND_IMPROVEMENT_PLAN.md` (§"SSE design", §"useChat facade", M2/M4 milestones).
