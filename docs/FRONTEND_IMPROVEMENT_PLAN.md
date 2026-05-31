# Frontend Improvement Plan — TypeScript Agentic RAG Frontend

## Context

The frontend (`typescript-agentic-rag-frontend`: Next.js 16, React 19, Tailwind v4,
shadcn/ui, TS strict) is a functional prototype that needs to reach industry standard.
Today **all state lives in `app/page.tsx`** with prop-drilling, chat is **blocking**
(awaits the full `/chat` JSON before rendering), uploads are fire-and-forget, there is
**no auth, no streaming, no tests, no CI, no theme toggle**, `layout.tsx` is missing the
`ThemeProvider`/`Toaster` and still says "Create Next App", and several `any` types leak in.

The paired **Python backend is mid-roadmap** (docs in `Python-Agentic-RAG-Backend/docs`).
Upcoming phases change the API contract: **P3** JWT auth + login/register + user-owned
sessions; **P4** multi-provider BYOK (Gemini/OpenAI/Anthropic) + model picker; **P5**
presigned S3 uploads + ingestion-status polling; **P6** rebuilds `/chat` as a **LangGraph
agentic graph** (supervisor → vector/web → synthesis; `BOTH` fans out in parallel) over **SSE
streaming** (`event: status {stage}`, `event: token`, typed `event: done`), adds a new
`event: component` for **rich structured output** (a fixed catalog —
`table`/`chart`/`citation`/`code`/`callout`/`media`), and introduces a **freemium provider
ladder** (BYOK → operator free-tier → `free_tier_exhausted`). The P6 route is a **flat enum**
`RAG | WEB | BOTH | DIRECT` (`BOTH` → the frontend's `WEB+RAG`), replacing the old
`{destination, relevant}` object. (`09_Phase6_Agentic_Architecture.md` is authoritative over
`07_Phase6` for this design.)

**Goal:** a minimal-looking but polished chat UI with tasteful micro-animations,
token-by-token **streaming**, and **"thinking / agent-steps" tabs** (driven by the SSE
`status` events: routing → retrieving/searching → synthesizing → done). Architect it once so
streaming/auth/BYOK/uploads **switch on via feature flags** as each backend phase ships —
no rewrites. Full stack freedom approved.

## Guiding Principles

- **Refactor + streaming-ready architecture first** — works against today's blocking backend,
  structured so SSE/auth/BYOK/presigned-upload just "switch on" later.
- **Zod-validated env feature flags** gate every forward-compatible surface so unfinished
  backend phases ship dark (no broken UI).
- **One `useChat` facade** delegates to a blocking _or_ streaming strategy behind
  `NEXT_PUBLIC_FEATURE_STREAMING`; both write the same `Message` shape, so UI never changes.
- **Minimal aesthetic, tasteful motion**; every animation honors `prefers-reduced-motion`.

## Target Stack (additions)

- **Server state:** `@tanstack/react-query` · **UI/live chat state:** `zustand`
- **Validation:** `zod` (env + API responses) · **Input:** `react-textarea-autosize`
- **Motion:** `framer-motion` · **Tests:** `vitest` + React Testing Library + `msw` + `playwright`
- **Quality:** `prettier` (+ `prettier-plugin-tailwindcss`), `eslint-plugin-jsx-a11y`,
  `eslint-config-prettier`, `husky`, `lint-staged`
- **Optional/gated:** `@sentry/nextjs`, analytics (Vercel/PostHog)

## Target Architecture

**Feature-based folders.** Move logic out of `page.tsx` into feature modules that each own
`api/` (+ Zod schemas) · `hooks/` · `store/` · `components/`.

```
app/            layout.tsx (ThemeProvider+Toaster+Providers, fixed metadata), page.tsx (thin),
                providers.tsx, error.tsx, global-error.tsx,
                (auth)/login, (auth)/register [P3], settings/ [P4]
lib/            env.ts (Zod env), flags.ts, query-client.ts,
                api/http-client.ts (+ api-error.ts), sse/parser.ts, sse/stream-chat.ts
features/chat/  api/{chat.api,chat.schemas} · store/chat.store · hooks/{use-chat,
                use-streaming-chat,use-blocking-chat} · components/{chat-screen,message-list,
                chat-message,chat-input,thinking-steps,sources-panel,message-actions,
                code-block,route-badge,empty-state,message-loading},
                components/rich/* (component dispatcher + per-type renderers — table/chart/
                citation/code/callout/media) [P6]
features/sessions|auth|upload|providers/   scaffolded per backend phase (flag-gated)
components/     ui/* (shadcn + add dropdown-menu,tooltip,collapsible,skeleton,command),
                theme/{theme-provider,theme-toggle}, layout/app-sidebar, error/error-fallback
hooks/          use-reduced-motion, use-copy-to-clipboard
test/ e2e/      setup.ts, msw/handlers.ts, chat.spec.ts
```

**State split.** _TanStack Query_ owns discrete async resources (blocking `/chat` mutation,
upload, cleanup; later: sessions, document status polling, provider keys, auth). _Zustand_
owns live chat (`messages[]`, in-flight stream buffer, per-message `steps[]`/`sources`/`status`,
`draft`, `webSearchAllowed`), persisted `auth.store` tokens, `session.store`, UI prefs.
High-frequency token/step accumulation stays in Zustand, not the Query cache. The blocking
mutation writes its result into the same Zustand store on success — identical shape to streaming.

**`useChat` facade** (`features/chat/hooks/use-chat.ts`) exposes a stable API
`{ messages, isStreaming, sendMessage, stop, retry }`. Reads `flags.streaming`; delegates to
`useStreamingChat` or `useBlockingChat`, both writing through the **same store actions** and
`Message` shape (with `steps`, `sources`, `status`, and an opaque `components?: RichComponent[]`
for P6 rich blocks — added in M1, populated by the `addComponent` store action, rendered later by
M10). Blocking path synthesizes a single "done" step + `context_count` sources so the
thinking/sources panels work today. Flipping the flag after P6 is the only change.

**API layer.** `lib/api/http-client.ts`: typed `request<T>(path,{method,body,schema,auth,signal})`,
prepends `env.NEXT_PUBLIC_API_URL`, Zod-parses responses into typed `ApiError`. Auth interceptor
(dormant until P3 flag): attach `Bearer`, on `401` refresh-once-and-retry, on `403` surface
forbidden. `types/index.ts` becomes `z.infer` re-exports so runtime + compile-time stay locked.

**SSE design (POST + ReadableStream, not EventSource** — EventSource can't POST or send auth):
`lib/sse/parser.ts` `async function* parseSSE(stream)` pipes through `TextDecoderStream`, splits
on `\n\n`, parses `event:`/`data:` (handles multi-line data, partial buffer, `[DONE]`).
`lib/sse/stream-chat.ts` `streamChat(payload,{signal,onStatus,onToken,onComponent,onError})`
fetches with `Accept: text/event-stream`, iterates events. `useStreamingChat` maps
`onStatus(stage)` → `pushStep` (feeds **ThinkingSteps** live), `onToken(chunk)` →
`appendContent` (streams body + blinking caret), completion → `finalize`. `AbortController`
powers the Stop button.

The P6 stream carries three payload events plus terminator: `status` (stage), `token` (prose,
streamed token-by-token), and **`component`** — a _whole-block_ event the backend emits only
once a structured block's fence closes (you can't render half a chart), carrying one item from
the fixed catalog (`table`/`chart`/`citation`/`code`/`callout`/`media`). `onComponent(block)` →
`addComponent` appends it to the message's opaque `components[]`; the **`citation`** type is the
real sources/provenance channel (so live streaming finally has sources). The typed `event: done`
ends the stream (tolerating a `[DONE]` sentinel) and its `done.route` is a **flat enum**
(`RAG | WEB | BOTH | DIRECT`; `BOTH` → `WEB+RAG`) consumed by the route badge.

## Milestones (each independently shippable)

- **M0 — Tooling & guardrails** (no UX change): Prettier + ESLint a11y + Husky + lint-staged,
  CI skeleton, `lib/env.ts` Zod env + `lib/flags.ts` (the `NEXT_PUBLIC_FEATURE_*` set —
  `STREAMING`, `AUTH`, `BYOK`, `PRESIGNED_UPLOAD`, `RICH_COMPONENTS` — all default **false**),
  fix `app/layout.tsx` (metadata, mount `Providers`/`ThemeProvider`/`Toaster`,
  `suppressHydrationWarning`), `theme-provider` + `theme-toggle`. _Verify:_ `lint`/`format`/
  `typecheck` pass, CI green, theme toggle + toasts work.
- **M1 — Architecture refactor (parity)**: add TanStack Query + Zustand, `http-client`, Zod
  schemas, feature folders; gut `page.tsx` to a thin shell; port `services/api.ts`; delete dead
  `components/chat/chat-interface.tsx`. Behavior identical (blocking). _Verify:_ send/upload/
  cleanup/reset still work; unit tests for store + `useBlockingChat`.
- **M2 — Streaming-ready core (dark)**: `useChat` facade, `useStreamingChat`, `lib/sse/*`,
  unified `Message` shape (`steps`/`sources`/`status`). Streaming behind flag = false. _Verify:_
  unit tests for `parseSSE` (multi-line/partial/`[DONE]`) + strategy switch; mock SSE server.
- **M3 — Chat UX polish** (today's backend): `thinking-steps`, `sources-panel`, `message-actions`
  (copy/retry), `code-block` (copy + lazy `react-syntax-highlighter` via `next/dynamic`),
  `route-badge`, autosize input, migrate hardcoded `slate/blue/white` classes → semantic tokens
  (`bg-card`/`text-muted-foreground`/`border-border`/`bg-primary`), skeleton states. _Verify:_
  component tests; manual dark/light; a11y pass.
- **M4 — Motion layer**: framer-motion for message enter/exit (`AnimatePresence`+`layout`),
  streaming caret, thinking-steps expand/collapse + stagger, sidebar spring, badge transitions,
  skeleton→content crossfade; `use-reduced-motion` gate; animate only transform/opacity, memoize
  messages. _Verify:_ reduced-motion = no transforms; 60fps during streaming.
- **M5 — Tests + E2E + Docker/CI hardening**: Vitest/RTL coverage (hooks/stores/components) + MSW,
  Playwright core flow; `next.config.ts` `output:'standalone'` + slim multi-stage Dockerfile
  (copy `.next/standalone`+`static`+`public`, run `node server.js`); CI runs lint+typecheck+test+
  build+Playwright. _Verify:_ CI green; image size drops sharply; E2E passes against MSW.
- **M6 — Auth activation [P3]**: `auth/*`, `(auth)/login|register`, persisted token store,
  `http-client` 401→refresh→retry + 403, server-owned `session-list` + resume; flag
  `NEXT_PUBLIC_FEATURE_AUTH`. _Verify:_ flag-off = today's anonymous flow; flag-on (mock) works.
- **M7 — Multi-provider BYOK [P4]**: `settings/page.tsx`, `api-keys-form` (CRUD, GET hides
  secrets), per-conversation `model-picker` (gemini/openai/anthropic); flag `..._BYOK`.
- **M8 — Presigned uploads + status [P5]**: presigned PUT to S3 + `/upload/status/{task_id}`
  polling (Query `refetchInterval`), progress UI, `document-manager`; flag `..._PRESIGNED_UPLOAD`,
  multipart fallback when off.
- **M9 — Real SSE on + rich markdown/observability**: flip `NEXT_PUBLIC_FEATURE_STREAMING=true`;
  live thinking-steps + token streaming; wire the **`component`** SSE event end-to-end (parsed +
  stored as `components[]`, `citation` = live sources) and handle the **`free_tier_exhausted`**
  error code (BYOK upsell, not a raw error); `next.config` images allowlist for rich markdown;
  enable Sentry + analytics via env/DSN. _Verify:_ end-to-end streaming vs real backend; steps
  animate from real `status` events; a `component` event arrives live; freemium error surfaces the
  CTA; reduced-motion clean; Sentry receives a test error.
- **M10 — Rich Component Rendering [P6]**: render the P6 `component` catalog from the message's
  `components[]` via `features/chat/components/rich/*` (a dispatcher + per-type renderers for
  `table`/`chart`/`citation`/`code`/`callout`/`media`); flag `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`
  (default off → blocks stay buffered/hidden). _Verify:_ each renderer round-trips its sample
  block; unknown/invalid blocks degrade gracefully (prose still renders); a11y + reduced-motion
  pass; flag-off path equals today's prose-only output.

**Recommended first delivery:** M0 → M5 (foundation, streaming-ready core, full UX polish,
motion, tests/CI/Docker) — all shippable against today's backend. M6–M10 land as backend phases
ship.

## Critical Files

- `app/page.tsx` — gut to thin shell (current home of all logic to extract)
- `services/api.ts` — replace with `lib/api/http-client.ts` + feature `*.api.ts` + Zod schemas
- `app/layout.tsx` — ThemeProvider/Providers/Toaster + real metadata + `suppressHydrationWarning`
- `types/index.ts` — `z.infer` re-exports; add `steps`/`sources`/`status` to `Message`
- `components/chat/chat-message.tsx` — refactor: memoized markdown, code-block, thinking-steps,
  sources, actions

## Verification (end-to-end)

1. **Per-milestone gates:** `npm run lint`, `prettier --check`, `tsc --noEmit`, `vitest run`,
   `next build` all pass; CI green on the branch.
2. **Functional (today's backend):** run `npm run dev`, point `NEXT_PUBLIC_API_URL` at the Render
   backend (or local `:8000/api`); verify send message, file upload, web-search toggle, reset
   session, theme toggle, copy code, thinking/sources panels render (synthesized steps).
3. **Streaming (mock):** with a local mock SSE endpoint and `NEXT_PUBLIC_FEATURE_STREAMING=true`,
   confirm tokens stream into the message and `status` events drive the thinking-steps panel.
4. **E2E:** Playwright `e2e/chat.spec.ts` against MSW mocks — load → send → assistant renders →
   upload → theme toggle → reset.
5. **Docker:** `docker build` produces a small standalone image that boots via `node server.js`.
6. **Reduced motion:** with OS reduced-motion on, no transform animations fire.

All work on branch `claude/frontend-improvements-planning-1aX4u`, committed in milestone-sized
commits, pushed with `git push -u origin claude/frontend-improvements-planning-1aX4u`.
