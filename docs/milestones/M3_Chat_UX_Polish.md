# M3 — Chat UX Polish

Replace the prototype chat surface with a production-grade, theme-correct UI: a refactored,
memoized `chat-message` that delegates to extracted `code-block`, `route-badge`, `thinking-steps`,
`sources-panel`, and `message-actions` subcomponents; an autosizing input; copy/retry affordances;
and skeleton loading states. Every hardcoded `slate/blue/white` class is migrated to the semantic
Tailwind v4 tokens defined in `app/globals.css`, so dark mode is correct by construction and the
panels are ready to render live data when streaming lands in M9.

**Status:** Planned · **Depends on:** M1 (feature-folder architecture, `useChat` facade, store) and
M2 (unified `Message` shape — `steps`/`sources`/`status`/`route`/`sourcesCount`) · **Unlocks:** M4
(motion layer — `framer-motion` enter/exit, streaming caret, staggered step reveal animate the
static components built here).

---

## 1. Objective & Scope

### In scope
- **New / extracted components** under `features/chat/components/`: `code-block`, `route-badge`,
  `thinking-steps`, `sources-panel`, `message-actions`, plus refactored `chat-message`,
  `chat-input`, `empty-state`, `message-loading`.
- **Semantic-token migration**: eliminate every hardcoded `slate-*`, `blue-*`, `white`,
  `green-*`, `red-*` class across the chat surface and replace with `bg-card`,
  `text-muted-foreground`, `border-border`, `bg-primary`, `bg-muted`, `text-foreground`, etc.
- **Autosize input**: swap the fixed-height `<Textarea>` for `react-textarea-autosize`, preserving
  Enter-to-send / Shift+Enter-newline, the web-search toggle, and file upload.
- **Copy / retry**: `useCopyToClipboard` hook (Clipboard API + transient "copied" state + toast);
  `message-actions` wires copy-answer and retry (via `useChat().retry`).
- **Lazy code-block**: extract the inline code renderer; lazy-load `react-syntax-highlighter` via
  `next/dynamic({ ssr: false })` with a plain `<pre>` fallback; add a per-block copy button and a
  language label.
- **Panels render synthesized data today**: `thinking-steps` renders the single synthesized "done"
  step; `sources-panel` renders `sourcesCount` / `context_count` ("Referenced N chunks").
- **Skeleton states**: shadcn `skeleton`-based `message-loading`.
- **shadcn additions**: `dropdown-menu`, `tooltip`, `collapsible`, `skeleton`, `command`.

### Out of scope (deferred)
- **Animations beyond simple CSS `transition-*`** — `AnimatePresence`, `layout` animation,
  streaming caret, staggered step reveal, spring sidebar are **M4**.
- **Real token streaming and live `status` events** — the panels consume the *unified shape* but
  the data is still synthesized by the blocking path; live wiring is **M9** (flag flip).
- **`use-reduced-motion`** — referenced by M4; only CSS `motion-reduce:` variants are used here.
- Auth, BYOK, presigned uploads, session list — later milestones.

---

## 2. Decisions & Rationale

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Semantic tokens over hardcoded `slate/blue/white`** | `chat-message.tsx:26` hardcodes `bg-white border-slate-100` with a separate `dark:` override on every element — brittle and frequently wrong in dark mode. `globals.css:46-113` already defines a full token set (`--card`, `--muted`, `--primary`, `--border`, `--muted-foreground`) for both `:root` and `.dark`. Using `bg-card`/`text-muted-foreground`/`border-border` makes dark mode correct with **zero** `dark:` overrides. |
| D2 | **Lazy `react-syntax-highlighter` via `next/dynamic({ ssr:false })`** | `chat-message.tsx:12-13` imports `Prism` + the `oneDark` style **eagerly** at module top. `react-syntax-highlighter` + Prism + theme is ~½ MB of JS pulled into the chat route's first-load bundle even for messages with no code. Wrapping it in `next/dynamic` with `ssr:false` defers the chunk until a code block actually mounts; a `<pre>` fallback renders instantly and during SSR. |
| D3 | **`React.memo` the message + memoize the ReactMarkdown `components` map** | Today every message re-parses markdown on each parent render. When M9 streams tokens, the assistant message re-renders on every token; an unmemoized `components` object forces ReactMarkdown to rebuild its renderer tree each time. Memoizing the map (module-scope/`useMemo`) and wrapping the message in `React.memo` keyed on stable props keeps re-render cost proportional to the *changed* message only. The `content` prop stays a stable string reference per render (see Risk R2). |
| D4 | **`react-textarea-autosize`** | The current input (`chat-input.tsx:103-104`) fakes autosize with `min-h/max-h` + an inline `style={{height:"40px"}}` that never grows. `react-textarea-autosize` grows row-by-row between `minRows`/`maxRows`, is SSR-safe, and is already the stack-approved input lib in the plan. |
| D5 | **Collapsible `thinking-steps` and `sources-panel`** | Today there is a single static sources footer (`chat-message.tsx:111-117`) and no steps panel. shadcn `collapsible` (Radix) gives us accessible `aria-expanded` disclosure now; it renders one synthesized "done" step / N sources today and scales to a live, growing list of streamed steps in M9 without structural change. |
| D6 | **Panels render synthesized data today** | The M2 `useBlockingChat` path synthesizes a single `{ stage:"done", status:"done" }` step and maps `context_count → sourcesCount`/`sources`. Building the panels now (against the real shape) means M9 is a **flag flip + data source swap**, not a UI rewrite — the plan's core "architect once" principle. |
| D7 | **`message-actions` retry calls `useChat().retry`** | The facade already exposes `retry`; actions stay presentational and call into the store action, so the same component works for blocking (re-run mutation) and streaming (re-open SSE) without change. |
| D8 | **`useCopyToClipboard` shared hook** | Copy is needed by both `code-block` (copy snippet) and `message-actions` (copy answer). One hook owns the `navigator.clipboard.writeText` call, the 2s "copied" reset timer, and the toast, avoiding duplicated state machines. |

---

## 3. Current-State Snapshot

All paths relative to `/home/user/typescript-agentic-rag-frontend`.

- **`components/chat/chat-message.tsx`**
  - `:12-13` — **eager** `import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"` and `import { oneDark } from ".../prism"`. Not code-split.
  - `:26` — hardcoded `bg-white border border-slate-100 shadow-sm dark:bg-slate-900/50 dark:border-slate-800`.
  - `:33-34` — avatar `bg-blue-600` (user) / `bg-slate-700` (assistant), `text-white`.
  - `:46` — name `text-slate-800 dark:text-slate-200`.
  - `:51` — route rendered as a bare `<Badge variant="outline">` with `text-slate-500` — no typed variant mapping.
  - `:60` — body `text-slate-700/600 dark:text-slate-300/400`.
  - `:71-96` — **inline** code-block renderer using `any`-typed `code({...}: any)`, hardcoded hex `bg-[#282c34]`/`bg-[#21252b]`, `border-slate-700/800`, `text-slate-400`, no copy button.
  - `:92` — inline code `bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200`.
  - `:99` — links `text-blue-600`.
  - `:113` — sources footer `border-slate-100 dark:border-slate-800`, static, not collapsible, no steps panel, no actions.
  - Whole component is **not memoized**.
- **`components/chat/chat-input.tsx`**
  - `:5` imports the fixed shadcn `Textarea`; `:103-104` fakes sizing with `min-h-[40px] max-h-[200px]` + inline `style={{height:"40px"}}`.
  - `:54,56` — `dark:border-slate-800` overrides; `:87` web-search active state hardcodes `text-blue-500 bg-blue-50 hover:bg-blue-100 hover:text-blue-600`.
  - `:71-72,91` — uses `title=` attributes, not accessible tooltips.
- **`components/chat/sidebar.tsx`**
  - `:13` — `bg-slate-50/50 dark:bg-slate-900/50 dark:border-slate-800`.
  - `:17` — logo `bg-blue-600 text-white`; `:33,53` — `text-slate-500`; `:52` — card `bg-white`; `:109` — reset button `text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30`.
- **`components/chat/empty-state.tsx`**
  - `:6` — `bg-slate-100 dark:bg-slate-800`; `:7` — `text-blue-500 fill-blue-500`; `:17,27` — `hover:bg-slate-50 dark:hover:bg-slate-800`; `:18` — `text-blue-600`; `:28` — `text-green-600`.
- **`components/chat/message-loading.tsx`**
  - `:6` — `bg-white border-slate-100 dark:bg-slate-900/50 dark:border-slate-800` + `animate-pulse`; `:9` — `bg-slate-700`; `:16-20` — fake lines `bg-slate-200/100 dark:bg-slate-800` (hand-rolled skeleton, no shadcn `Skeleton`).
- **`app/page.tsx`**
  - `:105` — root `bg-slate-50 dark:bg-slate-950`; `:120` — `border-slate-100 dark:border-slate-800`; `:128,130` — `hover:bg-slate-100 dark:hover:bg-slate-800`, `text-slate-500`. (Page is gutted to a thin shell in M1; M3 only touches the few residual hardcoded classes here, not the logic.)
- **`types/index.ts`** — `Message` (`:25-32`) currently has only `route?`/`sourcesCount?`. **M2 extends it** with `steps?`, `sources?`, `status?`. M3 consumes that extended shape (see §6 for the assumed type).
- **`app/globals.css`** — tokens **already present** for light + dark: `--card`/`--card-foreground` (`:51`/`:85`), `--muted`/`--muted-foreground` (`:58-59`/`:92-93`), `--primary`/`--primary-foreground` (`:54-55`/`:88-89`), `--secondary`, `--accent`, `--border` (`:63`/`:97`), `--destructive` (`:62`/`:96`), `--ring`. `@theme inline` (`:6-44`) maps each to a `--color-*` utility, so `bg-card`, `text-muted-foreground`, `border-border`, `bg-primary`, `bg-muted`, `bg-destructive`, etc. are all valid Tailwind v4 classes today. **No new tokens are required by M3.**
- **`package.json`** — `react-markdown@10`, `remark-gfm@4`, `react-syntax-highlighter@16` (+ `@types`) already present. **Missing:** `react-textarea-autosize`, and the shadcn primitives `dropdown-menu`/`tooltip`/`collapsible`/`skeleton`/`command` (and their Radix deps). `@tailwindcss/typography` is **not** installed — the `prose` classes currently render via `tw-animate-css`/Tailwind defaults only (see Risk R4).

---

## 4. Semantic Token Migration Map

Replace every class on the left with the token on the right and **delete the paired `dark:`
override** — the token resolves per-theme automatically.

### `chat-message.tsx`
| Hardcoded (current) | Semantic replacement |
|---|---|
| `bg-white border border-slate-100 shadow-sm dark:bg-slate-900/50 dark:border-slate-800` (assistant bubble) | `bg-card border border-border shadow-sm` |
| `bg-primary/5` (user bubble) | *keep* (already semantic) |
| avatar `bg-blue-600 text-white` (user) | `bg-primary text-primary-foreground` |
| avatar `bg-slate-700 text-white` (assistant) | `bg-muted text-muted-foreground` |
| name `text-slate-800 dark:text-slate-200` | `text-foreground` |
| body user `text-slate-700 dark:text-slate-300` | `text-foreground/90` |
| body assistant `text-slate-600 dark:text-slate-400` | `text-muted-foreground` |
| inline code `bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200` | `bg-muted text-foreground` |
| link `text-blue-600` | `text-primary` |
| sources border `border-slate-100 dark:border-slate-800` | `border-border` |
| code-block chrome `bg-[#282c34]` / `bg-[#21252b]` / `border-slate-700` / `text-slate-400` | `bg-muted` / `bg-muted/60` / `border-border` / `text-muted-foreground` (highlighter keeps `oneDark` palette inside) |

### `chat-input.tsx`
| Hardcoded | Semantic replacement |
|---|---|
| `border-t dark:border-slate-800` | `border-t border-border` |
| pill `dark:border-slate-800` | `border-border` |
| web-search active `text-blue-500 bg-blue-50 hover:bg-blue-100 hover:text-blue-600` | `text-primary bg-primary/10 hover:bg-primary/15` |
| web-search inactive `text-muted-foreground hover:text-foreground` | *keep* |

### `sidebar.tsx`
| Hardcoded | Semantic replacement |
|---|---|
| `bg-slate-50/50 dark:bg-slate-900/50 dark:border-slate-800` | `bg-sidebar border-r border-sidebar-border text-sidebar-foreground` |
| logo `bg-blue-600 text-white` | `bg-primary text-primary-foreground` |
| toggle icon `text-slate-500` | `text-muted-foreground` |
| card `bg-white` | `bg-card` |
| section label `text-slate-500` | `text-muted-foreground` |
| reset `text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30` | `text-destructive hover:text-destructive hover:bg-destructive/10` |

### `empty-state.tsx`
| Hardcoded | Semantic replacement |
|---|---|
| `bg-slate-100 dark:bg-slate-800` (icon halo) | `bg-muted` |
| `text-blue-500 fill-blue-500` | `text-primary fill-primary` |
| card `hover:bg-slate-50 dark:hover:bg-slate-800` | `hover:bg-muted` |
| `text-blue-600` (Analyze) | `text-primary` |
| `text-green-600` (Web Search) | `text-chart-2` *(or `text-primary`)* |

### `message-loading.tsx`
| Hardcoded | Semantic replacement |
|---|---|
| `bg-white border-slate-100 dark:bg-slate-900/50 dark:border-slate-800` | `bg-card border border-border` |
| avatar `bg-slate-700 text-white` | `bg-muted text-muted-foreground` |
| fake lines `bg-slate-200/100 dark:bg-slate-800` | shadcn `<Skeleton />` (uses `bg-accent`/`bg-muted`) |

### `app/page.tsx` (residual only)
| Hardcoded | Semantic replacement |
|---|---|
| `bg-slate-50 dark:bg-slate-950` | `bg-background` |
| `border-slate-100 dark:border-slate-800` | `border-border` |
| `hover:bg-slate-100 dark:hover:bg-slate-800` | `hover:bg-accent` |
| `text-slate-500` (menu icon) | `text-muted-foreground` |

> **Rule of thumb:** surfaces → `bg-card` (raised) / `bg-background` (page) / `bg-muted` (inset);
> text → `text-foreground` (primary) / `text-muted-foreground` (secondary); lines → `border-border`;
> brand/accent → `bg-primary`/`text-primary`; danger → `*-destructive`. Never write a `dark:` color
> override for these — the token already flips.

---

## 5. Target File Tree (delta)

```
features/chat/components/
  chat-message.tsx        # REFACTORED: React.memo + memoized markdown map; composes the below
  code-block.tsx          # NEW: lazy highlighter + copy + language label
  route-badge.tsx         # NEW: typed RouteType → variant/tone map
  thinking-steps.tsx      # NEW: collapsible step list (synthesized "done" today)
  sources-panel.tsx       # NEW: collapsible "Referenced N chunks"
  message-actions.tsx     # NEW: copy + retry, tooltip icon buttons
  chat-input.tsx          # REFACTORED: react-textarea-autosize, semantic tokens
  empty-state.tsx         # REFACTORED: semantic tokens
  message-loading.tsx     # REFACTORED: shadcn Skeleton

hooks/
  use-copy-to-clipboard.ts  # NEW: Clipboard API + copied-timeout + toast

components/ui/              # shadcn add (generated)
  dropdown-menu.tsx          # NEW (shadcn)
  tooltip.tsx                # NEW (shadcn)
  collapsible.tsx            # NEW (shadcn)
  skeleton.tsx               # NEW (shadcn)
  command.tsx                # NEW (shadcn)
```

> The legacy `components/chat/*` files are superseded by `features/chat/components/*` (the M1
> move). If M1 already relocated them, edit in place; if not, create the feature-folder versions and
> update the import in `app/page.tsx`. `command` is added now (per plan) for a future command
> palette; it generates the file but is not yet mounted.

**Install commands** (document in PR body):
```bash
npx shadcn@latest add dropdown-menu tooltip collapsible skeleton command
npm i react-textarea-autosize
# react-markdown, remark-gfm, react-syntax-highlighter already present
# If prose styling is desired (Risk R4): npm i -D @tailwindcss/typography
```
shadcn `tooltip` requires a `<TooltipProvider>` — mount it once in `app/providers.tsx` (created in
M0/M1) wrapping the tree, alongside the existing `ThemeProvider`/`Toaster`.

---

## 6. Tasks (ordered)

> Assumed M2 type (already merged before M3). If a field is absent, fall back as noted.

```ts
// types/index.ts (extended in M2 — shown for reference, NOT edited in M3)
export type StepStage = "routing" | "retrieving" | "searching" | "synthesizing" | "done";
export type StepStatus = "pending" | "active" | "done" | "error";

export interface ThinkingStep {
  id: string;
  stage: StepStage;
  label: string;
  status: StepStatus;
}

export interface Source {
  id: string;
  title?: string;
  snippet?: string;
  url?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: RouteType;
  sourcesCount?: number;
  steps?: ThinkingStep[];   // M2: blocking path synthesizes [{stage:"done",status:"done"}]
  sources?: Source[];       // M2: optional; sourcesCount is the always-present count
  status?: "streaming" | "done" | "error"; // M2
  timestamp: Date;
}
```

---

### Task 1 — Add shadcn UI primitives

**Goal:** generate the Radix-backed primitives the new components depend on.
**Files:** generated under `components/ui/`.

```bash
npx shadcn@latest add dropdown-menu tooltip collapsible skeleton command
```
This writes `dropdown-menu.tsx`, `tooltip.tsx`, `collapsible.tsx`, `skeleton.tsx`, `command.tsx` and
installs `@radix-ui/react-dropdown-menu`, `@radix-ui/react-tooltip`, `@radix-ui/react-collapsible`,
and `cmdk`. Then mount the tooltip provider once:

```tsx
// app/providers.tsx — add to the existing provider stack
import { TooltipProvider } from "@/components/ui/tooltip";
// ...
<TooltipProvider delayDuration={300}>{children}</TooltipProvider>
```

---

### Task 2 — `hooks/use-copy-to-clipboard.ts`

**Goal:** one reusable copy primitive: writes to clipboard, exposes a transient `copied` flag (auto
-resets after `timeout`), and fires a toast.
**Files:** `hooks/use-copy-to-clipboard.ts` (new).

```ts
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface UseCopyToClipboardOptions {
  /** ms before `copied` resets to false. Default 2000. */
  timeout?: number;
  /** show a sonner toast on success. Default true. */
  showToast?: boolean;
}

interface UseCopyToClipboardReturn {
  copied: boolean;
  copy: (text: string) => Promise<boolean>;
}

export function useCopyToClipboard({
  timeout = 2000,
  showToast = true,
}: UseCopyToClipboardOptions = {}): UseCopyToClipboardReturn {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      if (!text) return false;
      if (typeof navigator === "undefined" || !navigator.clipboard) {
        if (showToast) toast.error("Clipboard not available");
        return false;
      }
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        if (showToast) toast.success("Copied to clipboard");
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), timeout);
        return true;
      } catch {
        if (showToast) toast.error("Failed to copy");
        return false;
      }
    },
    [timeout, showToast]
  );

  return { copied, copy };
}
```

---

### Task 3 — `features/chat/components/code-block.tsx`

**Goal:** extract the inline renderer; lazy-load the highlighter (`ssr:false`) behind a `<pre>`
fallback; add a language label and copy button; semantic chrome.
**Files:** `features/chat/components/code-block.tsx` (new).

```tsx
"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";

// Lazy, client-only. The highlighter (~½MB w/ Prism + theme) is never in the
// first-load bundle; it loads only when a fenced code block actually mounts.
const SyntaxHighlighter = dynamic(
  async () => {
    const mod = await import("react-syntax-highlighter");
    return mod.Prism;
  },
  {
    ssr: false,
    loading: () => null, // the <pre> fallback below covers SSR + load gap
  }
);

// Theme is also code-split (separate dynamic import resolved lazily).
let cachedTheme: Record<string, React.CSSProperties> | null = null;
async function loadTheme() {
  if (cachedTheme) return cachedTheme;
  const mod = await import("react-syntax-highlighter/dist/esm/styles/prism");
  cachedTheme = mod.oneDark;
  return cachedTheme;
}

interface CodeBlockProps {
  language: string | undefined;
  value: string;
}

export function CodeBlock({ language, value }: CodeBlockProps) {
  const { copied, copy } = useCopyToClipboard({ showToast: false });
  const [theme, setTheme] = React.useState<Record<string, React.CSSProperties> | null>(null);

  React.useEffect(() => {
    let active = true;
    loadTheme().then((t) => active && setTheme(t));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="my-3 overflow-hidden rounded-md border border-border bg-muted">
      <div className="flex items-center justify-between border-b border-border bg-muted/60 px-3 py-1.5 select-none">
        <span className="font-mono text-xs text-muted-foreground">
          {language ?? "text"}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={copied ? "Copied" : "Copy code"}
          className="h-6 w-6 text-muted-foreground hover:text-foreground"
          onClick={() => void copy(value)}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {theme ? (
        <SyntaxHighlighter
          language={language}
          style={theme}
          PreTag="div"
          customStyle={{
            margin: 0,
            padding: "1rem",
            background: "transparent",
            fontSize: "0.8125rem",
          }}
          codeTagProps={{ className: "font-mono" }}
        >
          {value}
        </SyntaxHighlighter>
      ) : (
        // Instant fallback during SSR + highlighter/theme load.
        <pre className="overflow-x-auto p-4 text-[0.8125rem] leading-relaxed">
          <code className="font-mono text-foreground">{value}</code>
        </pre>
      )}
    </div>
  );
}
```

---

### Task 4 — `features/chat/components/chat-message.tsx` (refactor)

**Goal:** memoize the message and the markdown renderer map; use `CodeBlock`, `RouteBadge`,
`ThinkingSteps`, `SourcesPanel`, `MessageActions`; remove **all** hardcoded colors.
**Files:** `features/chat/components/chat-message.tsx` (refactored).

```tsx
"use client";

import * as React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, User } from "lucide-react";

import { Message } from "@/types";
import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { CodeBlock } from "./code-block";
import { RouteBadge } from "./route-badge";
import { ThinkingSteps } from "./thinking-steps";
import { SourcesPanel } from "./sources-panel";
import { MessageActions } from "./message-actions";

// Module-scope, stable across renders → ReactMarkdown does not rebuild its
// renderer tree on each streamed token (M9) or parent re-render.
const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className ?? "");
    const isInline = !match;
    if (isInline) {
      return (
        <code
          className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <CodeBlock
        language={match?.[1]}
        value={String(children).replace(/\n$/, "")}
      />
    );
  },
  a: ({ ...props }) => (
    <a
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline-offset-2 hover:underline"
      {...props}
    />
  ),
  ul: ({ ...props }) => <ul className="list-disc space-y-1 pl-4" {...props} />,
  ol: ({ ...props }) => <ol className="list-decimal space-y-1 pl-4" {...props} />,
};

interface ChatMessageProps {
  message: Message;
}

function ChatMessageImpl({ message }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "group flex w-full gap-4 rounded-xl p-5 transition-colors",
        isUser ? "flex-row-reverse bg-primary/5" : "border border-border bg-card shadow-sm"
      )}
    >
      <Avatar className="h-8 w-8 shrink-0 border border-border">
        <AvatarFallback
          className={cn(
            isUser ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
          )}
        >
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn("min-w-0 flex-1 space-y-2", isUser ? "text-right" : "text-left")}>
        <div className={cn("flex items-center gap-2", isUser ? "justify-end" : "justify-start")}>
          <span className="text-sm font-semibold text-foreground">
            {isUser ? "You" : "RAG Assistant"}
          </span>
          {!isUser && message.route && <RouteBadge route={message.route} />}
        </div>

        {/* Thinking steps (synthesized "done" today; live in M9) */}
        {!isUser && message.steps && message.steps.length > 0 && (
          <ThinkingSteps steps={message.steps} />
        )}

        <div
          className={cn(
            "prose prose-sm max-w-none break-words text-sm leading-relaxed dark:prose-invert",
            isUser ? "text-foreground/90" : "text-muted-foreground"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {/* Sources (Referenced N chunks) */}
        {!isUser && (
          <SourcesPanel
            sources={message.sources}
            count={message.sourcesCount}
          />
        )}

        {/* Actions: copy + retry */}
        {!isUser && message.status !== "streaming" && (
          <MessageActions content={message.content} messageId={message.id} />
        )}
      </div>
    </div>
  );
}

// Re-render only when this message's identity/content/status/steps change.
export const ChatMessage = React.memo(ChatMessageImpl, (prev, next) => {
  const a = prev.message;
  const b = next.message;
  return (
    a.id === b.id &&
    a.content === b.content &&
    a.status === b.status &&
    a.route === b.route &&
    a.sourcesCount === b.sourcesCount &&
    a.steps === b.steps && // M2 keeps a stable array ref unless steps change
    a.sources === b.sources
  );
});
```

---

### Task 5 — `features/chat/components/route-badge.tsx`

**Goal:** typed `RouteType → { label, variant, className }` map using semantic tones; no bare strings.
**Files:** `features/chat/components/route-badge.tsx` (new).

```tsx
"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RouteType } from "@/types";

type Variant = React.ComponentProps<typeof Badge>["variant"];

const ROUTE_MAP: Record<RouteType, { label: string; variant: Variant; className?: string }> = {
  RAG: { label: "RAG", variant: "secondary" },
  WEB: { label: "Web", variant: "secondary" },
  DIRECT: { label: "Direct", variant: "outline" },
  "WEB+RAG": { label: "Web + RAG", variant: "secondary" },
  "DIRECT+WEB": { label: "Direct + Web", variant: "outline" },
  "DIRECT+RAG": { label: "Direct + RAG", variant: "outline" },
  ERROR: { label: "Error", variant: "destructive" },
};

interface RouteBadgeProps {
  route: RouteType;
  className?: string;
}

export function RouteBadge({ route, className }: RouteBadgeProps) {
  const cfg = ROUTE_MAP[route] ?? ROUTE_MAP.DIRECT;
  return (
    <Badge
      variant={cfg.variant}
      className={cn("h-5 px-2 text-[10px] font-normal", cfg.className, className)}
      aria-label={`Route: ${cfg.label}`}
    >
      {cfg.label}
    </Badge>
  );
}
```

> All four variants (`default`/`secondary`/`outline`/`destructive`) resolve to semantic tokens
> inside `badge.tsx` — no hardcoded color leaks. `ERROR` maps to `destructive` so failed turns are
> visibly distinct in both themes.

---

### Task 6 — `features/chat/components/thinking-steps.tsx`

**Goal:** collapsible list of steps with a per-stage icon + label + status indicator. Renders the
synthesized single "done" step today; structurally ready for a live, growing list in M9.
**Files:** `features/chat/components/thinking-steps.tsx` (new).

```tsx
"use client";

import * as React from "react";
import {
  Brain,
  ChevronDown,
  Check,
  Loader2,
  Search,
  Database,
  Sparkles,
  CircleDot,
  AlertCircle,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { StepStage, StepStatus, ThinkingStep } from "@/types";

const STAGE_ICON: Record<StepStage, React.ComponentType<{ className?: string }>> = {
  routing: CircleDot,
  retrieving: Database,
  searching: Search,
  synthesizing: Sparkles,
  done: Check,
};

function StatusDot({ status }: { status: StepStatus }) {
  if (status === "active") return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
  if (status === "done") return <Check className="h-3.5 w-3.5 text-primary" />;
  if (status === "error") return <AlertCircle className="h-3.5 w-3.5 text-destructive" />;
  return <CircleDot className="h-3.5 w-3.5 text-muted-foreground/50" />;
}

interface ThinkingStepsProps {
  steps: ThinkingStep[];
}

export function ThinkingSteps({ steps }: ThinkingStepsProps) {
  const [open, setOpen] = React.useState(false);
  const hasActive = steps.some((s) => s.status === "active");

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border border-border bg-muted/40">
      <CollapsibleTrigger
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        aria-label="Toggle reasoning steps"
      >
        <Brain className="h-3.5 w-3.5" />
        <span>{hasActive ? "Thinking…" : "Reasoning"}</span>
        <span className="text-muted-foreground/60">({steps.length})</span>
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 transition-transform motion-reduce:transition-none",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>

      <CollapsibleContent className="px-3 pb-3">
        <ol className="space-y-1.5 border-l border-border pl-3">
          {steps.map((step) => {
            const Icon = STAGE_ICON[step.stage] ?? CircleDot;
            return (
              <li key={step.id} className="flex items-center gap-2 text-xs">
                <StatusDot status={step.status} />
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                <span
                  className={cn(
                    step.status === "active" ? "text-foreground" : "text-muted-foreground"
                  )}
                >
                  {step.label}
                </span>
              </li>
            );
          })}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
```

---

### Task 7 — `features/chat/components/sources-panel.tsx`

**Goal:** collapsible panel summarizing referenced context. Today it renders the count
("Referenced N chunks from your documents"); if M2/M9 supplies a `sources[]` array it lists them.
**Files:** `features/chat/components/sources-panel.tsx` (new).

```tsx
"use client";

import * as React from "react";
import { ChevronDown, FileText, Layers, ExternalLink } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { Source } from "@/types";

interface SourcesPanelProps {
  sources?: Source[];
  count?: number;
}

export function SourcesPanel({ sources, count }: SourcesPanelProps) {
  const total = count ?? sources?.length ?? 0;
  const [open, setOpen] = React.useState(false);

  if (total <= 0) return null;

  // No structured sources yet (today's backend): show the summary line only.
  if (!sources || sources.length === 0) {
    return (
      <div className="mt-3 flex items-center gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
        <Layers className="h-3.5 w-3.5" />
        <span>Referenced {total} chunk{total === 1 ? "" : "s"} from your documents</span>
      </div>
    );
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-3 border-t border-border pt-3">
      <CollapsibleTrigger
        className="flex w-full items-center gap-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        aria-label="Toggle sources"
      >
        <Layers className="h-3.5 w-3.5" />
        <span>Referenced {total} chunk{total === 1 ? "" : "s"}</span>
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 transition-transform motion-reduce:transition-none",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>

      <CollapsibleContent className="mt-2 space-y-1.5">
        {sources.map((s) => (
          <a
            key={s.id}
            href={s.url ?? "#"}
            target={s.url ? "_blank" : undefined}
            rel="noopener noreferrer"
            className="flex items-start gap-2 rounded-md bg-muted/40 p-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-foreground">
                {s.title ?? "Untitled source"}
              </span>
              {s.snippet && <span className="line-clamp-2 block">{s.snippet}</span>}
            </span>
            {s.url && <ExternalLink className="h-3 w-3 shrink-0" />}
          </a>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}
```

---

### Task 8 — `features/chat/components/message-actions.tsx`

**Goal:** tooltip-wrapped icon buttons for copy-answer and retry; retry calls `useChat().retry`.
**Files:** `features/chat/components/message-actions.tsx` (new).

```tsx
"use client";

import { Check, Copy, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { useChat } from "@/features/chat/hooks/use-chat";

interface MessageActionsProps {
  content: string;
  messageId: string;
}

export function MessageActions({ content, messageId }: MessageActionsProps) {
  const { copied, copy } = useCopyToClipboard();
  const { retry, isStreaming } = useChat();

  return (
    <div className="flex items-center gap-1 pt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 motion-reduce:transition-none">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={copied ? "Copied" : "Copy answer"}
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={() => void copy(content)}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "Copied" : "Copy answer"}</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Retry"
            disabled={isStreaming}
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={() => retry(messageId)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Retry</TooltipContent>
      </Tooltip>
    </div>
  );
}
```

> The `useChat` facade signature is `{ messages, isStreaming, sendMessage, stop, retry }` (plan
> §Target Architecture). If `retry` currently takes no argument, drop `messageId`; the component is
> otherwise unchanged. The action row uses the parent's `group` class for hover reveal (no motion lib).

---

### Task 9 — `features/chat/components/chat-input.tsx` (refactor)

**Goal:** swap to `react-textarea-autosize`; keep web-search switch, file upload, Enter-to-send /
Shift+Enter-newline; semantic tokens; accessible labels.
**Files:** `features/chat/components/chat-input.tsx` (refactored).

```tsx
"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { ArrowUp, Globe, Loader2, Paperclip } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { api } from "@/services/api"; // M1 may relocate to features/upload; keep import in sync

interface ChatInputProps {
  isLoading: boolean;
  onSend: (message: string, webSearch: boolean) => void;
  onFileUploaded?: (fileName: string) => void;
}

export function ChatInput({ isLoading, onSend, onFileUploaded }: ChatInputProps) {
  const [input, setInput] = useState("");
  const [webSearch, setWebSearch] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    onSend(input, webSearch);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    try {
      await api.uploadFile(file);
      toast.success(`${file.name} uploaded`);
      onFileUploaded?.(file.name);
    } catch {
      toast.error("Upload failed");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="border-t border-border bg-background p-4">
      <div className="mx-auto max-w-4xl space-y-2">
        <div className="relative flex items-end gap-1 rounded-2xl border border-border bg-background p-1 shadow-sm focus-within:ring-1 focus-within:ring-ring">
          <div className="flex items-center gap-1 pl-1">
            <input
              type="file"
              ref={fileInputRef}
              className="hidden"
              onChange={handleFileUpload}
              accept=".pdf,.docx,.txt"
            />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Upload document"
                  className="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading || isLoading}
                >
                  {isUploading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Paperclip className="h-4 w-4" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Upload document</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Toggle web search"
                  aria-pressed={webSearch}
                  className={cn(
                    "h-8 w-8 rounded-full transition-colors motion-reduce:transition-none",
                    webSearch
                      ? "bg-primary/10 text-primary hover:bg-primary/15"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() => setWebSearch((v) => !v)}
                >
                  <Globe className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {webSearch ? "Web search enabled" : "Web search disabled"}
              </TooltipContent>
            </Tooltip>
          </div>

          <TextareaAutosize
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything…"
            minRows={1}
            maxRows={8}
            disabled={isLoading}
            aria-label="Message"
            className="flex-1 resize-none border-0 bg-transparent px-3 py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-50"
          />

          <Button
            type="button"
            size="icon-sm"
            aria-label="Send message"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            className="mb-0.5 mr-1 h-8 w-8 shrink-0 rounded-full"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        </div>

        <p className="text-center text-[10px] text-muted-foreground">
          AI can make mistakes. Check important info.
        </p>
      </div>
    </div>
  );
}
```

---

### Task 10 — `features/chat/components/message-loading.tsx` (refactor)

**Goal:** replace the hand-rolled `animate-pulse` lines with shadcn `Skeleton`; semantic surface.
**Files:** `features/chat/components/message-loading.tsx` (refactored).

```tsx
import { Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";

export function MessageLoading() {
  return (
    <div
      className="flex w-full gap-4 rounded-xl border border-border bg-card p-5 shadow-sm"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <Avatar className="h-8 w-8 shrink-0 border border-border">
        <AvatarFallback className="bg-muted text-muted-foreground">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 space-y-2 pt-1">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <span className="sr-only">Assistant is generating a response…</span>
    </div>
  );
}
```

---

### Task 11 — `features/chat/components/empty-state.tsx` (refactor)

**Goal:** semantic-token refresh; no behavior change.
**Files:** `features/chat/components/empty-state.tsx` (refactored).

```tsx
import { FileText, Globe, Zap } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center space-y-6 p-8 text-center">
      <div className="rounded-full bg-muted p-4">
        <Zap className="h-8 w-8 fill-primary text-primary" />
      </div>
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight text-foreground">RAG Assistant</h2>
        <p className="mx-auto max-w-md text-muted-foreground">
          Upload documents to chat with them, or enable Web Search for live information.
        </p>
      </div>

      <div className="mt-8 grid w-full max-w-lg grid-cols-1 gap-4 md:grid-cols-2">
        <div className="cursor-default rounded-xl border border-border bg-card p-4 transition-colors hover:bg-muted motion-reduce:transition-none">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-primary">
            <FileText className="h-4 w-4" />
            Analyze Documents
          </div>
          <p className="text-xs text-muted-foreground">
            &quot;Summarize the quarterly report PDF I just uploaded.&quot;
          </p>
        </div>

        <div className="cursor-default rounded-xl border border-border bg-card p-4 transition-colors hover:bg-muted motion-reduce:transition-none">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-chart-2">
            <Globe className="h-4 w-4" />
            Web Search
          </div>
          <p className="text-xs text-muted-foreground">
            &quot;What are the latest features in Next.js 15?&quot;
          </p>
        </div>
      </div>
    </div>
  );
}
```

> `sidebar.tsx` and the residual `app/page.tsx` classes are migrated per the §4 tables in the same
> commit; their structure is unchanged, so no full listing is reproduced here.

---

## 7. Accessibility

- **Every icon-only button** (`code-block` copy, `message-actions` copy/retry, `chat-input`
  upload/web-search/send, sidebar toggle) carries an `aria-label`. Web-search uses
  `aria-pressed={webSearch}`.
- **Tooltips** (shadcn/Radix) provide visible hints on hover/focus; trigger uses `asChild` so the
  underlying `<button>` keeps native semantics. `TooltipProvider` is mounted once in `providers.tsx`.
- **Keyboard focus rings**: rely on shadcn `Button`'s built-in `focus-visible:ring-ring/50 ring-[3px]`
  (`button.tsx:8`). The input pill uses `focus-within:ring-1 focus-within:ring-ring`. No `outline:none`
  without a replacement ring.
- **Collapsibles** (`thinking-steps`, `sources-panel`): Radix `Collapsible` sets `aria-expanded` /
  `aria-controls` on the trigger automatically; chevron rotation is decorative.
- **Color contrast**: all foreground/background pairs come from the token set, which is tuned for
  WCAG AA in both `:root` and `.dark` (`globals.css:46-113`). `text-muted-foreground` on `bg-card`
  /`bg-muted` clears 4.5:1 in both themes — verify with axe (see §8).
- **Prose**: assistant body uses `prose dark:prose-invert` so generated headings/lists/quotes invert
  in dark mode (see Risk R4 re: `@tailwindcss/typography`).
- **Reduced motion**: every `transition-*` is paired with `motion-reduce:transition-none`; the only
  animation here is `animate-spin` on loaders (acceptable) and chevron rotation (transform, gated).
- **Loading status**: `MessageLoading` uses `role="status"` + `aria-live="polite"` + an `sr-only`
  message so screen readers announce the pending response.

---

## 8. Testing & Verification

**Stack:** Vitest + React Testing Library + `@testing-library/user-event` (added in M5; M3 lands the
component tests it covers). Mock `useChat` and `navigator.clipboard` per test.

RTL component tests:
- **`chat-message`**: renders "You" + plain text (no markdown parse) for `role:"user"`; renders
  "RAG Assistant" + a `<RouteBadge>` + markdown for assistant; asserts a fenced code block mounts a
  `CodeBlock` (queryByLabelText "Copy code"); asserts `React.memo` prevents re-render when an
  unrelated sibling updates (spy on render count).
- **`code-block`**: clicking copy calls `navigator.clipboard.writeText` with the snippet and swaps
  the icon to the check (assert `aria-label` flips to "Copied"); fallback `<pre>` renders before the
  dynamic highlighter resolves (assert `<pre>` present on first paint).
- **`route-badge`**: parametrized over every `RouteType` → correct label + `ERROR` → destructive
  variant (assert class / `data-variant`).
- **`thinking-steps`**: collapsed by default; clicking the trigger toggles `aria-expanded` and
  reveals the step list; renders the synthesized single "done" step (status icon = check).
- **`sources-panel`**: `count=3, sources=undefined` → "Referenced 3 chunks" summary, no collapsible;
  `count=0` → renders nothing; with `sources[]` → collapsible lists each title.
- **`message-actions`**: copy fires `clipboard.writeText(content)` + toast; retry calls the mocked
  `useChat().retry` with the message id; retry disabled while `isStreaming`.
- **`chat-input`**: typing + Enter calls `onSend(text, webSearch)` and clears; Shift+Enter does
  **not** send; toggling web-search flips `aria-pressed`; textarea grows (assert `rows`/height
  increases with multiline input).

**Manual dark/light pass:** toggle theme; verify no element keeps a light surface in dark mode
(scan for any residual `bg-white`/`slate`), code-block chrome, badges, panels, input pill, sidebar,
empty-state all flip correctly.

**a11y:** run `axe` (via `@axe-core/playwright` in M5 E2E, or the browser extension manually) on the
chat route in both themes — zero serious/critical violations; tab through the input toolbar and
message actions to confirm visible focus rings and reachable controls. Lighthouse a11y ≥ 95.

---

## 9. Risks & Gotchas

- **R1 — `next/dynamic` + `react-syntax-highlighter` SSR.** The highlighter touches browser-only
  APIs; it **must** be loaded with `ssr:false`. The `loading` fallback fires only client-side, so
  the in-component `<pre>` fallback (gated on `theme === null`) is what covers SSR and the first
  paint — keep it. Importing `Prism` (not the default `react-syntax-highlighter` barrel) keeps the
  chunk minimal; the `oneDark` theme is a second lazy import resolved in `useEffect`.
- **R2 — Memoization vs. streaming (M9).** `React.memo` compares `message.content` by reference.
  M2's `appendContent` store action must produce a **new string** per token append (it does —
  string concatenation yields a new value) while keeping **other** fields' references stable
  (`steps`, `sources` arrays only get new references when they actually change). If M2 instead
  mutates the message object in place, `React.memo` would skip the update — the M2 store must return
  new message objects (immutable update) for the comparator to fire. Document this contract on the
  store action.
- **R3 — Tailwind v4 token availability.** Every class in §4 maps to a `--color-*` declared in
  `@theme inline` (`globals.css:6-44`). `text-chart-2` (used for the Web Search accent) is valid via
  `--color-chart-2`. If a future token is referenced that isn't in `@theme inline`, the class
  silently no-ops — verify the class compiles (visible color) during the manual pass.
- **R4 — `prose` plugin.** `@tailwindcss/typography` is **not** in `package.json`. The `prose`
  classes currently degrade to no-ops for typography spacing (only the explicit `ul`/`ol`/`a`
  overrides in `markdownComponents` style lists/links). If richer markdown typography is desired,
  `npm i -D @tailwindcss/typography` and add `@plugin "@tailwindcss/typography";` to `globals.css`.
  Either way, `dark:prose-invert` is harmless without the plugin. Decide explicitly; do not assume
  it is present.
- **R5 — `command` is added but unmounted.** shadcn `command` generates `command.tsx` + installs
  `cmdk` per the plan, but no command palette ships in M3. Leaving an unused file is fine; do not
  wire a global key handler yet (that's a later UX addition).
- **R6 — File path drift vs. M1.** M3 assumes M1 moved chat components into `features/chat/components/`
  and that `useChat` + the store exist. If M1 is not yet merged, either rebase M3 onto it or
  temporarily author the components under `components/chat/` and stub `useChat`; the token migration
  and component code are identical regardless of folder.
- **R7 — `api` import location.** `chat-input` still imports `@/services/api`. M1 replaces that with
  `lib/api/http-client.ts` + a feature `upload.api.ts`; keep the import in sync with whatever M1
  landed to avoid a broken reference.

---

## 10. Exit Criteria (checkable)

- [ ] `npx shadcn add dropdown-menu tooltip collapsible skeleton command` run; five files exist
      under `components/ui/`; `TooltipProvider` mounted in `app/providers.tsx`.
- [ ] `react-textarea-autosize` installed; `chat-input` grows 1→8 rows and no longer uses the inline
      fixed `height` style.
- [ ] `hooks/use-copy-to-clipboard.ts` exists; copy works in `code-block` and `message-actions`
      (icon flips to check, toast where enabled).
- [ ] `code-block` lazy-loads the highlighter (`ssr:false`); the highlighter chunk is **absent** from
      the chat route's first-load JS (verify in `next build` output / bundle analyzer); `<pre>`
      fallback renders before it resolves.
- [ ] `chat-message` is `React.memo`-wrapped with a module-scope `components` map and composes
      `RouteBadge`/`ThinkingSteps`/`SourcesPanel`/`MessageActions`.
- [ ] `thinking-steps` renders the synthesized "done" step and toggles `aria-expanded`;
      `sources-panel` renders "Referenced N chunks".
- [ ] **Zero** hardcoded `slate-*`/`blue-*`/`white`/`green-*`/`red-*` color classes remain in
      `chat-message`, `chat-input`, `sidebar`, `empty-state`, `message-loading`, and the residual
      `app/page.tsx` lines (grep clean).
- [ ] `message-loading` uses shadcn `Skeleton`; `role="status"` present.
- [ ] All §8 RTL tests pass; manual dark/light pass clean; axe reports no serious/critical issues in
      both themes.
- [ ] `npm run lint`, `tsc --noEmit`, `vitest run`, `next build` all pass.

## 11. Commit Plan

Milestone-sized commits on `claude/frontend-improvements-planning-1aX4u`:

1. `chore(ui): add shadcn dropdown-menu, tooltip, collapsible, skeleton, command + TooltipProvider`
2. `feat(chat): add useCopyToClipboard hook`
3. `feat(chat): extract lazy CodeBlock with copy + language label`
4. `feat(chat): add RouteBadge, ThinkingSteps, SourcesPanel, MessageActions`
5. `refactor(chat): memoize ChatMessage, compose panels, drop inline highlighter`
6. `feat(chat): autosize ChatInput via react-textarea-autosize`
7. `feat(chat): skeleton MessageLoading + EmptyState semantic tokens`
8. `style(chat): migrate slate/blue/white → semantic tokens (message, input, sidebar, page)`
9. `test(chat): RTL coverage for message, code-block, badge, panels, actions, input`

Each commit message ends with:
```
https://claude.ai/code/session_01Vf1vzppqBGXAd1k9PPKMAB
```
