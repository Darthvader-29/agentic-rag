# M9 — Real SSE Activation + Rich Markdown & Observability (Backend Phase P6)

This is the capstone milestone: the dark-launched streaming architecture built in M2 (SSE parser + strategy + `useChat` facade) and animated in M4 (caret + thinking-steps motion) finally **switches on** against the real LangGraph/SSE backend shipped in Phase 6. The only code change required to enable streaming itself is flipping `NEXT_PUBLIC_FEATURE_STREAMING=true` — everything else here is _verification_ against the real P6 wire format, the **event plumbing** for the new `component` event (parse → `onComponent` → `addComponent` — **rich rendering is M10**), a `next.config.ts` images allowlist so rich Markdown images render, and _opt-in_ observability (Sentry + analytics) that ships dark when env/DSN are unset.

> **Backend design change (09_Phase6 supersedes 07's design).** The route in the `done` event is now a **flat enum** (`RAG | WEB | BOTH | DIRECT`), _not_ the old `{destination, relevant}` object; the synthesis node now emits a new **`component`** SSE event (whole fenced-JSON blocks) alongside prose `token`s; and the `citation` component is the real **sources channel**. M2 already designs its SSE core against `09_Phase6` (flat-route union, `SseComponentSchema`, `onComponent`/`addComponent`, `mapRoute` with `BOTH`→`"WEB+RAG"`), so M9 mostly **verifies** against the real backend rather than reconciling a stale shape. See §2.

**Status:** backend-dependent (needs P6 streaming shipped — see `Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md` (authoritative) + `07_Phase6_LangGraph_and_Streaming.md` (framing/tests)) / depends on (M2 streaming core, M4 motion layer) / capstone of M0–M9.

---

## 1. Objective & Scope

### In scope

- **Flip the streaming flag** (`NEXT_PUBLIC_FEATURE_STREAMING=true`) and **verify the dark-launched pipeline end-to-end** against the real P6 SSE backend: `status` events drive live thinking-steps (with M4 stagger), `token` events stream the body with the blinking caret, `done` finalizes route badge + sources.
- **Verify** `lib/sse/parser.ts` / `lib/sse/types.ts` / `features/chat/api/chat.schemas.ts` against the **exact P6 event shapes** — confirming the schemas M2 already designed against `09_Phase6` hold against the real stream, and tightening only where they differ (status stage strings, the flat-enum `done.route`, the `component` event, the `error` event). Note the `done` event's **`route` is now a flat enum** (`RAG | WEB | BOTH | DIRECT`), not the old `{destination, relevant}` object.
- **Wire the `component` SSE event end-to-end** — parse → `onComponent` → `addComponent` so component blocks (the new synthesis-emitted fenced-JSON blocks) arrive and are stored on the message. **M9 verifies arrival/storage only; the rich rendering of each component type is M10** (see Task 3a, and cross-ref M10).
- **Derive sources from `citation` components** — provenance now arrives in the stream as `citation`-typed `component` events (the real sources channel), feeding the sources panel (M4) instead of being inferred from `status` events.
- **Surface the `free_tier_exhausted` error as a BYOK CTA** — when the backend signals the free tier is exhausted (`code: "free_tier_exhausted"`), finalize the message in an error state **and** show the "add your own key to continue" call-to-action (cross-ref M7).
- **Rich-markdown image allowlist**: add `images.remotePatterns` to `next.config.ts` so `![alt](url)` images emitted by the synthesis node render through `next/image` / the Markdown renderer (also serves M10's `media` component).
- **Sentry enablement**: install + configure `@sentry/nextjs`, gated by `SENTRY_DSN` (no-op when unset), with `tracesSampleRate`, event filtering, source-map upload, and a test-error trigger.
- **Analytics enablement**: a provider component (Vercel Web Analytics or PostHog) mounted in `app/providers.tsx`, gated by an env key (ships dark when unset).
- **Env schema additions** for the new optional vars in `lib/env.ts` (Zod, all `.optional()`) + `.env.example`.

### Out of scope (already delivered — do **not** rebuild)

- **The SSE parser, the streaming strategy, the `useChat` facade switch** — built and unit-tested in **M2** (`lib/sse/parser.ts`, `lib/sse/stream-chat.ts`, `features/chat/hooks/use-streaming-chat.ts`, `use-chat.ts`).
- **The streaming caret, thinking-steps stagger/expand-collapse, reduced-motion gate** — built in **M4** (`features/chat/components/thinking-steps.tsx`, the caret in `chat-message.tsx`, `hooks/use-reduced-motion.ts`).
- **The store actions** `appendContent` / `pushStep` / `addComponent` / `finalize` and the unified `Message` shape (`steps`/`sources`/`components`/`status`) — built in **M2/M1** (`features/chat/store/chat.store.ts`). M9 only adds a second _caller_ of `addComponent` against the live stream.
- **The flat-route union + `component` schema + `mapRoute`** — `SseRouteSchema` (flat `RAG | WEB | BOTH | DIRECT`, legacy object tolerated), the loose `SseComponentSchema`, the `onComponent`/`addComponent` wiring, and `mapRoute` (`BOTH`→"WEB+RAG") all landed in **M2** designed against `09_Phase6`. M9 _verifies_ them against the real backend; it does not redesign them.
- **Rendering the `component` blocks** — the strict per-type Zod schemas + renderers (tables/charts/citations/code/callouts/media under `features/chat/components/rich/`) are **M10**, gated by `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`. M9 only confirms the events _arrive and are stored_ (cross-ref **M10**).
- **Backend SSE itself** — P6 (`09_Phase6...md`, authoritative; `07_Phase6...md` for framing/tests). M9 consumes it; it does not implement it.

This milestone is **mostly activation + verification + observability wiring**, not building streaming from scratch.

---

## 2. Production SSE Contract (P6)

Source of truth (authoritative): `Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md` — **§2 the graph**, **§5 the output contract**, **Appendix A `GraphState`/`Route`**, **Appendix C component examples**. This **supersedes the design in `07`**. Framing + tests still come from `07_Phase6_LangGraph_and_Streaming.md` — **Appendix C (SSE framing helper + base event catalog)** and **Appendix F (parity / event-sequence test)**. The two `09` additions over `07` are the **`component` event** and the **flat `route` enum** (both detailed below).

### Wire format

The backend emits over `Content-Type: text/event-stream` using the hand-rolled framing helper (07_Phase6, Appendix C):

```python
def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
```

So every frame is exactly an `event:` line + a single-line JSON `data:` line, terminated by a blank line (`\n\n`). **There is no multi-line `data:` and no `[DONE]` sentinel** — completion is signalled by a typed `event: done`.

### Event catalog (07_Phase6 Appendix C base catalog + the two 09_Phase6 additions)

| `event:`    | `data:` JSON payload                                                      | Emitted when                                                                               |
| ----------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `status`    | `{"stage": "routing"}`                                                    | supervisor node starts (route + relevance decision)                                        |
| `status`    | `{"stage": "searching web"}`                                              | web node starts                                                                            |
| `status`    | `{"stage": "retrieving"}`                                                 | vector node starts                                                                         |
| `status`    | `{"stage": "synthesizing"}`                                               | synthesis node starts                                                                      |
| `token`     | `{"text": "..."}`                                                         | each generated chunk (or one final chunk if the provider can't stream)                     |
| `component` | `{"type": "table"\|"chart"\|"citation"\|"code"\|"callout"\|"media", ...}` | a synthesis fenced-JSON block's fence closed; emitted as one whole block (**09 addition**) |
| `done`      | `{"answer": "...", "route": "RAG"\|"WEB"\|"BOTH"\|"DIRECT"}`              | stream complete; final answer + **flat route enum** (**09 changes the route shape**)       |
| `error`     | `{"detail": "...", "code"?: "..."}`                                       | any node raised, or a guard (e.g. free-tier) tripped; closes the stream cleanly            |

> **The `component` event (09 §5 + Appendix C).** Synthesis streams Markdown prose token-by-token (`token` events) **plus** zero-or-more fenced ` ```json ` component blocks. The backend **buffers each block until its closing fence**, validates it, and emits it as **one** `component` event (you can't render half a chart). An invalid/malformed block is **dropped server-side** — the prose still streams and the request **never 500s**. The catalog `type` is one of `table | chart | citation | code | callout | media`. M9 wires this event through to storage (parse → `onComponent` → `addComponent`); **rendering each type is M10**.

### Status stage ordering

From Appendix F's event-sequence assertion (`stages == ["routing", "retrieving", "synthesizing"]`) and the endpoint sketch (Appendix C), the observable stage progression is:

```
routing → (retrieving | searching web | both)* → synthesizing → [token | component]* → done
```

- `routing` is always first (supervisor).
- The middle stage(s) depend on the route decision: `retrieving` (vectorstore), `searching web` (web_search), or **both** on the `BOTH` parallel fan-out (09 §2 / Appendix A — disjoint `web_result`/`vector_result` keys). When both branches run, **both** `status` events arrive (order not guaranteed between them).
- `synthesizing` precedes the `token`/`component` stream; `component` events are **interleaved with `token`s** (each emitted when its fenced block closes).
- The stream terminates with **exactly one** `done` _or_ one `error`.

### Where route + sources are delivered

- **`route` — a FLAT enum.** Route + the final answer arrive in the **`done` event payload** (`{"answer", "route"}`), _not_ as standalone events and _not_ as a trailing `status`. Per `09` (Appendix A `GraphState.route`), `route` is a **flat string enum** `RAG | WEB | BOTH | DIRECT` — **not** `07`'s `{destination, relevant}` object. The streaming strategy maps it to the frontend's `RouteType` badge label via the shared `mapRoute`: `RAG`→"RAG", `WEB`→"WEB", **`BOTH`→"WEB+RAG"**, `DIRECT`→"DIRECT" (see Task 3). M2 already designs `mapRoute` this way; M9 verifies it against the live `done.route` and keeps a defensive tolerance for the legacy object form.
- **Sources — from `citation` components (✅ resolved).** Provenance now arrives **in the stream** as `citation`-typed `component` events (`09` §5: the `citation` component is the SOURCES / provenance channel — clickable cards linking to the exact retrieved chunk / web source). This **resolves this milestone's earlier open issue** — previous drafts noted "the P6 SSE catalog doesn't surface sources, so derive a best-effort signal from observed `status` events." That is no longer needed: **feed the sources panel (M4) from the `citation` components** collected during the stream. A precise numeric "sources count" stays **best-effort/optional** (e.g. the number of `citation` items, when present); the UI must still render gracefully when a turn emits no `citation` component at all.

### Delta vs. the contract M2 designed against

**M2 already designs against `09_Phase6`**, so there is almost nothing to reconcile — only to _verify_ against the real backend. M2 already landed: the flat-route union `SseRouteSchema` (`RAG | WEB | BOTH | DIRECT`, with the legacy `{destination, relevant}` object tolerated in a `z.union`), the loose `SseComponentSchema`, the `onComponent`/`addComponent` wiring, the `component` dispatch `case` in `streamChat`, and `mapRoute` with `BOTH`→"WEB+RAG". `parseSSE` is event-name-agnostic, so `component` already passes through with no parser change. The genuinely-remaining reconciliation is small:

| Concern            | M2 (designed against 09)                                                       | M9's job (verify against the real backend)                                                                                                                    |
| ------------------ | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `done.route` shape | `SseRouteSchema` = flat enum, legacy object tolerated                          | Confirm the live `done.route` is the **flat string**; keep the legacy-object tolerance as a defensive no-op until the backend is confirmed flat-only.         |
| `component` event  | Parsed (loose schema), dispatched via `onComponent`, stored via `addComponent` | Confirm real-backend `component` events **arrive and are stored**; malformed/unknown-`type` blocks are dropped, never thrown (rendering verified in **M10**). |
| Sources            | From `citation` components                                                     | Confirm `citation` components populate the sources panel against the real stream.                                                                             |
| `error` `code`     | `SseErrorSchema` carries an optional `code`                                    | Confirm `free_tier_exhausted` surfaces the BYOK CTA (Task 3b); flag the HTTP-status assumption.                                                               |

No rewrite — M2's multi-line/partial-buffer handling and the schemas all stay; M9 tightens only if the live stream proves a schema too loose.

---

## 3. Decisions & Rationale

| Decision                                                                  | Rationale                                                                                                                                                                                                                                                                                                                            | Alternatives considered                                                                                                                   |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Flag flip is the only activation**                                      | M2 built the strategy switch (`flags.streaming ? useStreamingChat : useBlockingChat`) and M4 built the motion; both strategies write the same `Message` shape through the same store actions, so flipping `NEXT_PUBLIC_FEATURE_STREAMING=true` lights up streaming with **zero component rewrites**. The architecture pays off here. | Conditionals scattered in components (defeats the facade); a parallel streaming UI (duplicate surface).                                   |
| **M9 verifies, M10 renders the `component` event**                        | The `component` plumbing (parse → `onComponent` → `addComponent`) lands here so the data plane is proven against the real backend, but the strict per-type schemas + renderers live in **M10** behind `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`. Storing whole blocks now means M10 is a render-only add, not a re-integration.          | Render rich components in M9 (couples plumbing verification to a much larger UI surface; can't ship streaming until renderers exist).     |
| **`free_tier_exhausted` is surfaced as a BYOK CTA, by `code` not status** | The backend's freemium guard (09 §3) returns a machine-readable `code: "free_tier_exhausted"`; branching on the **code** (not the HTTP status, which is an API-layer detail) lets M9 show M7's "add your own key to continue" upsell distinctly from a generic error.                                                                | Treat it as a generic 429/error toast (loses the upsell — the entire point of the freemium ladder).                                       |
| **Backend-internal cost levers are invisible to the frontend**            | Per-node **model tiering** + **prompt caching** (09 §6) change the backend's spend, not the SSE/HTTP contract — so M9 carries **no** frontend change for them.                                                                                                                                                                       | (n/a — noted only so no one looks for a frontend hook.)                                                                                   |
| **Observability is opt-in via env/DSN**                                   | Sentry/analytics must **ship dark** when `SENTRY_DSN` / analytics key are unset — no errors, no network calls, no bundle cost beyond a guard. Mirrors the backend's posture (08_Phase7: OTEL/LangSmith gated behind flags, default off in CI; keys as secrets, never logged).                                                        | Always-on Sentry (PII risk, noise in dev/CI); build-time-only gating (can't toggle per environment).                                      |
| **Sentry tunneling + source maps for Next 16**                            | Next 16 uses the `instrumentation`/`instrumentation-client` model; `withSentryConfig` wraps `next.config.ts`, uploads source maps at build, and a `tunnelRoute` proxies events past ad-blockers. Source-map auth token comes from CI secret `SENTRY_AUTH_TOKEN`, never committed.                                                    | Manual `@sentry/browser` (loses Next integration, server/edge spans, tunneling); no source maps (unreadable stack traces).                |
| **Analytics: Vercel Web Analytics (default), PostHog optional**           | Vercel `@vercel/analytics` is zero-config, privacy-friendly, and a one-line `<Analytics/>` component gated by an env flag; PostHog is the swap-in when product analytics/funnels are needed. Either is mounted in `providers.tsx` behind an env key so it ships dark.                                                                | GA4 (heavier, consent burden); roll-our-own (no value).                                                                                   |
| **Images allowlist over disabling optimization**                          | Rich Markdown from the synthesis node contains `![alt](url)` to arbitrary hosts. Use `images.remotePatterns` with an **explicit trusted host allowlist** rather than `unoptimized: true` or a `**` wildcard — keeps optimization + SSRF/abuse surface bounded.                                                                       | `unoptimized:true` (loses optimization, still no host control); `remotePatterns: [{hostname:'**'}]` (open proxy / SSRF risk — forbidden). |

---

## 4. Pre-Flight Checklist (entry gate — what M2/M4 already delivered)

**Do not flip the flag until every item below is GREEN.** These are the M2/M4 deliverables this milestone activates; treat this as the entry gate.

- [ ] `lib/sse/parser.ts` — `parseSSE(stream)` async generator exists; **unit tests pass** for multi-line `data:`, partial-buffer across chunk boundaries, and `[DONE]` tolerance. (Event-name-agnostic, so `component` already passes through.)
- [ ] `lib/sse/stream-chat.ts` — `streamChat(payload, {signal, onStatus, onToken, onComponent, onDone, onError})` fetches with `Accept: text/event-stream`, iterates `parseSSE`, and already dispatches a **`component`** case via `onComponent`.
- [ ] `features/chat/api/chat.schemas.ts` (M2) — `SseRouteSchema` (flat `RAG | WEB | BOTH | DIRECT`, legacy object tolerated) and the loose `SseComponentSchema` exist; `SseErrorSchema` carries an optional `code`.
- [ ] `features/chat/hooks/use-streaming-chat.ts` — maps `onStatus → pushStep`, `onToken → appendContent`, `onComponent → addComponent`, completion `→ finalize` (with `mapRoute(done.route)`, `BOTH`→"WEB+RAG"); `AbortController` wired to `stop()`.
- [ ] `features/chat/hooks/use-chat.ts` — facade reads `flags.streaming` and delegates to `useStreamingChat` **or** `useBlockingChat`; exposes stable `{ messages, isStreaming, sendMessage, stop, retry }`.
- [ ] `features/chat/store/chat.store.ts` — `appendContent(id, chunk)`, `pushStep(id, step)`, **`addComponent(id, component)`**, `finalize(id, {route, sources})` actions exist and write the unified `Message` (`steps`/`sources`/`components`/`status`).
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
.env.example                      # FLIP + document new SENTRY_/analytics vars (+ list NEXT_PUBLIC_FEATURE_RICH_COMPONENTS, owned by M10)
next.config.ts                    # ADD images.remotePatterns; WRAP with withSentryConfig
instrumentation.ts                # NEW (Next 16): registers Sentry server/edge init
instrumentation-client.ts         # NEW (Next 16): Sentry browser init (replaces sentry.client.config.ts)
sentry.server.config.ts           # NEW: server Sentry.init, gated by SENTRY_DSN
sentry.edge.config.ts             # NEW: edge Sentry.init, gated by SENTRY_DSN
app/global-error.tsx              # EDIT: report render errors to Sentry (Sentry.captureException)
app/providers.tsx                 # EDIT: mount <Analytics/> (gated) under existing providers
lib/env.ts                        # EDIT: add SENTRY_DSN, NEXT_PUBLIC_SENTRY_DSN, analytics key + list NEXT_PUBLIC_FEATURE_RICH_COMPONENTS (all optional)
lib/observability/sentry.ts       # NEW: shared buildSentryInit(dsn) + isSentryEnabled guard
lib/observability/analytics.tsx   # NEW: <AnalyticsProvider/> wrapper gated by env
app/(debug)/sentry-test/page.tsx  # NEW (dev/staging only): button that throws a test error

# Verification against the real backend (M2 already designed these against 09_Phase6 — see §2):
lib/sse/parser.ts                 # VERIFY: event-name-agnostic; component already passes through; keep [DONE] no-op
lib/sse/types.ts                  # VERIFY: SseEvent union incl. component + flat-route done; tighten only if needed
lib/sse/stream-chat.ts            # VERIFY: onComponent dispatch + onDone(answer, flat route) + error code
features/chat/api/chat.schemas.ts # VERIFY: SseRouteSchema (flat enum, legacy tolerated), loose SseComponentSchema, SseErrorSchema.code
features/chat/hooks/use-streaming-chat.ts  # VERIFY/EDIT: onComponent → addComponent; mapRoute(done.route); citation → sources; free_tier_exhausted → BYOK CTA

# RENDERING the component blocks is M10 — NOT touched by M9:
features/chat/components/rich/*   # M10 OWNS: strict per-type schemas + renderers (table/chart/citation/code/callout/media)
```

No source file outside this list should change. The component **renderers** (`features/chat/components/rich/*`) and the `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` flag are **M10's**; M9 only touches the event plumbing/verification above plus the observability files. If your repo used `sentry.client.config.ts` in an earlier scaffold, the Next 16 equivalent is `instrumentation-client.ts` (see Task 5).

---

## 6. Tasks (ordered)

> Code below is copy-pasteable. Adjust import aliases only if your `tsconfig` `paths` differ from `@/*`.

### Task 1 — Verify the SSE types/schemas against the real stream (tighten only if needed)

**Goal:** confirm the TypeScript event union + Zod schemas **M2 already designed against `09_Phase6`** match the real wire format; tighten only where the live stream differs. M2 landed the flat-route union (`SseFlatRouteSchema`/`SseRouteSchema`), the loose `SseComponentSchema`, and `SseErrorSchema` with an optional `code` — **there is no `{destination, relevant}`-only assumption left to reconcile** (that shape is superseded by `09`; the legacy object is merely _tolerated_ in a `z.union`). M9 verifies and keeps the schemas tolerant.

**Files:** `lib/sse/types.ts`, `features/chat/api/chat.schemas.ts` (both already authored in M2 — **confirm**, don't redesign).

`lib/sse/types.ts` (the M2 union — `component` + flat-route `done` + optional error `code`):

```ts
// lib/sse/types.ts
// Mirrors Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md (authoritative)
// + 07_Phase6 Appendix C framing. (Authored in M2; M9 verifies against the live stream.)

/** Backend status stages, verbatim (note the SPACE in "searching web"). */
export type SseStage =
  | "routing"
  | "retrieving"
  | "searching web"
  | "synthesizing";

/** done.route is a FLAT enum (09 Appendix A) — not 07's {destination, relevant}. */
export type SseFlatRoute = "RAG" | "WEB" | "BOTH" | "DIRECT";
/** Legacy object form, tolerated defensively until the backend is confirmed flat-only. */
export interface SseLegacyRoute {
  destination: "web_search" | "vectorstore";
  relevant?: boolean;
}
export type SseRoute = SseFlatRoute | SseLegacyRoute;

/** The component catalog discriminant (loose here; M10 owns the strict per-type shapes). */
export type SseComponentType =
  | "table"
  | "chart"
  | "citation"
  | "code"
  | "callout"
  | "media";

export type SseEvent =
  | { event: "status"; data: { stage: SseStage } }
  | { event: "token"; data: { text: string } }
  | {
      event: "component";
      data: { type: SseComponentType; [k: string]: unknown };
    }
  | { event: "done"; data: { answer: string; route: SseRoute | null } }
  | { event: "error"; data: { detail: string; code?: string } };

export type SseEventName = SseEvent["event"];
```

`features/chat/api/chat.schemas.ts` (the M2 streaming schemas, alongside the existing blocking `ChatResponse` schema; do not remove the blocking one):

```ts
// features/chat/api/chat.schemas.ts  (M2 streaming additions — M9 verifies)
import { z } from "zod";

export const SseStatusSchema = z.object({
  stage: z.string().min(1), // "routing" | "retrieving" | "searching web" | "synthesizing"
});
export const SseTokenSchema = z.object({ text: z.string() });

// done.route — FLAT enum, with the legacy {destination, relevant} object TOLERATED.
export const SseFlatRouteSchema = z.enum(["RAG", "WEB", "BOTH", "DIRECT"]);
export const SseLegacyRouteSchema = z.object({
  destination: z.string(),
  relevant: z.boolean().optional(),
});
export const SseRouteSchema = z.union([
  SseFlatRouteSchema,
  SseLegacyRouteSchema,
]);

export const SseDoneSchema = z.object({
  answer: z.string(),
  // route may be null/omitted (e.g. DIRECT); keep it tolerant. Flat enum, legacy tolerated.
  route: SseRouteSchema.nullable().optional(),
  // best-effort: a precise count is optional (sources come from citation components). See §2.
  context_count: z.number().int().nonnegative().optional(),
});

// component — LOOSE here (validate only the catalog discriminant, pass the rest through).
// M10 introduces the STRICT per-type schemas when it builds the renderers.
export const SseComponentSchema = z
  .object({
    type: z.enum(["table", "chart", "citation", "code", "callout", "media"]),
  })
  .passthrough();

// error — optional `code` distinguishes free_tier_exhausted etc. (09 §3).
export const SseErrorSchema = z.object({
  detail: z.string(),
  code: z.string().optional(),
});

export type SseRoute = z.infer<typeof SseRouteSchema>;
export type SseDone = z.infer<typeof SseDoneSchema>;
export type SseComponent = z.infer<typeof SseComponentSchema>;
```

**Acceptance:** types compile; `SseDoneSchema` tolerates a missing/`null` route **and** the flat enum (and still accepts the legacy object); `SseComponentSchema` accepts each catalog `type` and drops nothing it shouldn't; `SseErrorSchema` parses both a bare `{detail}` and `{detail, code}`. Tighten only if the live stream reveals a too-loose shape.

---

### Task 2 — Verify the parser + `streamChat` against the real stream (`component` + flat `done` + error `code`)

**Goal:** confirm the M2 transport surfaces the typed `status`/`token`/**`component`**/`done`/`error` events to the strategy; keep M2's robustness (partial buffer, multi-line, `[DONE]` tolerance). The `component` dispatch `case` and the `onComponent` callback **already exist from M2** — M9 verifies them against the real backend and the flat-route `done`.

**Files:** `lib/sse/parser.ts`, `lib/sse/stream-chat.ts` (both authored in M2 — **confirm**, don't rebuild).

In `lib/sse/parser.ts`, the generator already yields `{ event, data }` frames and is **event-name-agnostic** — so `component` already passes through with **no parser change**. The only thing to confirm is the defensive `[DONE]` no-op and malformed-JSON skip:

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

In `lib/sse/stream-chat.ts`, the callback surface already includes `onComponent` and `onDone`; the in-band `error` event is surfaced **with its `code`** (carried on a typed error so the hook can branch — see Task 3b):

```ts
// lib/sse/stream-chat.ts
import { parseSSE } from "@/lib/sse/parser";
import {
  SseStatusSchema,
  SseTokenSchema,
  SseComponentSchema,
  SseDoneSchema,
  SseErrorSchema,
  type SseDone,
  type SseComponent,
} from "@/features/chat/api/chat.schemas";
import type { SseStage } from "@/lib/sse/types";
import { env } from "@/lib/env";

/** Carries the backend's machine-readable `code` (e.g. "free_tier_exhausted") to the hook. */
export class StreamError extends Error {
  constructor(
    message: string,
    readonly code?: string
  ) {
    super(message);
    this.name = "StreamError";
  }
}

export interface StreamChatHandlers {
  signal: AbortSignal;
  onStatus: (stage: SseStage) => void;
  onToken: (text: string) => void;
  onComponent: (component: SseComponent) => void; // M2 wiring; M9 verifies
  onDone: (payload: SseDone) => void;
  onError: (err: Error) => void;
}

export async function streamChat(
  payload: { message: string; session_id: string; web_search_allowed: boolean },
  {
    signal,
    onStatus,
    onToken,
    onComponent,
    onDone,
    onError,
  }: StreamChatHandlers
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
      // Non-stream HTTP failure (auth/rate-limit/free-tier raised BEFORE the stream
      // opened). Try to surface the machine-readable code from the JSON body.
      let detail = `Backend error: ${res.status}`;
      let code: string | undefined;
      try {
        const body = await res.json();
        if (body?.detail) detail = String(body.detail);
        if (body?.code) code = String(body.code); // e.g. "free_tier_exhausted"
      } catch {
        /* non-JSON error body */
      }
      throw new StreamError(detail, code);
    }

    for await (const frame of parseSSE(res.body)) {
      switch (frame.event) {
        case "status": {
          const { stage } = SseStatusSchema.parse(frame.data);
          onStatus(stage);
          break;
        }
        case "token": {
          const { text } = SseTokenSchema.parse(frame.data);
          onToken(text);
          break;
        }
        case "component": {
          // Loose-validate the catalog type; drop (never throw) an invalid block.
          const parsed = SseComponentSchema.safeParse(frame.data);
          if (parsed.success) onComponent(parsed.data); // → addComponent (Task 3a)
          break;
        }
        case "done": {
          onDone(SseDoneSchema.parse(frame.data));
          return; // terminal
        }
        case "error": {
          // Surface the optional `code` (free_tier_exhausted etc.) on a StreamError.
          const { detail, code } = SseErrorSchema.parse(frame.data);
          onError(new StreamError(detail, code));
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

**Acceptance:** unit test — feed a recorded P6 stream (`status×N → token×N → component? → done`) and assert `onStatus`/`onToken`/`onComponent`/`onDone` fire in order; a `component` with an unknown `type` is **dropped** (no `onComponent`, no throw); an injected `event: error` with `code: "free_tier_exhausted"` calls `onError` with a `StreamError` carrying that `code` and stops; an `AbortError` is swallowed.

---

### Task 3 — Map `done` → `finalize` in the streaming strategy (flat-enum `mapRoute` + sources from `citation`)

**Goal:** translate the **flat-enum** `done.route` into the frontend's `RouteType` badge via the shared `mapRoute` (`BOTH`→"WEB+RAG"), and feed the sources panel from the **`citation` components** collected during the stream. M2 already authored `mapRoute`; M9 reuses it as-is and verifies it against the live `done.route`.

**Files:** `features/chat/hooks/use-streaming-chat.ts`, plus the shared `mapRoute` (M2).

`mapRoute` is the **flat-enum** mapper M2 landed (in `features/chat/api/chat.schemas.ts` or a `route.ts` util); confirm it matches:

```ts
// features/chat/lib/map-route.ts  (authored in M2 — confirm)
import type { SseRoute } from "@/lib/sse/types";
import type { RouteType } from "@/types";

/**
 * Map the backend's FLAT route enum (09 Appendix A: RAG | WEB | BOTH | DIRECT) to the
 * frontend badge label. BOTH = vector + web both ran → "WEB+RAG". The legacy
 * {destination, relevant} object is tolerated defensively (pre-normalized below).
 */
export function mapRoute(route: SseRoute | null): RouteType {
  if (!route) return "DIRECT";
  // Legacy object form (tolerated until the backend is confirmed flat-only).
  if (typeof route === "object") {
    return route.destination === "web_search" ? "WEB" : "RAG";
  }
  switch (route) {
    case "RAG":
      return "RAG";
    case "WEB":
      return "WEB";
    case "BOTH":
      return "WEB+RAG"; // BOTH = retrieve + web_search both ran
    case "DIRECT":
      return "DIRECT";
    default:
      return "DIRECT";
  }
}
```

> **Legacy tolerance.** The Phase 6 contract is the flat enum; the object branch above is a defensive pre-normalization only. Do not change the canonical flat-enum signature — if the live backend is confirmed flat-only, the object branch is dead code that can be dropped later.

In `use-streaming-chat.ts`, wire the callbacks to the store actions (M2). The strategy already exists; M9 confirms the `onComponent → addComponent` sink (Task 3a) and **derives sources from the `citation` components**:

```ts
// features/chat/hooks/use-streaming-chat.ts  (callback wiring — strategy already exists from M2)
import { mapRoute } from "@/features/chat/lib/map-route";
import type { SseComponent } from "@/features/chat/api/chat.schemas";

// inside sendMessage, after creating the assistant message `id` and AbortController:
const citations: SseComponent[] = []; // the real sources channel (09 §5)

await streamChat(payload, {
  signal: controller.signal,
  onStatus: (stage) => {
    pushStep(id, stage); // M4 ThinkingSteps renders this live with stagger
  },
  onToken: (text) => appendContent(id, text), // body grows + blinking caret (M4)
  onComponent: (component) => {
    // Store every component on the message (M2 sink); M10 renders them.
    addComponent(id, component);
    // Sources ARE the citation components — collect them for the sources panel (M4).
    if (component.type === "citation") citations.push(component);
  },
  onDone: ({ answer, route, context_count }) => {
    finalize(id, {
      route: mapRoute(route), // flat enum → badge; BOTH → "WEB+RAG"
      // Sources come from the citation components, not a status-derived guess.
      sources: citations.length ? citations : undefined,
      // best-effort numeric count: explicit count if sent, else #citations.
      sourcesCount: context_count ?? (citations.length || undefined),
      fallbackContent: answer, // store uses this only if streamed content is empty
    });
  },
  onError: (err) => handleStreamFailure(err), // Task 3b (free_tier_exhausted → BYOK CTA)
});
```

> The exact `finalize` signature is M2's; this task only feeds it mapped values. If M2's `finalize` doesn't yet accept `fallbackContent`, that is the **one** small additive change permitted to the store action here (additive, optional params — no breaking change to the blocking path).

**Acceptance:** a streamed turn ends with the correct route badge — `RAG`→"RAG", `WEB`→"WEB", `BOTH`→"WEB+RAG", `DIRECT`→"DIRECT"; the sources panel renders the `citation` components (and is empty/hidden — no fabricated number — when a turn emits none); a legacy `{destination, relevant}` `done.route` still maps without throwing.

---

### Task 3a — Confirm `onComponent → addComponent` (rendering is M10)

**Goal:** verify each real-backend `component` event flows **parse → `onComponent` → `addComponent`** and lands on the message's `components` array. **M9 stops at storage; M10 renders** the blocks via `features/chat/components/rich/*` behind `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`.

**Files:** `features/chat/hooks/use-streaming-chat.ts` (the `onComponent` wiring above), `features/chat/store/chat.store.ts` (the M1 `addComponent` sink — read-only confirm).

```ts
// in use-streaming-chat.ts (from M2) — the component sink, verified here
onComponent: (component) => {
  addComponent(id, component); // opaque store-write; survives into M5 history
  // M10: rich renderers read message.components and render per `type`.
},
```

`addComponent` is an **opaque sink** (M1): it appends the validated payload to `Message.components` without interpreting it, so an unrecognized/partial block is stored harmlessly and never breaks the stream. M9 asserts arrival + storage; M10 owns render correctness.

**Acceptance:** against the real backend, a turn that emits `component` events ends with those blocks in `message.components` (count matches the events received); a malformed block never reaches the store (dropped in `streamChat`, Task 2) and never throws.

---

### Task 3b — Surface `free_tier_exhausted` as a BYOK CTA (cross-ref M7)

**Goal:** when the backend signals the free tier is exhausted, finalize the message in an error state **and** show the "add your own key to continue" call-to-action; all other errors behave as today.

**Files:** `features/chat/hooks/use-streaming-chat.ts` (the error path), plus M7's BYOK CTA component (cross-ref).

The backend's freemium guard (09 §3) returns a failure carrying a machine-readable **`code: "free_tier_exhausted"`** (body `{detail, code}`). Branch on the **`code`**, not the HTTP status:

```ts
// features/chat/hooks/use-streaming-chat.ts  (error handling)
import { StreamError } from "@/lib/sse/stream-chat";

function handleStreamFailure(err: Error) {
  const code = err instanceof StreamError ? err.code : undefined;

  if (code === "free_tier_exhausted") {
    // Finalize the (partial) message in an error state AND prompt for BYOK.
    finalize(id, { route: "ERROR", error: err.message });
    showByokCta(); // M7's "add your own key to continue" upsell + data-policy disclaimer
    return;
  }

  // Existing behavior for all other errors: error state + generic toast.
  finalize(id, { route: "ERROR", error: err.message });
}
```

> **⚠️ Flag the assumption.** `free_tier_exhausted` may arrive **two ways**: (a) as a **non-stream HTTP failure** (a 4xx with body `{detail, code}`) raised _before_ the SSE stream opens — `streamChat` reads the `code` off the JSON body and throws a `StreamError` (Task 2); or (b) as a **terminal `error` SSE event** with `code: "free_tier_exhausted"`. The **exact HTTP status is an API-layer detail** (09 §3) — **branch on the `code`, not the status**, and handle both delivery paths. The BYOK CTA + the free-mode **data-policy disclaimer** are owned by **M7** (09 §4); M9 only triggers them on this code.

**Acceptance:** a `free_tier_exhausted` response (whether pre-stream HTTP failure or terminal `error` event) finalizes the message in an error state **and** renders the BYOK "add your own key" CTA; every other error code keeps today's generic error+toast behavior; no branch depends on a specific HTTP status code.

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
import {
  buildClientInit,
  isSentryClientEnabled,
} from "@/lib/observability/sentry";

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
import {
  buildServerInit,
  isSentryServerEnabled,
} from "@/lib/observability/sentry";

if (isSentryServerEnabled) {
  Sentry.init(buildServerInit());
}
```

`sentry.edge.config.ts`:

```ts
// sentry.edge.config.ts
import * as Sentry from "@sentry/nextjs";
import {
  buildServerInit,
  isSentryServerEnabled,
} from "@/lib/observability/sentry";

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

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
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

**Goal:** let synthesis-node `![alt](url)` images render via `next/image` without opening an SSRF/abuse surface. The same allowlist also covers **M10's `media` component** (image/gallery references rendered from `component` events), so include the hosts those media URLs come from.

**Files:** `next.config.ts` (the `images.remotePatterns` block shown in Task 5).

Rationale + how to extend, document inline in the config as a comment:

```ts
// To add a trusted image host: append a { protocol, hostname } entry.
// NEVER use hostname: "**" (open proxy / SSRF). Prefer the narrowest host or a
// "**.cdn.example.com" subdomain wildcard tied to a host you control.
```

If the Markdown renderer (`react-markdown`) uses plain `<img>` rather than `next/image`, the allowlist still matters for any component that _does_ route through `next/image`; either way, keep the host list explicit. For non-`next/image` `<img>`, also ensure the renderer is configured (M3) to only allow `http(s)` URLs and to set `referrerPolicy="no-referrer"`.

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
        api_host:
          process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com",
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

**Goal:** keep `lib/env.ts` the single validated source of truth; all new observability vars are **optional** so unset = dark. **Also list `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`** here alongside the observability vars — the flag is **owned by M10** (it gates the rich component renderers → `flags.richComponents`); M9 just adds it to the schema so the env validates while M10 builds against it. Default `false`.

**Files:** `lib/env.ts`, `.env.example`.

```ts
// lib/env.ts  (additions to the existing Zod schema)
import { z } from "zod";

const envSchema = z.object({
  // ...existing: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_FEATURE_STREAMING, etc.

  // --- Rich components flag — OWNED BY M10; listed here so env validates (default false) ---
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: z
    .enum(["true", "false"])
    .optional()
    .default("false")
    .transform((v) => v === "true"),

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

> Note: `SENTRY_DSN`/`SENTRY_ORG`/`SENTRY_PROJECT`/`SENTRY_AUTH_TOKEN` are **server/build-only** (no `NEXT_PUBLIC_` prefix) so they are not bundled into client JS. Only `NEXT_PUBLIC_SENTRY_DSN` and the `NEXT_PUBLIC_*` analytics keys reach the browser. Sentry's `*.config.ts` files read `process.env` directly (they run at init before `env.ts` parse), but keeping the schema in sync documents and validates them in CI. `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` is consumed by **M10** (it drives `flags.richComponents`); M9 only declares it so the schema stays complete.

`.env.example` (append):

```bash
# --- Rich components (owned by M10; default off) ---
# NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false
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

**Acceptance:** `tsc --noEmit` + `next build` pass with all observability vars unset (and `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` defaulting to `false`); setting an invalid `SENTRY_DSN` (non-URL) fails the Zod parse loudly.

---

## 7. End-to-End Verification (real backend)

Run against the **real P6 streaming backend** (local `:8000/api` or the deployed Render URL once P6 is merged).

### Streaming happy path

1. `NEXT_PUBLIC_API_URL=http://localhost:8000/api`, `NEXT_PUBLIC_FEATURE_STREAMING=true`, `npm run dev`.
2. Open the chat, send: _"What does the uploaded document say about X?"_
3. **Observe the thinking-steps animate in real order**, driven by real `status` events: `routing` → `retrieving` (and/or `searching web`) → `synthesizing` — each step entering with the M4 stagger.
4. **Tokens stream into the assistant body** with the **blinking caret** trailing the text (M4), growing chunk by chunk.
5. On `done`: caret disappears, the **route badge** renders the **flat-enum mapped label** — `RAG`→"RAG", `WEB`→"WEB", `BOTH`→"WEB+RAG", `DIRECT`→"DIRECT". The sources panel renders gracefully (populated from `citation` components when present; hidden/empty otherwise — no fabricated number).

### Component events arrive + stored (rendering verified in M10)

6. During a turn that emits structured output, confirm `component` events are parsed, flow `onComponent`→`addComponent`, and land on `message.components` (the count matches the events received). **Storage is verified here; the rich rendering of each type is verified in M10** — M9 just confirms the blocks arrive and survive into the message. A malformed block is dropped (no crash, prose still renders).

### Sources from `citation` components

7. Confirm the sources panel (M4) is populated from `citation`-typed `component` events (clickable cards), and degrades gracefully (empty/hidden) on a turn that emits no `citation` component.

### Stop mid-stream

8. Send a long query; click **Stop** while tokens stream. The `AbortController` aborts the fetch; the partial message is finalized cleanly; **no error toast, no Sentry event** (AbortError is swallowed per Task 2/5).

### Reduced-motion

9. Enable OS reduced-motion (`prefers-reduced-motion: reduce`). Resend. **Tokens still stream and steps still appear**, but **no transform/stagger animations fire** — content updates without movement (M4 gate). Caret may render static or omit blink.

### Error path

10. Force a backend `event: error` (e.g. invalid provider key). The stream closes cleanly: the partial assistant message finalizes in an error state with a toast; the UI does not crash; the next message works.

### Freemium BYOK CTA

11. Trigger a `free_tier_exhausted` response (exhaust the free allowance, no BYOK key) — whether it arrives as a **pre-stream HTTP failure** or a **terminal `error` event** (branching on `code`, not status). The message finalizes in an error state **and** the BYOK **"add your own key to continue"** CTA renders (M7); the free-mode data-policy disclaimer is shown in free mode (M7).

### Observability

12. With `NEXT_PUBLIC_SENTRY_DSN`/`SENTRY_DSN` set, visit `/(debug)/sentry-test`, click the button → **a test error appears in the Sentry project** within ~1 min (delivered via the `/monitoring` tunnel).
13. With the analytics key/flag set, load a page → **a pageview is recorded** in the analytics dashboard.

### Fallback / regression (BLOCKING)

14. Set `NEXT_PUBLIC_FEATURE_STREAMING=false`, restart. The **blocking path still works** end-to-end (send → full JSON answer renders → synthesized single "done" step + `context_count` sources). This proves the facade switch is intact and M9 didn't regress the blocking strategy.
15. With **all observability env unset**, confirm zero Sentry/analytics network requests in the Network tab — the app ships fully dark.

---

## 8. Risks & Gotchas

- **Real protocol drift from the docs.** P6 uses `event: done` (not `[DONE]`) and `status.stage` includes a space (`"searching web"`). Per `09`, `done.route` is now a **flat enum** (`RAG | WEB | BOTH | DIRECT`) and provenance arrives as **`citation` components** — both of which M2 already designs against. Mitigated by the Task 1–3 guards (flat-route union with the legacy object tolerated, `mapRoute` `BOTH`→"WEB+RAG", loose `component` schema, `[DONE]` tolerated as a no-op). **Version-guard**: the tolerant schemas absorb a stray legacy route object or extra `done` field without a breaking change.
- **Confirm `done.route` is the flat string (not the legacy object).** The live backend should send the flat enum; M9 verifies this. The `mapRoute` object branch is a defensive pre-normalization only — if a turn ever sends `{destination, relevant}`, the badge still maps; once the backend is confirmed flat-only, that branch is droppable.
- **`component` events: buffered-whole, drop-on-invalid.** A `component` arrives only once its fenced block closes (never half a chart), interleaved with `token`s. An invalid/unknown-`type` block is **dropped** in `streamChat` (loose `safeParse`) so the prose still renders and the stream never breaks. M9 only stores them; **rendering robustness is M10's** (`features/chat/components/rich/*`). If components don't appear, confirm storage (`message.components`) before suspecting render.
- **`free_tier_exhausted` delivery is two-path; branch on `code` not status.** It may be a **pre-stream HTTP failure** (`streamChat` reads `code` off the JSON body → `StreamError`) **or** a **terminal `error` SSE event** with `code`. The exact HTTP status is an API-layer detail (09 §3) — branching on the status alone misses the BYOK CTA. Handle both paths; the CTA + free-mode disclaimer are M7's.
- **Partial / last token + termination.** Some providers emit the whole answer as one `token` event (07_Phase6: "one final chunk if the provider can't stream"); the UI must render identically whether it's 1 chunk or 500. If zero tokens stream, `onDone.answer` is the fallback content (Task 3) — never show an empty assistant bubble.
- **Error event mid-stream UX.** An `event: error` after some tokens already streamed must finalize the _partial_ message in an error state (not discard it) and surface a toast (or the BYOK CTA for `free_tier_exhausted`) — don't throw past the hook boundary or you lose the partial answer and crash the list.
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
- [ ] `lib/sse/types.ts` / `chat.schemas.ts` / `stream-chat.ts` **verified against the real `09_Phase6` shapes**; SSE unit tests green for `status`/`token`/`component`/`done`/`error` and a recorded real stream.
- [ ] **`done.route` flat enum maps correctly** via `mapRoute`: `RAG`→"RAG", `WEB`→"WEB", `BOTH`→"WEB+RAG", `DIRECT`→"DIRECT" (legacy object form still tolerated).
- [ ] **`component` events arrive and are stored**: real-backend `component` events flow `onComponent`→`addComponent` onto `message.components` (count matches); a malformed block is dropped, never thrown. (**Rendering is verified in M10**.)
- [ ] **Sources come from `citation` components**: the sources panel is populated from `citation` blocks and degrades gracefully (empty/hidden) when a turn emits none — no fabricated count.
- [ ] **`free_tier_exhausted` shows the BYOK CTA**: a free-tier-exhausted response (pre-stream HTTP failure _or_ terminal `error` event, branched on `code`) finalizes in an error state **and** renders M7's "add your own key to continue" CTA; other errors map to generic handling.
- [ ] `NEXT_PUBLIC_FEATURE_STREAMING=true` in `.env`/`.env.example`; `flags.streaming` routes `useChat` → `useStreamingChat`.
- [ ] **Stop mid-stream** aborts cleanly (partial message finalized, no error toast, no Sentry event).
- [ ] **Reduced-motion clean**: tokens still stream but no transform/stagger animations fire.
- [ ] **Error event** finalizes a partial message in an error state without crashing; next message works.
- [ ] `next.config.ts` `images.remotePatterns` allowlist present (explicit hosts, **no wildcard**); a Markdown image from an allowlisted host renders (also covers M10's `media` component hosts).
- [ ] **Sentry test error received** in the project when DSN set; **zero Sentry calls** when DSN unset (ships dark).
- [ ] Analytics pageview logged when key set; zero analytics calls when unset.
- [ ] New env vars added to `lib/env.ts` (all `.optional()`, incl. `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` — owned by M10) + `.env.example`; `tsc --noEmit` + `next build` pass with everything unset.
- [ ] **Flag-off blocking path still green** (regression gate): blocking strategy renders answer + synthesized step + `context_count` sources.

---

## 10. Commit Plan

Milestone-sized, conventional commits on the working branch (`claude/frontend-improvements-planning-1aX4u`):

1. `fix(sse): verify parser/types/schemas against real 09_Phase6 shapes (component/flat-route/error code)` — Tasks 1–2.
2. `feat(chat): map flat done.route via mapRoute + derive sources from citation components` — Task 3 / 3a.
3. `feat(chat): surface free_tier_exhausted as a BYOK CTA (branch on code, not status)` — Task 3b.
4. `feat(flags): flip NEXT_PUBLIC_FEATURE_STREAMING on for real SSE backend` — Task 4.
5. `feat(observability): add @sentry/nextjs gated by SENTRY_DSN (Next 16 instrumentation)` — Task 5.
6. `feat(next): images remotePatterns allowlist for rich-markdown images (+ M10 media hosts)` — Task 6 (may fold into #5's `next.config.ts` change; keep the diff reviewable).
7. `feat(observability): opt-in analytics provider gated by env key` — Task 7.
8. `chore(env): add optional observability vars + list NEXT_PUBLIC_FEATURE_RICH_COMPONENTS (M10) in Zod schema + .env.example` — Task 8.
9. `test(m9): e2e streaming + component-arrival + citation-sources + free-tier CTA + dark-launch gates` — Section 7 automation where feasible.

Each commit independently builds and type-checks; observability commits are no-ops at runtime until env/DSN are provided, so they are safe to land ahead of secrets being configured. The `component` plumbing lands here, but **rendering it is M10** — keep the two milestones' diffs separate.

> Citations: backend SSE contract (authoritative) — `Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md` (§2 the graph, §5 the output contract incl. the `component` event + `citation` sources channel, Appendix A `GraphState`/`Route` flat enum, Appendix C component examples; supersedes `07`'s design); framing + tests still from `07_Phase6_LangGraph_and_Streaming.md` (Appendix C event catalog + SSE helper, Appendix F event-sequence test). Freemium (`free_tier_exhausted` code, free-tier data policy) — `09_Phase6` §3–4 (owned by **M7** on the frontend). Backend observability posture (opt-in, secrets-as-secrets, default-off-in-CI) aligned from `Python-Agentic-RAG-Backend/docs/08_Phase7_Memory_and_Observability.md` (§2 decisions, §4 gotchas). Frontend architecture/flag/facade conventions — `docs/FRONTEND_IMPROVEMENT_PLAN.md` (§"SSE design", §"useChat facade", M2/M4 milestones); rich-component rendering + `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` — **M10** (`docs/milestones/M10_Rich_Component_Rendering.md`).
