# M10 — Rich Component Rendering (Backend Phase P6)

This is the rich-output capstone: Phase 6 synthesis emits **Markdown prose plus zero-or-more
structured component blocks** (fenced ` ```json ` blocks), each streamed as one whole `component`
SSE event. M1 already carries those blocks **opaquely** on `Message.components` and exposes an
`addComponent` store action; M2/M9 already plumb the `component` event into the store via
`onComponent → addComponent` (validated only loosely by `SseComponentSchema` in `chat.schemas.ts`).
M10 is what turns those opaque specs into **rich UI**: a **strict** per-type Zod discriminated union
(`component.schemas.ts`), a `<ComponentBlock>` dispatcher, and six per-type renderers
(`table` / `chart` / `citation` / `code` / `callout` / `media`). It is **dark-launched and additive**:
gated behind `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS`, off ⇒ no rich rendering (the block degrades to a
collapsed raw `code-block`), on ⇒ the validated catalog renders. An invalid block is **dropped** —
the surrounding prose always renders, never a crash.

**Status:** Planned · **Depends on:** M1 (`Message.components` field + `addComponent` store action),
M2 (the `component` SSE event + loose `SseComponentSchema` + `onComponent → addComponent` wiring),
M3 (`chat-message.tsx` render seam + `code-block.tsx` + `sources-panel.tsx`), M9 (real SSE flip, so
`component` events actually arrive live) · backend dep: **P6 / `09_Phase6_Agentic_Architecture.md`**
· **Unlocks:** full agentic rich output (tables, charts, provenance citations, callouts, code,
media galleries).

> **Ships behind `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false` (default OFF).** With the flag off the
> app behaves exactly as after M9: `message.components` either renders nothing or, defensively, a
> collapsed raw fenced-JSON block inside the existing `code-block`. Flipping the flag on is a pure
> additive UI capability — no change to the wire, the store, the SSE plumbing, or the prose path.

---

## 1. Objective & Scope

### In scope

- **Flag + env wiring** (extends M0-owned files, mirroring how M7/M8 add their flags):
  `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` → `flags.richComponents`, added to `lib/env.ts` (Zod, the
  existing `FeatureFlag` `z.enum(["true","false"]).transform` pattern), `lib/flags.ts`, and
  `.env.example`.
- **Strict per-type schemas** — `features/chat/components/rich/component.schemas.ts`: a
  `z.discriminatedUnion("type", [...])` over the six catalog types, producing a typed
  `ComponentSpec` (`z.infer`), plus a `safeParseComponent` helper that **drops** invalid blocks and a
  small normalizer.
- **Dispatcher** — `features/chat/components/rich/component-block.tsx`: validates one opaque spec
  against the strict union and `switch`es on the validated `type` to the matching renderer; invalid ⇒
  renders nothing (flag-on) or the raw-JSON fallback (flag-off, see §7).
- **Six per-type renderers** under `features/chat/components/rich/`:
  - `table.tsx` — semantic-token-styled `<table>`.
  - `chart.tsx` — **lazy** `recharts` (`next/dynamic`, `ssr:false`) bar/line/area chart.
  - `citation.tsx` — clickable provenance **cards** that feed / mirror the **M3 sources panel**
    (this is the SOURCES channel).
  - `code.tsx` — reuses **M3's `code-block.tsx`** (lazy highlighter + copy).
  - `callout.tsx` — leveled (`info` / `warning` / `tip`) box.
  - `media.tsx` — allowlisted images / gallery (safe `<img>`, `referrerPolicy="no-referrer"`,
    `http(s)`-only, respecting the M9 `next.config` images allowlist).
- **Render-site wiring** — `chat-message.tsx` renders `message.components` via `<ComponentBlock>`
  **after** the Markdown body (the seam M3 leaves), flag-gated.
- **Tests** — Vitest/RTL per renderer; invalid-spec-dropped; flag-off fallback; citation→sources;
  an MSW `component`-event end-to-end (M5 stack).

### Out of scope (do NOT build here)

- **Backend emission of components.** Synthesis emitting the fenced ` ```json ` blocks is **P6**
  (`09_Phase6_Agentic_Architecture.md` §5). M10 only *renders* what the backend already emits.
- **The `component` SSE event plumbing itself** — the parser branch, the loose `SseComponentSchema`,
  `onComponent`, and the `addComponent` store wiring are **M2/M9**. M10 consumes
  `message.components`; it does not parse the wire or touch the store action.
- **The strict streaming flip** (`NEXT_PUBLIC_FEATURE_STREAMING=true`) — that is **M9**. M10 works
  with components delivered by *either* strategy (M9 streamed, or a blocking path that ever populates
  `components`); it renders from `Message.components` regardless of how they got there.
- **The buffer-until-fence rule** — buffering a partial ` ```json ` block until its fence closes is a
  **backend (P6)** concern; the frontend receives one whole block per `component` event (M2).
- New shadcn primitives beyond what M3 added; auth/BYOK; the sources-panel *structure* (M3 owns it —
  M10 only feeds it citation data).

---

## 2. Backend Output Contract (P6)

> **Source of truth:** [`../../../Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md`](../../../Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md)
> — **§5 "Output contract"** and **Appendix C "Component JSON examples"**. This is the QUALITY BAR;
> the strict union in Task 2 MUST match it.

### 2.1 What synthesis emits

From 09_Phase6 §5 (verbatim intent): `synthesis` returns **Markdown prose** plus **zero or more
component specs** as fenced ` ```json ` blocks. On the backend a pydantic union validates each block;
**an invalid block is dropped (prose still renders) — never a 500** (it mirrors Phase 6's defensive
`decide_combined_route` pattern). Decision 2 of that doc fixes *why* the output is Markdown +
component JSON rather than model-authored HTML: it is cheaper (fewer tokens), **safe** (no
model-authored executable markup → no XSS), and **streamable** — "the frontend renders rich UI from a
trusted, fixed component catalog."

### 2.2 Streaming behavior (how a block reaches the frontend)

Also from §5: prose **streams token-by-token** over SSE; each component block is **buffered until its
fence closes** and emitted as **one whole `component` event** ("you can't render half a chart"). SSE
event types follow `07_Phase6` Appendix C **plus a `component` event**. So by the time M10 sees a
spec it is a complete object on `Message.components` — M10 never deals with partial blocks.

```
prose tokens … (token)*  ──┐
                           ├──>  done
component (whole block 1) ─┤      ▲ M2/M9: onComponent → addComponent(id, spec)  (opaque)
component (whole block 2) ─┘      │ M10: validate spec → <ComponentBlock> renders it
```

### 2.3 Component catalog (09_Phase6 §5 table + Appendix C examples)

| Type | Purpose (09 §5) | Authoritative shape |
|------|-----------------|---------------------|
| `table` | structured data, comparisons | `{"type":"table","columns":[...],"rows":[[...],[...]]}` (Appendix C) |
| `chart` | numbers, comparisons | `{"type":"chart","chart":"bar","x":[...],"series":[{"name":...,"y":[...]}]}` (Appendix C) |
| `citation` | clickable cards → exact retrieved chunk / web source — **provenance** | `{"type":"citation","items":[{"label":...,"source_id":...,"snippet":...}]}` (Appendix C) |
| `callout` | info / warning / tip boxes | `{"type":"callout","level":"info"\|"warning"\|"tip","text":...}` (Appendix C) |
| `code` | syntax-highlighted, copyable code | **shape not printed in 09** — see assumption below |
| `media` | images & galleries | **shape not printed in 09** — see assumption below |

The four Appendix C examples (`table`, `chart`, `citation`, `callout`) are reproduced exactly in the
strict schemas. `citation` is explicitly the **SOURCES / provenance channel** — its `items[]` are the
clickable cards linking to the exact retrieved chunk (`source_id`) or web source, "pairs naturally
with RAG, shows provenance" (09 §5). M10 routes them into the M3 sources panel (Task 6).

### 2.4 Contract ASSUMPTIONS (flagged — `code` + `media` shapes are not in 09)

09_Phase6 lists `code` and `media` in the catalog but **does not print their exact JSON shape**. M10
**defines a reasonable shape for each and flags it here as a contract assumption** to be reconciled
when the backend pins the synthesis format contract (09 §9 lists "the exact pydantic schemas for each
component type" as an open build-time detail):

```jsonc
// ASSUMED — code (reconcile with backend §9 when pinned)
{ "type": "code", "language": "python", "code": "print('hi')" }

// ASSUMED — media (reconcile with backend §9 when pinned)
{ "type": "media", "items": [ { "url": "https://…/x.png", "alt": "…", "caption": "…" } ] }
```

Because the strict union **drops** any block it cannot validate (§2.1 parity), a backend that later
ships a *different* `code`/`media` shape degrades safely to "prose only" until M10's schema is
updated — it never crashes. These two schemas are the **single place** to reconcile; the
single-source-of-truth rule (Risk R5) keeps the renderer and the backend catalog from drifting
silently.

### 2.5 The drop-invalid rule (frontend mirror of the backend rule)

The backend already drops invalid blocks server-side. M10 applies the **same rule a second time** on
the client: every opaque spec on `message.components` is re-validated against the strict union, and
**an invalid / unknown block is dropped** (the surrounding prose, and every *sibling* valid block,
still render). This is defense-in-depth — the loose `SseComponentSchema` (M2) only guarantees
"an object with a string `type`"; M10's strict union is what actually gates rendering.

---

## 3. Decisions & Rationale

| # | Decision | Rationale | Rejected alternative |
|---|----------|-----------|----------------------|
| D1 | **Strict `z.discriminatedUnion("type", …)` in M10**, while M2's loose `SseComponentSchema` stays in `chat.schemas.ts` | Two layers, two jobs: M2's loose schema keeps the *wire/store* permissive (accept any `{type, …}` so a forward-compatible block isn't dropped at the transport before the UI even exists). M10's strict union is the *render gate* — it produces a fully-typed `ComponentSpec` so each renderer gets exact props with **no `any`**, and `discriminatedUnion` gives O(1) dispatch + the best Zod error messages keyed on `type`. | One strict schema at the SSE boundary (couples the wire to the renderer; a new backend block type would be dropped before reaching the store, losing forward-compat). |
| D2 | **Drop-invalid → degrade to prose** (mirror the backend §5 rule) | A malformed/unknown block must never blank the answer. `safeParseComponent` returns `null` on failure and the dispatcher renders nothing for it; siblings + prose are unaffected. Matches the backend's "invalid block dropped, never a 500" exactly, so behavior is consistent end-to-end. | Throwing / an error boundary per block (heavier, and an error boundary still flashes a fallback); rendering raw JSON for *invalid* blocks (noisy — raw JSON is reserved for the **flag-off** path, D3). |
| D3 | **Flag-gated with a raw-`code-block` fallback when OFF** | Dark-launch discipline (the M2/M9 pattern): off ⇒ zero behavior change. But "zero rich UI" shouldn't mean "silently lose the data," so off renders each spec as **collapsed pretty-printed JSON inside M3's `code-block`** — debuggable, copyable, and obviously-not-yet-rich. On ⇒ the validated renderer. | Off ⇒ render nothing (loses visibility that components arrived); off ⇒ rich anyway (defeats the dark launch). |
| D4 | **Reuse M3's `code-block.tsx` for the `code` type** | M3 already built a lazy-highlighted, copyable, semantic-token code block with a `<pre>` fallback (`ssr:false`). The `code` component is exactly that with `{language, code}` props — reusing it means one highlighter, one copy hook, one theme, and the **flag-off raw fallback** is literally the same component. | A second highlighter in `rich/code.tsx` (duplicate ~½MB chunk, divergent styling). |
| D5 | **`citation` feeds the M3 sources panel** (provenance is one channel) | 09 §5 calls `citation` the provenance channel. M3 already owns the "Referenced N chunks" sources panel. Mapping `citation.items[] → Source[]` and rendering through the **same** panel/card affordances keeps one sources UX whether provenance arrives as M3's synthesized `sources` (blocking) or as a P6 `citation` block (rich). | A parallel, differently-styled citation list (two provenance UIs that drift). |
| D6 | **Charts via `recharts`, lazy-loaded (`next/dynamic`, `ssr:false`)** | A charting lib is heavy and only needed when an answer actually contains a `chart` block. `next/dynamic({ssr:false})` keeps it **out of the chat route's first-load JS** and off the server; it loads only when a chart mounts — the same lazy posture M3 uses for the highlighter (D2 there). `recharts` is React-native, responsive, and SVG-based (crisp, themeable via `currentColor`/tokens). | Eager import (½MB+ in first load for every chat, most with no chart); a canvas lib (imperative, harder to theme with tokens / reduced-motion). |
| D7 | **SSR / XSS safety: render from a trusted FIXED catalog only — no model-authored HTML** | This is the whole reason the backend chose "Markdown + component JSON" over "HTML from a cheap model" (09 Decision 2). M10 never `dangerouslySetInnerHTML`s anything from a spec; every field is rendered as **text or as a typed prop** of a fixed component. `media` URLs are constrained to `http(s)` + the `next.config` allowlist (M9) + `referrerPolicy="no-referrer"`; `citation`/`media` links open with `rel="noopener noreferrer"`. | Trusting any spec field as markup (re-introduces the XSS surface the backend deliberately removed). |

---

## 4. Current-State Snapshot

Everything M10 needs already exists from M1/M2/M3/M9 — M10 is purely additive on top.

- **`types/index.ts` — `Message.components` (M1).** M1's unified `Message` carries an **opaque**
  `components` field (the array each `component` SSE event appends to). M10 reads it; it does **not**
  change the `Message` type. (M1 §"unified Message shape" + the `addComponent` store action below.)
- **`features/chat/store/chat.store.ts` — `addComponent` (M1).** The store action that appends one
  opaque spec to a message's `components[]`. M2/M9 call it from `onComponent`. M10 does **not** add or
  modify a store action — it consumes the already-populated array via the message selector.
- **`features/chat/api/chat.schemas.ts` — loose `SseComponentSchema` (M2).** The permissive wire
  schema (≈ `z.object({ type: z.string() }).passthrough()`) that lets *any* component frame through
  to `addComponent`. **Stays as-is.** M10's strict union lives in a **new** file and is what the
  renderer validates against.
- **`lib/sse/parser.ts` / `lib/sse/stream-chat.ts` (M2/M9).** Already yield/dispatch the `component`
  event and call `onComponent` → `addComponent`. M10 touches none of this.
- **`features/chat/components/chat-message.tsx` (M3).** Renders the Markdown body and leaves a
  **seam after the body** (the §6 render order: route badge → thinking-steps → body → sources →
  actions). M10 inserts `<ComponentBlock>` rendering of `message.components` **between body and
  sources**, flag-gated.
- **`features/chat/components/code-block.tsx` (M3).** Lazy highlighter (`next/dynamic ssr:false`) +
  copy + `<pre>` fallback + semantic chrome. M10's `code.tsx` and the **flag-off raw fallback** reuse
  it directly.
- **`features/chat/components/sources-panel.tsx` (M3).** Collapsible provenance panel rendering
  `Source[]` / a count. M10's `citation.tsx` maps `citation.items[] → Source[]` and renders through
  the same card affordances.
- **`next.config.ts` — `images.remotePatterns` allowlist (M9).** Explicit trusted image hosts (no
  wildcard). M10's `media.tsx` respects it and additionally guards `http(s)`-only + `no-referrer`.
- **`lib/env.ts` + `lib/flags.ts` + `.env.example` (M0).** Own the Zod `FeatureFlag` pattern
  (`z.enum(["true","false"]).transform(v => v === "true")`, default `false`) and the `flags` object.
  M7/M8 already **extend** these with their flags; M10 adds one more line each, identically.

---

## 5. Target File Tree (delta)

Files **added** (✚) or **modified** (✎) by M10. Everything else is untouched.

```
features/chat/components/
  rich/
✚   component.schemas.ts        strict z.discriminatedUnion("type",…) → ComponentSpec + safeParseComponent + normalizer
✚   component-block.tsx         DISPATCHER: validate one spec, switch on type → renderer (or raw fallback when flag off)
✚   table.tsx                   semantic-token <table>
✚   chart.tsx                   lazy recharts (next/dynamic ssr:false): bar | line | area
✚   citation.tsx                provenance cards → feed the M3 sources panel
✚   code.tsx                    reuse M3 code-block.tsx ({language, code})
✚   callout.tsx                 leveled info/warning/tip box
✚   media.tsx                   allowlisted images / gallery (safe <img>, no-referrer, http(s) only)
✎ chat-message.tsx             render message.components via <ComponentBlock> after the body (flag-gated)

lib/
✎ env.ts                       + NEXT_PUBLIC_FEATURE_RICH_COMPONENTS (M0 file; extend like M7/M8)
✎ flags.ts                     + flags.richComponents (M0 file; extend like M7/M8)

✎ .env.example                 + NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false (documented)

features/chat/components/rich/__tests__/
✚   table.test.tsx             header/cells render; ragged rows tolerated
✚   chart.test.tsx             lazy chart mounts; reduced-motion disables animation
✚   citation.test.tsx          cards render; map → sources panel; safe link rel
✚   code.test.tsx              delegates to code-block; language label
✚   callout.test.tsx           level → tone/icon/role
✚   media.test.tsx             allowlisted host renders; non-http(s) dropped; no-referrer set
✚   component-block.test.tsx   valid → right renderer; invalid → dropped; flag-off → raw code-block
```

**Pre-existing (read-only here):** `types/index.ts` (`Message.components`), `chat.store.ts`
(`addComponent`), `chat.schemas.ts` (loose `SseComponentSchema`), `code-block.tsx`,
`sources-panel.tsx`, `lib/sse/*`, `next.config.ts` images allowlist.

**Install:**
```bash
npm i recharts
# react-markdown / remark-gfm / the highlighter are already present (M3).
```

---

## 6. Tasks (ordered)

> Code is copy-pasteable, TS strict, no `any`, types via `z.infer`, semantic Tailwind tokens. Build
> bottom-up: flag → schemas → dispatcher → renderers → render-site. Each layer is testable before the
> next.

### Task 1 — Flag + env wiring (extend the M0 files; mirror M7/M8)

**Goal.** One Zod-validated boolean flag, default OFF, exposed as `flags.richComponents`.
**Files.** `lib/env.ts`, `lib/flags.ts`, `.env.example` (all M0-owned — **extend**, do not rewrite).

`lib/env.ts` — add one field to the existing schema, using the established `FeatureFlag` helper
(the same `z.enum(["true","false"]).transform(...)` pattern M7/M8 reuse):

```ts
// lib/env.ts  (addition inside the existing envSchema — shown in context)
// const FeatureFlag = z.enum(["true", "false"]).default("false").transform((v) => v === "true");
const envSchema = z.object({
  // …existing: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_FEATURE_STREAMING, M7/M8 flags, …

  /** M10: render rich component blocks (table/chart/citation/code/callout/media). Default OFF. */
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: FeatureFlag,
});
// …existing env = envSchema.parse({ … , NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: process.env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS, … })
```

`lib/flags.ts` — add one derived line, exactly like the existing flags:

```ts
// lib/flags.ts  (addition)
import { env } from "@/lib/env";

export const flags = {
  streaming: env.NEXT_PUBLIC_FEATURE_STREAMING,
  // …auth, byok, presignedUpload (M6–M8) …
  richComponents: env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS, // M10 — default false (dark)
} as const;
```

`.env.example` — document it (commented / `false`, ships dark):

```bash
# M10: rich component rendering (table/chart/citation/code/callout/media). Off ⇒ raw block.
NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false
```

**Acceptance.** `flags.richComponents === false` by default; setting an invalid value (not
`"true"`/`"false"`) fails the Zod parse loudly; `tsc --noEmit` passes.

---

### Task 2 — `features/chat/components/rich/component.schemas.ts` (strict union + safe parse + normalizer)

**Goal.** The single render gate: a `z.discriminatedUnion("type", […])` over the six catalog types
that produces a typed `ComponentSpec`, a `safeParseComponent` that **drops** invalid input (returns
`null`), and a `normalizeComponents` helper for a message's whole opaque array. Mirrors 09 §5 +
Appendix C exactly; the `code`/`media` shapes are the §2.4 assumptions.
**Files.** `features/chat/components/rich/component.schemas.ts` (new).

```ts
// features/chat/components/rich/component.schemas.ts
//
// STRICT per-type schemas for the P6 component catalog.
// Source of truth: 09_Phase6_Agentic_Architecture.md §5 + Appendix C.
//   table / chart / citation / callout  -> verbatim from Appendix C
//   code / media                        -> shape ASSUMED (09 §9 open detail); see M10 §2.4
//
// This is the RENDER gate. The loose SseComponentSchema (M2, chat.schemas.ts) stays at the
// wire/store boundary; here we re-validate strictly and DROP anything that doesn't match,
// mirroring the backend's "invalid block dropped, never a 500" rule (09 §5 / §2.1).
import { z } from "zod";

/** table: {"type":"table","columns":[...],"rows":[[...],[...]]} (Appendix C). */
export const tableSchema = z.object({
  type: z.literal("table"),
  columns: z.array(z.string()),
  // Cells are scalars; coerce-tolerant (string|number|boolean|null) and rendered as text.
  rows: z.array(z.array(z.union([z.string(), z.number(), z.boolean(), z.null()]))),
  caption: z.string().optional(),
});

/** chart: {"type":"chart","chart":"bar","x":[...],"series":[{"name","y":[...]}]} (Appendix C). */
export const chartSeriesSchema = z.object({
  name: z.string(),
  y: z.array(z.number()),
});
export const chartSchema = z.object({
  type: z.literal("chart"),
  // Appendix C shows "bar"; line/area are reasonable supersets the renderer also handles.
  chart: z.enum(["bar", "line", "area"]).default("bar"),
  x: z.array(z.union([z.string(), z.number()])),
  series: z.array(chartSeriesSchema).min(1),
  title: z.string().optional(),
});

/** citation: {"type":"citation","items":[{"label","source_id","snippet"}]} (Appendix C). PROVENANCE. */
export const citationItemSchema = z.object({
  label: z.string(),
  source_id: z.string().optional(),
  snippet: z.string().optional(),
  // Web sources may carry a URL; retrieved chunks carry only a source_id.
  url: z.string().url().optional(),
});
export const citationSchema = z.object({
  type: z.literal("citation"),
  items: z.array(citationItemSchema).min(1),
});

/** callout: {"type":"callout","level":"info"|"warning"|"tip","text":...} (Appendix C). */
export const calloutSchema = z.object({
  type: z.literal("callout"),
  level: z.enum(["info", "warning", "tip"]).default("info"),
  text: z.string(),
  title: z.string().optional(),
});

/** code: ASSUMED {"type":"code","language":...,"code":...} (09 §9 open; M10 §2.4). */
export const codeSchema = z.object({
  type: z.literal("code"),
  language: z.string().optional(),
  code: z.string(),
});

/** media: ASSUMED {"type":"media","items":[{"url","alt","caption"}]} (09 §9 open; M10 §2.4). */
export const mediaItemSchema = z.object({
  url: z.string().url(),
  alt: z.string().optional(),
  caption: z.string().optional(),
});
export const mediaSchema = z.object({
  type: z.literal("media"),
  items: z.array(mediaItemSchema).min(1),
});

/** The discriminated union the renderer validates against. */
export const componentSpecSchema = z.discriminatedUnion("type", [
  tableSchema,
  chartSchema,
  citationSchema,
  calloutSchema,
  codeSchema,
  mediaSchema,
]);

export type ComponentSpec = z.infer<typeof componentSpecSchema>;
export type TableSpec = z.infer<typeof tableSchema>;
export type ChartSpec = z.infer<typeof chartSchema>;
export type CitationSpec = z.infer<typeof citationSchema>;
export type CalloutSpec = z.infer<typeof calloutSchema>;
export type CodeSpec = z.infer<typeof codeSchema>;
export type MediaSpec = z.infer<typeof mediaSchema>;

/**
 * Validate one opaque spec (from Message.components) against the strict union.
 * Returns the typed spec, or null to DROP it (unknown/invalid type, malformed payload).
 * Never throws — the surrounding prose + sibling blocks must always render.
 */
export function safeParseComponent(raw: unknown): ComponentSpec | null {
  const parsed = componentSpecSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/**
 * Normalize a message's whole opaque components array into typed, render-ready specs,
 * dropping every invalid block (defense-in-depth over the backend's own drop, §2.5).
 */
export function normalizeComponents(raw: readonly unknown[] | undefined): ComponentSpec[] {
  if (!raw || raw.length === 0) return [];
  const out: ComponentSpec[] = [];
  for (const item of raw) {
    const spec = safeParseComponent(item);
    if (spec) out.push(spec);
  }
  return out;
}
```

**Acceptance.** Each Appendix C example parses to its typed spec; an unknown `type`, a missing
required field, or a non-object returns `null` from `safeParseComponent`; `normalizeComponents` over a
mixed array returns only the valid specs in order. No `any`.

---

### Task 3 — `features/chat/components/rich/component-block.tsx` (dispatcher)

**Goal.** Validate one opaque spec and dispatch to the matching renderer on the validated `type`. When
the flag is **off**, render the spec as a collapsed raw `code-block` (D3) instead of rich UI. Invalid
specs render **nothing** (flag-on) — they were already dropped upstream by `normalizeComponents`, but
the dispatcher also accepts a single raw spec for the flag-off path and for direct/test use.
**Files.** `features/chat/components/rich/component-block.tsx` (new).

```tsx
// features/chat/components/rich/component-block.tsx
"use client";

import { flags } from "@/lib/flags";
import { CodeBlock } from "@/features/chat/components/code-block"; // M3 (reused for raw fallback + code type)
import { safeParseComponent, type ComponentSpec } from "./component.schemas";
import { TableComponent } from "./table";
import { ChartComponent } from "./chart";
import { CitationComponent } from "./citation";
import { CalloutComponent } from "./callout";
import { CodeComponent } from "./code";
import { MediaComponent } from "./media";

interface ComponentBlockProps {
  /** One opaque spec from Message.components (validated here). */
  spec: unknown;
  /** A message-scoped index, used for stable keys by the caller. */
  index?: number;
}

/** Flag-OFF (or unvalidatable) fallback: show the spec as collapsed, copyable raw JSON. */
function RawFallback({ spec }: { spec: unknown }) {
  let json: string;
  try {
    json = JSON.stringify(spec, null, 2);
  } catch {
    return null; // unserializable → nothing (never crash)
  }
  return (
    <details className="my-3 rounded-md border border-border bg-muted/40">
      <summary className="cursor-pointer select-none px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground">
        Rich component (raw)
      </summary>
      <div className="px-3 pb-3">
        <CodeBlock language="json" value={json} />
      </div>
    </details>
  );
}

export function ComponentBlock({ spec }: ComponentBlockProps) {
  // Dark launch: off ⇒ never render rich UI; show the raw block so data is still visible (D3).
  if (!flags.richComponents) return <RawFallback spec={spec} />;

  const parsed: ComponentSpec | null = safeParseComponent(spec);
  if (!parsed) return null; // drop-invalid → prose/siblings still render (D2 / §2.5)

  switch (parsed.type) {
    case "table":
      return <TableComponent spec={parsed} />;
    case "chart":
      return <ChartComponent spec={parsed} />;
    case "citation":
      return <CitationComponent spec={parsed} />;
    case "callout":
      return <CalloutComponent spec={parsed} />;
    case "code":
      return <CodeComponent spec={parsed} />;
    case "media":
      return <MediaComponent spec={parsed} />;
    default: {
      // Exhaustiveness guard: a new union member must add a case above.
      const _never: never = parsed;
      return _never ?? null;
    }
  }
}
```

**Acceptance.** Flag on + valid spec → the right renderer; flag on + invalid spec → `null`; flag off →
`<RawFallback>` with the pretty-printed JSON inside an M3 `code-block`; the `switch` is exhaustive over
`ComponentSpec` (a new type fails to compile until handled).

---

### Task 4 — `features/chat/components/rich/table.tsx`, `callout.tsx`, `code.tsx`

**Goal.** The three simplest renderers: a semantic-token table, a leveled callout box, and the
code-block reuse.
**Files.** `table.tsx`, `callout.tsx`, `code.tsx` (new).

`table.tsx` — render columns/rows; tolerate ragged rows (pad/clip to `columns.length`); cells are
text only (XSS-safe):

```tsx
// features/chat/components/rich/table.tsx
"use client";

import type { TableSpec } from "./component.schemas";

function cellText(v: string | number | boolean | null): string {
  return v === null ? "" : String(v);
}

export function TableComponent({ spec }: { spec: TableSpec }) {
  const { columns, rows, caption } = spec;
  return (
    <div className="my-3 overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-sm">
        {caption && (
          <caption className="px-3 py-2 text-left text-xs text-muted-foreground">{caption}</caption>
        )}
        <thead>
          <tr className="border-b border-border bg-muted/60">
            {columns.map((col, i) => (
              <th key={i} scope="col" className="px-3 py-2 text-left font-medium text-foreground">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b border-border last:border-0 hover:bg-muted/40">
              {columns.map((_, c) => (
                <td key={c} className="px-3 py-2 align-top text-muted-foreground">
                  {cellText(row[c] ?? null)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

`callout.tsx` — `level → tone + icon + role`; semantic tokens (no hardcoded colors):

```tsx
// features/chat/components/rich/callout.tsx
"use client";

import { Info, AlertTriangle, Lightbulb } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CalloutSpec } from "./component.schemas";

const LEVEL = {
  info: { Icon: Info, box: "border-primary/30 bg-primary/5", icon: "text-primary", role: "note" },
  warning: {
    Icon: AlertTriangle,
    box: "border-destructive/30 bg-destructive/5",
    icon: "text-destructive",
    role: "alert",
  },
  tip: { Icon: Lightbulb, box: "border-chart-2/30 bg-chart-2/5", icon: "text-chart-2", role: "note" },
} as const;

export function CalloutComponent({ spec }: { spec: CalloutSpec }) {
  const { level, text, title } = spec;
  const { Icon, box, icon, role } = LEVEL[level];
  return (
    <div role={role} className={cn("my-3 flex gap-3 rounded-md border p-3 text-sm", box)}>
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", icon)} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <p className="mb-0.5 font-medium text-foreground">{title}</p>}
        <p className="whitespace-pre-wrap text-muted-foreground">{text}</p>
      </div>
    </div>
  );
}
```

`code.tsx` — delegate to M3's `code-block` (D4):

```tsx
// features/chat/components/rich/code.tsx
"use client";

import { CodeBlock } from "@/features/chat/components/code-block"; // M3
import type { CodeSpec } from "./component.schemas";

export function CodeComponent({ spec }: { spec: CodeSpec }) {
  return <CodeBlock language={spec.language} value={spec.code} />;
}
```

**Acceptance.** Table renders header + one `<td>` per column for every row (ragged rows don't throw);
callout maps each level to its tone/icon and sets `role="alert"` for `warning`; `code` mounts the M3
code-block with the language label + copy.

---

### Task 5 — `features/chat/components/rich/chart.tsx` (lazy recharts)

**Goal.** Render `chart`/`line`/`area` from `{x, series[]}`, with `recharts` **lazy-loaded**
(`next/dynamic`, `ssr:false`) so it never enters the chat route's first-load bundle (D6), and
**reduced-motion** disabling the entry animation (Risk R6).
**Files.** `features/chat/components/rich/chart.tsx` (new).

```tsx
// features/chat/components/rich/chart.tsx
"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useReducedMotion } from "@/hooks/use-reduced-motion"; // M4
import type { ChartSpec } from "./component.schemas";

// Lazy, client-only: recharts is pulled in ONLY when a chart block actually mounts.
const ResponsiveContainer = dynamic(
  () => import("recharts").then((m) => m.ResponsiveContainer),
  { ssr: false, loading: () => <div className="h-64 w-full animate-pulse rounded-md bg-muted/40" /> },
);
const BarChart = dynamic(() => import("recharts").then((m) => m.BarChart), { ssr: false });
const LineChart = dynamic(() => import("recharts").then((m) => m.LineChart), { ssr: false });
const AreaChart = dynamic(() => import("recharts").then((m) => m.AreaChart), { ssr: false });
const Bar = dynamic(() => import("recharts").then((m) => m.Bar), { ssr: false });
const Line = dynamic(() => import("recharts").then((m) => m.Line), { ssr: false });
const Area = dynamic(() => import("recharts").then((m) => m.Area), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const Tooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const Legend = dynamic(() => import("recharts").then((m) => m.Legend), { ssr: false });

// Semantic palette via the token utilities (resolve per-theme; no hardcoded hex).
const SERIES_COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
];

/** Pivot {x, series[]} → recharts row objects: [{ x, [seriesName]: y }]. */
function toRows(spec: ChartSpec): Array<Record<string, string | number>> {
  return spec.x.map((label, i) => {
    const row: Record<string, string | number> = { x: typeof label === "number" ? label : String(label) };
    for (const s of spec.series) row[s.name] = s.y[i] ?? 0;
    return row;
  });
}

export function ChartComponent({ spec }: { spec: ChartSpec }) {
  const reduced = useReducedMotion();
  const rows = React.useMemo(() => toRows(spec), [spec]);
  const animate = !reduced;

  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
      <XAxis dataKey="x" tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }} />
      <YAxis tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }} />
      <Tooltip
        contentStyle={{
          background: "var(--color-popover)",
          border: "1px solid var(--color-border)",
          borderRadius: 8,
          color: "var(--color-popover-foreground)",
          fontSize: 12,
        }}
      />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  );

  return (
    <figure className="my-3 rounded-md border border-border bg-card p-3">
      {spec.title && (
        <figcaption className="mb-2 text-xs font-medium text-muted-foreground">{spec.title}</figcaption>
      )}
      <div className="h-64 w-full" role="img" aria-label={spec.title ?? "Chart"}>
        <ResponsiveContainer width="100%" height="100%">
          {spec.chart === "line" ? (
            <LineChart data={rows}>
              {axes}
              {spec.series.map((s, i) => (
                <Line
                  key={s.name}
                  type="monotone"
                  dataKey={s.name}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  dot={false}
                  isAnimationActive={animate}
                />
              ))}
            </LineChart>
          ) : spec.chart === "area" ? (
            <AreaChart data={rows}>
              {axes}
              {spec.series.map((s, i) => (
                <Area
                  key={s.name}
                  type="monotone"
                  dataKey={s.name}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  fill={SERIES_COLORS[i % SERIES_COLORS.length]}
                  fillOpacity={0.2}
                  isAnimationActive={animate}
                />
              ))}
            </AreaChart>
          ) : (
            <BarChart data={rows}>
              {axes}
              {spec.series.map((s, i) => (
                <Bar
                  key={s.name}
                  dataKey={s.name}
                  fill={SERIES_COLORS[i % SERIES_COLORS.length]}
                  isAnimationActive={animate}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
```

**Acceptance.** A `bar` spec renders one series per `series[]`; `chart:"line"`/`"area"` switch chart
type; the recharts chunk is **absent** from the chat route first-load JS (verify in `next build`);
`isAnimationActive` is `false` under `prefers-reduced-motion`; colors come from `--color-chart-*`
tokens (theme-correct).

---

### Task 6 — `features/chat/components/rich/citation.tsx` (provenance → sources panel)

**Goal.** Render `citation.items[]` as clickable provenance cards, and **feed the M3 sources panel**
by mapping items → `Source[]` so provenance has one UX (D5). Links are XSS-safe.
**Files.** `features/chat/components/rich/citation.tsx` (new).

```tsx
// features/chat/components/rich/citation.tsx
"use client";

import { SourcesPanel } from "@/features/chat/components/sources-panel"; // M3
import type { CitationSpec } from "./component.schemas";
import type { Source } from "@/types";

/**
 * Map P6 citation items (provenance, 09 §5) → the M3 Source shape so they render through the
 * existing sources panel. A retrieved chunk has only source_id; a web source may have a url.
 */
function toSources(spec: CitationSpec): Source[] {
  return spec.items.map((item, i) => ({
    id: item.source_id ?? `citation-${i}`,
    title: item.label,
    snippet: item.snippet,
    url: item.url, // only http(s) URLs survived the strict schema (z.string().url())
  }));
}

export function CitationComponent({ spec }: { spec: CitationSpec }) {
  const sources = toSources(spec);
  // Reuse the M3 sources panel: same collapsible "Referenced N …" + cards, one provenance UX.
  return <SourcesPanel sources={sources} count={sources.length} />;
}
```

**Acceptance.** Each item becomes a card via the M3 panel; an item with a `url` renders a safe
external link (the panel sets `rel="noopener noreferrer"`); an item with only `source_id` renders a
non-link card; the count matches `items.length`.

> **Render-order note (§Task 8):** because `citation` renders the sources panel, when an answer has
> *both* M3-synthesized `sources` (from `context_count`) **and** a `citation` component, prefer the
> citation provenance (it is the precise, P6-authored channel). The `chat-message` wiring (Task 8)
> suppresses the generic synthesized sources panel for that message when a `citation` component is
> present, so provenance isn't shown twice.

---

### Task 7 — `features/chat/components/rich/media.tsx` (allowlisted images / gallery)

**Goal.** Render `media.items[]` as images (single or a responsive gallery) with **SSRF/XSS-safe**
loading: `http(s)`-only URLs (already enforced by `z.string().url()` + a protocol re-check),
`referrerPolicy="no-referrer"`, `loading="lazy"`, and reliance on the **M9 `next.config` images
allowlist**. Uses a plain `<img>` (the renderer is client-side Markdown-adjacent; the allowlist still
governs any `next/image` path, per M9 Task 6).
**Files.** `features/chat/components/rich/media.tsx` (new).

```tsx
// features/chat/components/rich/media.tsx
"use client";

import { cn } from "@/lib/utils";
import type { MediaSpec } from "./component.schemas";

/** Defense-in-depth over the schema: only ever load http(s) URLs (no data:, blob:, javascript:). */
function isSafeHttpUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

export function MediaComponent({ spec }: { spec: MediaSpec }) {
  const items = spec.items.filter((it) => isSafeHttpUrl(it.url));
  if (items.length === 0) return null; // nothing safe to show → drop (prose still renders)

  const isGallery = items.length > 1;
  return (
    <div
      className={cn(
        "my-3",
        isGallery ? "grid grid-cols-2 gap-2 sm:grid-cols-3" : "max-w-full",
      )}
    >
      {items.map((it, i) => (
        <figure key={i} className="overflow-hidden rounded-md border border-border bg-muted/40">
          {/* eslint-disable-next-line @next/next/no-img-element -- external, allowlisted, no-referrer */}
          <img
            src={it.url}
            alt={it.alt ?? ""}
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-auto w-full object-cover"
          />
          {it.caption && (
            <figcaption className="px-2 py-1 text-xs text-muted-foreground">{it.caption}</figcaption>
          )}
        </figure>
      ))}
    </div>
  );
}
```

**Acceptance.** An allowlisted `https` image renders with `referrerPolicy="no-referrer"` +
`loading="lazy"`; a non-`http(s)` URL is filtered out (and an all-unsafe `items` renders nothing); two+
items render the gallery grid; a broken image fails closed (no crash).

---

### Task 8 — Wire `<ComponentBlock>` into `chat-message.tsx` (after the body, flag-gated)

**Goal.** Render `message.components` after the Markdown body — the seam M3 left (M3 §6 order:
badge → steps → **body** → sources → actions). M10 inserts the component list **between body and
sources**. Flag-off ⇒ each spec renders as the collapsed raw `code-block` (the dispatcher handles
this); flag-on ⇒ rich. When a `citation` component is present, suppress the generic synthesized
sources panel (Task 6 note).
**Files.** `features/chat/components/chat-message.tsx` (edit — additive insert + one guard).

Add the import and a small derived value, then insert the block. Illustrative diff (preserve all M3
markup):

```tsx
// features/chat/components/chat-message.tsx  (additions)
import { ComponentBlock } from "./rich/component-block";
import { normalizeComponents } from "./rich/component.schemas";

// …inside ChatMessageImpl, after `const isUser = …`:
const components = normalizeComponents(message.components);
// A P6 citation block is the precise provenance channel; if present, don't ALSO show the
// generic synthesized sources panel for this message (Task 6 note).
const hasCitation = components.some((c) => c.type === "citation");
```

```tsx
        {/* …existing Markdown body block (M3) stays exactly as-is… */}

        {/* M10: rich component blocks, after the body. Flag-gated inside <ComponentBlock>.
            Render the RAW (opaque) message.components so the flag-off path can pretty-print them;
            the dispatcher validates per spec when the flag is on. */}
        {!isUser && message.components && message.components.length > 0 && (
          <div className="space-y-1">
            {message.components.map((spec, i) => (
              <ComponentBlock key={i} spec={spec} index={i} />
            ))}
          </div>
        )}

        {/* Sources (M3) — suppressed when a P6 citation component already shows provenance. */}
        {!isUser && !hasCitation && (
          <SourcesPanel sources={message.sources} count={message.sourcesCount} />
        )}

        {/* …existing actions block (M3) stays as-is… */}
```

> **Memo note (M3 D3 / R2):** M3 wraps `ChatMessage` in `React.memo` with a custom comparator. Add
> `a.components === b.components` to that comparator so a new `component` event (which `addComponent`
> appends as a **new array reference**, M1 immutable-update contract) re-renders the message. Without
> it, a late-arriving component block wouldn't paint.

**Acceptance.** With the flag on, a message carrying a valid `table`/`chart`/etc. renders the rich
component after its prose; with the flag off, the same message renders the collapsed raw block; a
message with a `citation` component shows provenance once (no duplicate sources panel); a message with
no `components` is visually unchanged from M3/M9.

---

## 7. Feature-Flag Behavior Matrix

`NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` → `flags.richComponents`. The flag is read **only** inside
`<ComponentBlock>` (one choke point), so behavior is uniform.

| Situation | Flag OFF (default — dark) | Flag ON |
|---|---|---|
| Message has **no** `components` | Unchanged from M9 (prose + M3 sources) | Same — `<ComponentBlock>` never renders |
| `components` has a **valid** `table`/`chart`/`citation`/`callout`/`code`/`media` | Collapsed **raw JSON** in an M3 `code-block` (`<details>`), copyable | The **rich** renderer for that type |
| `components` has an **invalid / unknown-type** block | Collapsed raw JSON (so you can see the malformed block) | **Dropped** (renders nothing); prose + siblings unaffected |
| Mixed valid + invalid blocks | Each shown raw | Valid ones render rich; invalid ones dropped |
| `citation` block present | Raw JSON (sources panel still shows M3 synthesized sources) | Provenance cards via M3 panel; generic synthesized panel suppressed |
| Prose (Markdown body) | Always renders (never gated) | Always renders |
| First-load JS bundle | No `recharts` (lazy) | No `recharts` until a `chart` actually mounts |

**Invariant:** flag OFF ⇒ **no rich UI and no crash** — the prose path and every other M3/M9 behavior
are byte-for-byte unchanged; the only addition is a collapsed, opt-in raw block.

---

## 8. Testing & Verification

**Stack:** Vitest + React Testing Library + `@testing-library/user-event`; MSW for the end-to-end
`component`-event path (the **M5** test stack). Mock `flags` per test to exercise both flag states.

### Per-renderer (RTL)

- **`table.tsx`**: renders a `<th>` per column and a `<td>` per column for each row; a **ragged row**
  (fewer cells than columns) renders empty cells and does not throw; `null` cells render empty; cell
  content is text (no HTML injection from a cell string).
- **`chart.tsx`**: a `bar` spec renders (assert the responsive container / a series element appears
  after the dynamic import resolves); `chart:"line"`/`"area"` select the right chart;
  `isAnimationActive` is `false` when `useReducedMotion()` is mocked `true`; the loading skeleton
  shows before the lazy chunk resolves.
- **`citation.tsx`**: `items[]` → cards through the M3 sources panel; an item with `url` is a link
  with `rel="noopener noreferrer"`; an item with only `source_id` is a non-link card; count =
  `items.length`.
- **`code.tsx`**: delegates to `code-block` — asserts the language label + a copy control (reuse M3's
  `code-block` test doubles).
- **`callout.tsx`**: each `level` → its icon + tone class; `warning` sets `role="alert"`, `info`/`tip`
  set `role="note"`.
- **`media.tsx`**: an allowlisted `https` URL renders an `<img>` with `referrerPolicy="no-referrer"`
  and `loading="lazy"`; a `javascript:`/`data:` URL is filtered out; an all-unsafe `items` renders
  nothing; 2+ items render the gallery grid.

### Dispatcher + flag (`component-block.tsx`)

- Flag **on** + each valid type → the matching renderer is in the tree.
- Flag **on** + an **invalid spec** (unknown `type`, missing field) → renders **nothing** (drop).
- Flag **off** + any spec → a `<details>` raw block containing an M3 `code-block` with the
  pretty-printed JSON (assert the JSON text + the disclosure).
- `safeParseComponent`/`normalizeComponents` unit tests: each Appendix C sample → typed spec; a mixed
  array → only valid specs, order preserved.

### End-to-end (MSW `component` event)

- Extend the **M2** MSW SSE handler (`test/msw/handlers.ts`) to emit a `component` event mid-stream
  (e.g. a `table` block), then `done`. With the streaming flag on (M9) **and** rich-components on,
  drive `useStreamingChat` (or render `<ChatScreen>`) and assert: the prose renders, `addComponent`
  populated `message.components`, and `<ComponentBlock>` rendered the **table** after the body. Repeat
  with rich-components **off** → assert the raw `code-block` fallback. Repeat with a **malformed**
  `component` payload → assert it is dropped and the prose still renders (no crash) — the §2.5 rule.
- **Flag-off parity:** with `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` unset, a message with `components`
  shows only the raw fallback and the rest of the chat surface is identical to M9.

### Build / a11y

- `next build`: confirm `recharts` is **not** in the chat route's first-load JS (it is `next/dynamic`,
  `ssr:false`).
- `axe` on a message containing each component type (both themes): table has proper header scope;
  chart container has an `aria-label`/`role="img"`; callout has the right `role`; images have `alt`;
  zero serious/critical violations.
- `npm run lint`, `tsc --noEmit` (no `any`), `vitest run` all green.

---

## 9. Risks & Gotchas

- **R1 — XSS via a model-authored spec field.** The catalog is the security boundary (09 Decision 2:
  no model-authored HTML). **Never** `dangerouslySetInnerHTML` a spec field; render everything as text
  or as a typed prop of a fixed component. Tables render cell strings as text; callout/caption are
  text; code goes through the highlighter (text). Covered by the table/cell injection test.
- **R2 — SSRF / unsafe URLs on `media` (and `citation` links).** A spec URL could be `data:`,
  `blob:`, `javascript:`, or an internal host. Mitigations stack: `z.string().url()` in the schema, an
  explicit `http(s)`-only re-check in `media.tsx` (`isSafeHttpUrl`), `referrerPolicy="no-referrer"`,
  `rel="noopener noreferrer"` on links, **and** the M9 `next.config` `images.remotePatterns`
  allowlist (no `**` wildcard) for any `next/image` path. An off-allowlist image fails closed (broken
  image), never a crash.
- **R3 — Invalid / partial JSON dropped, not crashed.** The frontend re-validates (strict union) and
  drops on failure (`safeParseComponent → null`), mirroring the backend §5 rule. The renderer must
  **never** assume a field exists beyond what the schema guarantees. The end-to-end malformed-payload
  test is the gate. (Partial blocks are a non-issue — P6 buffers until the fence closes and emits one
  whole block per event, §2.2.)
- **R4 — Chart lib bundle size.** `recharts` is large; eager-importing it would bloat the chat route
  for the common no-chart answer. **Lazy via `next/dynamic({ssr:false})`** (D6); verify with
  `next build` that it's absent from first-load JS. Keep the loading skeleton so the layout doesn't
  jump while the chunk arrives.
- **R5 — Schema drift vs. the backend catalog.** The strict union is the **single source of truth** on
  the client and must track 09 §5 / Appendix C. The `code` and `media` shapes are **assumptions**
  (§2.4, flagged in the schema comments) because 09 §9 lists the exact pydantic schemas as an open
  detail — when the backend pins them, reconcile **only** `codeSchema`/`mediaSchema`. Until then,
  drop-invalid keeps a shape mismatch safe (degrades to prose). Add a `// SYNC:` comment pointing at
  09 Appendix C on each schema so the coupling is obvious in review.
- **R6 — Reduced motion for the chart.** recharts animates series on mount by default. Gate it with
  M4's `useReducedMotion()` → `isAnimationActive={!reduced}`. No other component animates (callout/
  table/citation/media are static; the only transitions are the M3 `code-block`/sources collapsibles,
  already `motion-reduce:`-gated).
- **R7 — Duplicate provenance.** A message can have both M3-synthesized `sources` and a P6 `citation`
  block. Showing both is confusing. Task 8 suppresses the generic sources panel when a `citation`
  component is present (prefer the precise P6 channel). Covered by a `chat-message` test.
- **R8 — `React.memo` comparator (M3 R2).** `addComponent` appends a **new** `components` array
  reference (M1 immutable-update contract). M3's message comparator must include
  `a.components === b.components` or a late-arriving block won't paint. Verify with the end-to-end test
  (component arrives after the first paint).
- **R9 — Dark-launch leakage.** The flag is read **only** in `<ComponentBlock>`. Do not branch on
  `flags.richComponents` in `chat-message` (it always renders the list; the dispatcher decides
  raw-vs-rich), so there is exactly one place to audit and flipping the flag changes nothing else.

---

## 10. Exit Criteria (checkable)

- [ ] `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` added to `lib/env.ts` (Zod `FeatureFlag`, default
      `false`), `flags.richComponents` derived in `lib/flags.ts`, documented in `.env.example`.
- [ ] `features/chat/components/rich/component.schemas.ts` exports a `z.discriminatedUnion("type", …)`
      over `table`/`chart`/`citation`/`callout`/`code`/`media`, the `ComponentSpec` type, and
      `safeParseComponent` + `normalizeComponents` that **drop** invalid blocks; each Appendix C sample
      parses, unknown/invalid returns `null`.
- [ ] `component-block.tsx` dispatches on validated `type`; flag-off ⇒ collapsed raw `code-block`;
      flag-on + invalid ⇒ nothing; the `switch` is exhaustive (compile-time `never` guard).
- [ ] All six renderers exist and are semantic-token styled: `table` (ragged-row safe),
      `chart` (lazy recharts, reduced-motion-gated, `--color-chart-*`), `citation` (→ M3 sources
      panel, safe links), `code` (reuses M3 `code-block`), `callout` (leveled `role`), `media`
      (allowlisted `http(s)` `<img>`, `no-referrer`, gallery).
- [ ] `chat-message.tsx` renders `message.components` via `<ComponentBlock>` **after** the body;
      `citation` present ⇒ generic sources panel suppressed; the `React.memo` comparator includes
      `components`.
- [ ] Flag **off** ⇒ no rich UI, no crash; raw fallback shows the spec JSON; the rest of the chat
      surface is byte-for-byte M9. Flag **on** ⇒ the catalog renders.
- [ ] **Drop-invalid proven**: a malformed `component` payload (MSW) is dropped and the prose + sibling
      blocks still render (no 500-equivalent crash) — frontend mirror of 09 §5.
- [ ] `recharts` is **absent** from the chat route first-load JS (`next build`); it loads only when a
      `chart` mounts.
- [ ] `code`/`media` schemas carry a `// SYNC:` / "ASSUMED" comment citing 09 §2.4 / Appendix C as a
      flagged contract assumption.
- [ ] All §8 RTL + dispatcher + end-to-end (MSW `component`) tests pass; `axe` clean in both themes.
- [ ] `npm run lint`, `tsc --noEmit` (no `any`), `vitest run`, `next build` all pass.

---

## 11. Commit Plan

Milestone-sized, conventional commits on the milestone branch (one concern each; everything lands
behind `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false`, so each commit is a runtime no-op until the flag
is flipped):

1. `feat(flags): add NEXT_PUBLIC_FEATURE_RICH_COMPONENTS → flags.richComponents (dark, default off)`
   — Task 1 (`lib/env.ts`, `lib/flags.ts`, `.env.example`).
2. `feat(rich): add strict component discriminated-union schemas + safeParse/normalize`
   — Task 2 (`features/chat/components/rich/component.schemas.ts`).
3. `feat(rich): add ComponentBlock dispatcher with flag-off raw-code-block fallback`
   — Task 3 (`component-block.tsx`).
4. `feat(rich): add table, callout, and code (reuse M3 code-block) renderers`
   — Task 4 (`table.tsx`, `callout.tsx`, `code.tsx`).
5. `feat(rich): add lazy recharts chart renderer (next/dynamic, reduced-motion)`
   — Task 5 (`chart.tsx`) + `npm i recharts`.
6. `feat(rich): add citation renderer feeding the M3 sources panel`
   — Task 6 (`citation.tsx`).
7. `feat(rich): add allowlisted media/gallery renderer (no-referrer, http(s) only)`
   — Task 7 (`media.tsx`).
8. `feat(chat): render message.components via ComponentBlock after the body`
   — Task 8 (`chat-message.tsx`: insert + memo comparator + citation/sources suppression).
9. `test(rich): RTL per-renderer + dispatcher + MSW component-event end-to-end + drop-invalid`
   — Section 8 suites.

> Citations: backend output contract — [`../../../Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md`](../../../Python-Agentic-RAG-Backend/docs/09_Phase6_Agentic_Architecture.md)
> §5 "Output contract" + Appendix C "Component JSON examples" (`table`/`chart`/`citation`/`callout`
> shapes verbatim; `code`/`media` shapes assumed per §9 open build-time details). Frontend seams —
> M1 (`Message.components` + `addComponent`), M2 (`component` SSE event + loose `SseComponentSchema` +
> `onComponent → addComponent`), M3 (`chat-message` render seam + `code-block` + `sources-panel`),
> M9 (real SSE flip + `next.config` images allowlist). Each commit message ends with the session
> trailer per repo convention.
