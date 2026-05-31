# M0 — Tooling & Guardrails

Establish the quality, safety, and configuration foundation for the entire frontend upgrade **without changing a single pixel of UX or any runtime behavior** the user can observe. This milestone adds Prettier + a11y-aware ESLint + Husky/lint-staged + GitHub Actions CI, introduces a fail-fast Zod-validated env layer (`lib/env.ts`) and feature flags (`lib/flags.ts`), and finally fixes the broken `app/layout.tsx` (wrong metadata, no `ThemeProvider`, no `Toaster`) by mounting a `Providers` tree and the theme toggle.

**Status:** Not started · **Depends on:** nothing (this is the root milestone) · **Unlocks:** M1–M9 (every later milestone assumes lint/format/typecheck gates, CI, `env`/`flags`, and a working `Providers`/`ThemeProvider`/`Toaster` tree exist).

---

## 1. Objective & Scope

**Objective:** make every later commit safe to merge — quality and guardrails built in, not bolted on — and wire the provider/theming/toast infrastructure the rest of the plan depends on, with zero observable UX change beyond a newly-functional theme toggle and the page title/favicon metadata.

**In scope**
- Prettier + `prettier-plugin-tailwindcss` + config + `format` / `format:check` scripts.
- ESLint flat-config extension: `eslint-plugin-jsx-a11y` (recommended), `eslint-config-prettier` (disable stylistic rules that fight Prettier), and an explicit ban on `any`.
- `typecheck` npm script (`tsc --noEmit`).
- Husky pre-commit hook running `lint-staged` (ESLint --fix + Prettier on staged files only).
- GitHub Actions CI: install → lint → format:check → typecheck → build, on push/PR to the working branch.
- `lib/env.ts` — Zod schema that parses `process.env` (server + `NEXT_PUBLIC_*`) and fails fast.
- `lib/flags.ts` — typed booleans derived from env; **all forward-compat flags default `false`**.
- `app/providers.tsx` — single client `Providers` tree wrapping `ThemeProvider` (with a documented seam for `QueryClientProvider` in M1).
- `components/theme/theme-provider.tsx` + `components/theme/theme-toggle.tsx` (light/dark/system dropdown).
- Fix `app/layout.tsx`: real metadata, `suppressHydrationWarning` on `<html>`, mount `<Providers>` and `<Toaster />`.
- `.env.example` documenting every env var.

**Out of scope (explicitly)**
- **No UX/behavior change.** No refactor of `app/page.tsx`, no state-management change (TanStack Query / Zustand land in M1), no API-layer change (`services/api.ts` is replaced in M1), no streaming/SSE, no auth/BYOK/upload work.
- No design-token migration of hardcoded `slate/blue` classes (that is M3).
- No new tests / Vitest / Playwright (M5).
- No Docker / `output: 'standalone'` (M5).
- No deletion of `components/chat/chat-interface.tsx` (dead-code removal is M1).
- The flags added here gate **future** surfaces; no flag-gated feature is implemented in M0 — they ship dark by default.

---

## 2. Decisions & Rationale

| Decision | Choice | Rationale |
|---|---|---|
| Formatter | **Prettier** (not Biome) | Plan mandates Prettier; first-class `prettier-plugin-tailwindcss` auto-sorts Tailwind classes (critical for the Tailwind-heavy `page.tsx`); ubiquitous editor/CI support. Biome would mean re-deciding the linter too. |
| Tailwind class sorting | **`prettier-plugin-tailwindcss`** | Deterministic class order kills "class soup" diffs and enforces the canonical Tailwind order. Tailwind v4 reads config from `globals.css`, so the plugin needs no `tailwind.config` path. |
| Linter base | **Keep `eslint-config-next` flat config** + layer plugins | Already installed and wired; we extend rather than replace to preserve Next's `core-web-vitals` + TS rules. |
| a11y | **`eslint-plugin-jsx-a11y` (recommended)** | Catches missing `alt`, label/control associations, role misuse at lint time — cheap insurance before the UX-heavy milestones (M3/M4). |
| Prettier ↔ ESLint conflict | **`eslint-config-prettier` last** | Turns off ESLint's stylistic rules so ESLint owns correctness and Prettier owns formatting; no rule fights. Must be the final entry so it wins. |
| Ban `any` | **`@typescript-eslint/no-explicit-any: error`** | `page.tsx:78` (`catch (err: any)`) and `services/api.ts:80` (`Promise<any>`) leak `any`; the plan calls strict TS the standard. Erroring forces typed errors in M1. |
| Env validation | **Zod** | Plan standardizes on Zod for env + API schemas; one library for runtime validation everywhere. Single typed `env` export means no scattered `process.env.X!` with `!`. |
| Fail-fast strategy | Parse at module load, `throw` on invalid | A missing/malformed required var should crash the build/boot loudly, not surface as a runtime `undefined` deep in the UI. |
| Feature flags | **Derived booleans in `lib/flags.ts`** from validated env | Single source of truth, typed, defaults `false` so unfinished backend phases ship dark. Centralizing avoids stringly-typed `process.env.NEXT_PUBLIC_FEATURE_* === "true"` checks scattered across components. |
| Git hooks | **Husky** (not simple-git-hooks) | Plan mandates Husky; mature, the `prepare` script auto-installs hooks on `npm install`, and it composes cleanly with `lint-staged`. |
| Staged-file linting | **`lint-staged`** | Runs ESLint/Prettier only on staged files — fast pre-commit, no full-repo scan per commit. |
| CI | **GitHub Actions** | Plan mandates GitHub Actions on the branch; native to the repo host, zero extra infra. |
| Package manager | **npm** | Repo ships `package-lock.json` only — stay on npm; CI uses `npm ci`. |

---

## 3. Current-State Snapshot (with `file:line` citations)

- **Broken metadata / no theming mount.** `app/layout.tsx:15-18` sets `title: "Create Next App"` / `description: "Generated by create next app"`. `app/layout.tsx:26` is `<html lang="en">` with **no `suppressHydrationWarning`**. `app/layout.tsx:30` renders `{children}` directly — **no `ThemeProvider`, no `Providers`, no `<Toaster />`** anywhere in the tree.
- **Toaster component exists but is never mounted.** `components/ui/sonner.tsx:13-40` exports a themed `Toaster` (already reads `useTheme()` from `next-themes`), yet nothing imports/renders it. `sonner`'s `toast()` is already called at `app/page.tsx:101` (`toast.success("Chat history cleared")`) — meaning **toasts currently no-op / never appear** because the `<Toaster />` host is absent.
- **`next-themes` installed, unused.** `package.json:22` lists `next-themes@^0.4.6`; the only consumer is `components/ui/sonner.tsx`. No `ThemeProvider` wraps the app and **no theme toggle UI exists** (`components/theme/` does not exist; `components/` contains only `chat/` and `ui/`).
- **No Prettier.** No `.prettierrc` / `.prettierignore` in repo root; `package.json:5-10` scripts are only `dev`/`build`/`start`/`lint` — **no `format`, `format:check`, or `typecheck`**.
- **Minimal ESLint flat config.** `eslint.config.mjs:5-16` spreads `eslint-config-next/core-web-vitals` + `/typescript` and sets `globalIgnores` — **no a11y plugin, no `eslint-config-prettier`, no `no-explicit-any` rule**.
- **No env validation.** No `lib/env.ts`; env is read ad hoc: `app/page.tsx:19-20` and `services/api.ts:5-6` each do `process.env.NEXT_PUBLIC_API_URL || <hardcoded fallback>` (two **different** fallbacks — `http://localhost:8000/api` vs the Render URL). No `.env.example`.
- **No feature flags.** No `lib/flags.ts`; none of `NEXT_PUBLIC_FEATURE_STREAMING|AUTH|BYOK|PRESIGNED_UPLOAD` are referenced anywhere.
- **No CI.** No `.github/` directory.
- **No git hooks.** No `.husky/`, no `lint-staged` config, no `prepare` script.
- **`any` leaks today.** `app/page.tsx:78` `catch (err: any)`; `services/api.ts:80` `uploadFile: async (file: File): Promise<any>`. (These will be flagged by the new lint rule; M0 fixes only what blocks `lint` from passing — see Task 2 note.)
- **`lib/` is nearly empty.** `lib/utils.ts:1-6` is the only file (the `cn()` helper). `tsconfig.json:21-23` maps `@/*` → `./*`, so `@/lib/env` and `@/lib/flags` resolve correctly.
- **TS already strict.** `tsconfig.json:7` `"strict": true`, `tsconfig.json:8` `"noEmit": true` — so a `typecheck` script is just `tsc --noEmit`.
- **shadcn config.** `components.json:3` `"style": "new-york"`, `iconLibrary: "lucide"`, aliases `@/components/ui`, `@/lib`, `@/hooks` — so `npx shadcn add dropdown-menu` will drop into `components/ui/dropdown-menu.tsx`. **`dropdown-menu` is not yet present** (`components/ui/` has avatar, badge, button, card, input, scroll-area, separator, sheet, sonner, switch, textarea only).

---

## 4. Target File Tree (delta)

Only files **added** or **changed** by M0:

```
typescript-agentic-rag-frontend/
├── .prettierrc                         (new)
├── .prettierignore                     (new)
├── .lintstagedrc.json                  (new)
├── .env.example                        (new)
├── package.json                        (changed: scripts + devDeps + "prepare")
├── eslint.config.mjs                   (changed: a11y + prettier + no-any)
├── .husky/
│   └── pre-commit                      (new)
├── .github/
│   └── workflows/
│       └── ci.yml                      (new)
├── lib/
│   ├── env.ts                          (new)
│   └── flags.ts                        (new)
├── app/
│   ├── layout.tsx                      (changed: metadata + Providers + Toaster + suppressHydrationWarning)
│   └── providers.tsx                   (new)
└── components/
    ├── theme/
    │   ├── theme-provider.tsx          (new)
    │   └── theme-toggle.tsx            (new)
    └── ui/
        └── dropdown-menu.tsx           (new — via `npx shadcn add dropdown-menu`)
```

---

## 5. Tasks (ordered)

> Run all commands from the repo root `/home/user/typescript-agentic-rag-frontend`. Package manager is **npm** (lockfile present).

### Task 1 — Install & configure Prettier + Tailwind plugin

**Goal:** deterministic formatting with auto-sorted Tailwind classes; add `format` / `format:check` scripts.

**Install**
```bash
npm i -D prettier prettier-plugin-tailwindcss
```

**`.prettierrc`** (new)
```json
{
  "semi": true,
  "singleQuote": false,
  "trailingComma": "es5",
  "printWidth": 80,
  "tabWidth": 2,
  "plugins": ["prettier-plugin-tailwindcss"],
  "tailwindFunctions": ["cn", "cva"]
}
```
> `tailwindFunctions` makes the plugin sort classes passed to `cn(...)` (`lib/utils.ts`) and `cva(...)` (used by shadcn variants). With Tailwind v4, config lives in `app/globals.css` (`@import "tailwindcss"`), which the plugin auto-detects — no `tailwindConfig` key needed.

**`.prettierignore`** (new)
```
.next
out
build
node_modules
next-env.d.ts
package-lock.json
*.lock
pnpm-lock.yaml
coverage
public
```

**`package.json` scripts** (changed) — add `format` and `format:check`:
```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "format": "prettier --write .",
    "format:check": "prettier --check ."
  }
}
```

**Initial pass:** run `npm run format` once so the existing tree is normalized and `format:check` is green from the start. (This is formatting-only — no behavioral change.)

---

### Task 2 — Extend ESLint flat config (a11y + prettier + ban `any`)

**Goal:** add a11y linting, stop ESLint fighting Prettier, and error on `any`.

**Install**
```bash
npm i -D eslint-plugin-jsx-a11y eslint-config-prettier
```

**`eslint.config.mjs`** (changed — full file)
```js
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";
import prettier from "eslint-config-prettier";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,

  // Accessibility (flat-config recommended preset).
  jsxA11y.flatConfigs.recommended,

  // Project rules.
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
    },
  },

  // MUST be last: disables ESLint stylistic rules that conflict with Prettier.
  prettier,

  // Override default ignores of eslint-config-next.
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
```

> **Note on existing `any` usages:** turning `no-explicit-any` to `error` will flag `app/page.tsx:78` and `services/api.ts:80`. Those files are **fully rewritten/replaced in M1** (page gutted to a thin shell; `services/api.ts` → `lib/api/http-client.ts`). To keep M0 self-contained and `lint` green *without* doing M1's refactor, apply the two minimal, behavior-preserving fixes below:
>
> - `app/page.tsx:78` — change `catch (err: any)` to `catch (err: unknown)` and read the message safely:
>   ```ts
>   } catch (err: unknown) {
>     console.error(err);
>     const errorText =
>       err instanceof Error
>         ? err.message
>         : "The AI service returned an error. Please try again later.";
>   ```
> - `services/api.ts:80` — change the return type `Promise<any>` to `Promise<unknown>` (the single caller in `app/page.tsx` ignores the body; `unknown` is sufficient and type-safe).
>
> These two edits are the only source-code touches in M0 and change no runtime behavior (same thrown message, same returned data). If your team prefers zero source edits in M0, instead scope the rule to non-legacy paths and leave the global ban for M1 — but the inline fixes above are recommended and trivial.

Verify: `npm run lint` passes.

---

### Task 3 — `lib/env.ts` (Zod env, fail-fast, typed)

**Goal:** one validated, typed `env` object; crash loudly on misconfiguration. Server vars + `NEXT_PUBLIC_*` in one schema.

**Install**
```bash
npm i zod
```

**`lib/env.ts`** (new)
```ts
import { z } from "zod";

/**
 * Single source of truth for runtime configuration.
 *
 * IMPORTANT (Next.js): only env vars that are *statically referenced* as
 * `process.env.NEXT_PUBLIC_*` are inlined into the client bundle at build time.
 * We therefore reference each public var explicitly below (no dynamic indexing),
 * so the values survive into the browser. Server-only vars are read the same way
 * and are simply never sent to the client.
 */

const FeatureFlag = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");

const envSchema = z.object({
  // --- Public (client-exposed) ---
  NEXT_PUBLIC_API_URL: z
    .string()
    .url()
    .default("http://localhost:8000/api"),

  // Forward-compat feature flags — default OFF so unfinished phases ship dark.
  NEXT_PUBLIC_FEATURE_STREAMING: FeatureFlag,
  NEXT_PUBLIC_FEATURE_AUTH: FeatureFlag,
  NEXT_PUBLIC_FEATURE_BYOK: FeatureFlag,
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: FeatureFlag,
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: FeatureFlag,

  // --- Build/runtime context ---
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
});

/**
 * Explicit, statically-analyzable mapping. Do NOT replace with `process.env`
 * spread — Next's inliner cannot follow dynamic access for NEXT_PUBLIC_* vars.
 */
const parsed = envSchema.safeParse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_FEATURE_STREAMING: process.env.NEXT_PUBLIC_FEATURE_STREAMING,
  NEXT_PUBLIC_FEATURE_AUTH: process.env.NEXT_PUBLIC_FEATURE_AUTH,
  NEXT_PUBLIC_FEATURE_BYOK: process.env.NEXT_PUBLIC_FEATURE_BYOK,
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD:
    process.env.NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD,
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS:
    process.env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS,
  NODE_ENV: process.env.NODE_ENV,
});

if (!parsed.success) {
  // Fail fast: a misconfigured environment must crash the build/boot loudly,
  // not surface as `undefined` deep in the UI.
  console.error(
    "❌ Invalid environment variables:",
    z.treeifyError(parsed.error)
  );
  throw new Error("Invalid environment variables. See logs above.");
}

export const env = parsed.data;
export type Env = typeof env;
```

> `z.treeifyError` is the Zod v4 API for a readable error tree. If your installed Zod is v3, substitute `parsed.error.flatten().fieldErrors`. Confirm with `npm ls zod` after install and adjust that one line.

---

### Task 4 — `lib/flags.ts` (typed feature flags)

**Goal:** derive booleans from validated `env`; centralize so components never read `process.env` directly. All forward-compat flags default `false` (already enforced in the schema).

**`lib/flags.ts`** (new)
```ts
import { env } from "@/lib/env";

/**
 * Feature flags gate forward-compatible surfaces so unfinished backend phases
 * ship dark. Each flag is consumed by exactly one later milestone:
 *
 *   streaming        -> M2 wires the strategy seam; M9 flips it true (backend P6 SSE)
 *   auth             -> M6 (backend P3 JWT auth + login/register)
 *   byok             -> M7 (backend P4 multi-provider BYOK + model picker)
 *   presignedUpload  -> M8 (backend P5 presigned S3 uploads + status polling)
 *   richComponents   -> M10 (backend P6 rich-output `component` SSE event — table/chart/citation/…)
 *
 * In M0 nothing reads these yet — they exist so the seams are ready.
 */
export const flags = {
  streaming: env.NEXT_PUBLIC_FEATURE_STREAMING,
  auth: env.NEXT_PUBLIC_FEATURE_AUTH,
  byok: env.NEXT_PUBLIC_FEATURE_BYOK,
  presignedUpload: env.NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD,
  richComponents: env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS,
} as const;

export type Flags = typeof flags;
```

---

### Task 5 — `app/providers.tsx` (client provider tree)

**Goal:** one `Providers` boundary wrapping the app, with the `ThemeProvider` mounted now and a documented seam for `QueryClientProvider` (M1).

**`app/providers.tsx`** (new)
```tsx
"use client";

import * as React from "react";
import { ThemeProvider } from "@/components/theme/theme-provider";

// M1 SEAM: import { QueryClientProvider } from "@tanstack/react-query"
//          and the singleton client from "@/lib/query-client".
//          Wrap <ThemeProvider>{...}</ThemeProvider> with it then.

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </ThemeProvider>
  );
}
```

---

### Task 6 — `components/theme/theme-provider.tsx`

**Goal:** thin wrapper over `next-themes` so the rest of the app imports a stable internal path.

**`components/theme/theme-provider.tsx`** (new)
```tsx
"use client";

import * as React from "react";
import {
  ThemeProvider as NextThemesProvider,
  type ThemeProviderProps,
} from "next-themes";

export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
```

> If your `next-themes` version does not re-export `ThemeProviderProps` from the package root, import it from `next-themes/dist/types`. Verify with `npm ls next-themes` (repo pins `^0.4.6`, which exports it from the root).

---

### Task 7 — Add the shadcn `dropdown-menu` primitive

**Goal:** the theme toggle uses a dropdown; `components/ui/dropdown-menu.tsx` is not yet present.

**Command**
```bash
npx shadcn@latest add dropdown-menu
```
This writes `components/ui/dropdown-menu.tsx` (new-york style, per `components.json`) and adds the `@radix-ui/react-dropdown-menu` dependency to `package.json`. Run `npm run format` afterward so the generated file matches our Prettier config. Commit the generated file.

> If the registry call is unavailable in CI/offline, the file can be vendored manually from the shadcn registry; the toggle below only relies on the standard `DropdownMenu`, `DropdownMenuTrigger`, `DropdownMenuContent`, `DropdownMenuItem` exports.

---

### Task 8 — `components/theme/theme-toggle.tsx`

**Goal:** light/dark/system dropdown using lucide icons, accessible, no hydration flash.

**`components/theme/theme-toggle.tsx`** (new)
```tsx
"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function ThemeToggle() {
  const { setTheme } = useTheme();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Toggle theme">
          <Sun className="h-5 w-5 scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
          <Moon className="absolute h-5 w-5 scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
          <span className="sr-only">Toggle theme</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

> We use icon swap via Tailwind `dark:` variants (no `theme` read) so the trigger renders identically on server and client — avoiding a hydration mismatch on the icon. The `aria-label` + `sr-only` text satisfy `jsx-a11y` for the icon-only button.
>
> **Where to mount it (M0):** mounting the toggle anywhere visible is enough to satisfy the verification step. The minimal, no-layout-churn placement is the existing sidebar header. The plan's full app-shell relayout is M3, so in M0 you may either (a) drop `<ThemeToggle />` into `components/chat/sidebar.tsx` next to the existing controls, or (b) render it in `app/layout.tsx` is **not** appropriate (it would float over content). Recommended: add it to the sidebar header in `sidebar.tsx`. This is a purely additive control and counts as the "theme toggle works" check; it is not a UX redesign.

---

### Task 9 — Fix `app/layout.tsx`

**Goal:** real metadata, `suppressHydrationWarning`, mount `<Providers>` + `<Toaster />`.

**`app/layout.tsx`** (changed — full file)
```tsx
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { Providers } from "@/app/providers";
import { Toaster } from "@/components/ui/sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Agentic RAG",
    template: "%s · Agentic RAG",
  },
  description:
    "An agentic Retrieval-Augmented Generation chat assistant with document upload and web search.",
  applicationName: "Agentic RAG",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
        <Toaster />
      </body>
    </html>
  );
}
```

> `suppressHydrationWarning` on `<html>` is **required** by `next-themes`: the theme class is applied to `<html>` by a pre-hydration inline script, so the server-rendered markup intentionally differs from the client's first paint. This one-level suppression is the documented, scoped fix (it does not silence warnings on children). `<Toaster />` is placed at the end of `<body>`, outside `<Providers>` is fine because the Sonner component reads `useTheme()` itself — but it must still be inside the same React tree; here both are descendants of the same root, and `next-themes` exposes context app-wide via the `<html>` class, so the toaster theming works.

---

### Task 10 — Husky + lint-staged pre-commit

**Goal:** auto-run ESLint `--fix` + Prettier on **staged** files before each commit.

**Install**
```bash
npm i -D husky lint-staged
npx husky init
```
`npx husky init` creates `.husky/` and adds a `"prepare": "husky"` script to `package.json` (so hooks reinstall on every `npm install`). It also creates a sample `.husky/pre-commit`; replace its contents.

**`.husky/pre-commit`** (new — full file)
```sh
npx lint-staged
```
> Modern Husky (v9+) does not need the legacy `#!/bin/sh` + `husky.sh` sourcing block — a plain command file is correct.

**`.lintstagedrc.json`** (new)
```json
{
  "*.{ts,tsx}": ["eslint --fix", "prettier --write"],
  "*.{js,mjs,cjs,json,css,md}": ["prettier --write"]
}
```

**Resulting `package.json` scripts block** (after Tasks 1, 11, 10 — for reference):
```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "typecheck": "tsc --noEmit",
    "prepare": "husky"
  }
}
```

---

### Task 11 — `typecheck` script

**Goal:** dedicated typecheck gate for CI and local use.

Add to `package.json` scripts (shown above):
```json
"typecheck": "tsc --noEmit"
```
> `tsconfig.json` already has `"strict": true` and `"noEmit": true`, so this is a pure type gate. Verify: `npm run typecheck` exits 0.

---

### Task 12 — GitHub Actions CI

**Goal:** on push/PR to the working branch, run install → lint → format:check → typecheck → build.

**`.github/workflows/ci.yml`** (new)
```yaml
name: CI

on:
  push:
    branches:
      - claude/frontend-improvements-planning-1aX4u
      - main
  pull_request:
    branches:
      - claude/frontend-improvements-planning-1aX4u
      - main

jobs:
  quality:
    name: Lint · Format · Typecheck · Build
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm

      - name: Install dependencies
        # HUSKY=0 disables git-hook install in CI (no .git hooks needed there).
        run: npm ci
        env:
          HUSKY: 0

      - name: Lint
        run: npm run lint

      - name: Format check
        run: npm run format:check

      - name: Typecheck
        run: npm run typecheck

      - name: Build
        run: npm run build
        env:
          # Provide deterministic public env so the build's Zod env.ts passes.
          NEXT_PUBLIC_API_URL: http://localhost:8000/api
          NEXT_PUBLIC_FEATURE_STREAMING: "false"
          NEXT_PUBLIC_FEATURE_AUTH: "false"
          NEXT_PUBLIC_FEATURE_BYOK: "false"
          NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: "false"
```

> The flag/API env in the `build` step is defensive: every var has a schema default, so the build would pass even without them, but pinning them keeps CI behavior explicit and matches `.env.example`. `HUSKY: 0` prevents the `prepare` script from trying to install git hooks in the non-interactive CI checkout.

---

### Task 13 — `.env.example`

**Goal:** document every env var so contributors copy → `.env.local`.

**`.env.example`** (new)
```bash
# Backend API base URL (must include the /api suffix).
NEXT_PUBLIC_API_URL=http://localhost:8000/api

# Feature flags — keep "false" until the matching backend phase ships.
# streaming  -> backend P6 SSE        (consumed M2/M9)
# auth       -> backend P3 JWT        (consumed M6)
# byok       -> backend P4 BYOK       (consumed M7)
# presigned  -> backend P5 S3         (consumed M8)
# rich       -> backend P6 component  (consumed M10 — rich-output rendering)
NEXT_PUBLIC_FEATURE_STREAMING=false
NEXT_PUBLIC_FEATURE_AUTH=false
NEXT_PUBLIC_FEATURE_BYOK=false
NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD=false
NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false
```
> `.env.example` is committed; real `.env.local` is git-ignored by Next's default `.gitignore`. Note `services/api.ts:6` currently defaults to the Render URL and `app/page.tsx:20` to localhost — both keep working because `env.ts` is not yet consumed by them in M0 (that rewire is M1); `.env.example` documents the intended single value going forward.

---

## 6. Feature Flags & Env

All env is validated once in `lib/env.ts` and surfaced via `env` (raw, typed) and `flags` (booleans).

| Env var | Type (post-parse) | Default | Validation | Consumed by |
|---|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | `string` (URL) | `http://localhost:8000/api` | `z.string().url()` | M1 `http-client` (today read ad hoc in `page.tsx`/`services/api.ts`) |
| `NEXT_PUBLIC_FEATURE_STREAMING` | `boolean` | `false` | `"true"|"false"` → bool | M2 (seam), M9 (flip true) |
| `NEXT_PUBLIC_FEATURE_AUTH` | `boolean` | `false` | `"true"|"false"` → bool | M6 |
| `NEXT_PUBLIC_FEATURE_BYOK` | `boolean` | `false` | `"true"|"false"` → bool | M7 |
| `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD` | `boolean` | `false` | `"true"|"false"` → bool | M8 |
| `NEXT_PUBLIC_FEATURE_RICH_COMPONENTS` | `boolean` | `false` | `"true"|"false"` → bool | M10 (backend P6 `component` event) |
| `NODE_ENV` | `"development"|"test"|"production"` | `development` | enum | tooling/build |

- **`.env.example`** (Task 13) is the canonical list; CI's `build` step pins the same values.
- **Why string-enum → boolean:** env vars are always strings; an explicit `"true"|"false"` enum rejects typos like `True`/`1`/`yes` at boot instead of silently coercing them.
- **Defaults `false`:** any flag-gated surface stays dark until its milestone flips it, satisfying the plan's "unfinished backend phases ship dark" principle.

---

## 7. Testing & Verification

**Automated gates (must all pass locally and in CI):**
```bash
npm run lint          # ESLint: next + a11y + no-explicit-any, no errors
npm run format:check  # Prettier: every file already formatted
npm run typecheck     # tsc --noEmit: no type errors
npm run build         # next build succeeds with env.ts validation
```

**Pre-commit gate:** `git add` a `.tsx` file with a deliberate format error → `git commit` → `lint-staged` reformats it via Prettier and re-stages; commit succeeds with the file fixed. Introduce an `any` → commit is **blocked** by `eslint --fix` surfacing the error.

**CI:** push the branch → GitHub Actions `CI / quality` job runs and goes **green** (all five steps).

**Manual checks (`npm run dev`):**
- Page `<title>` reads "Agentic RAG" (not "Create Next App"); favicon/metadata correct.
- **Theme toggle:** open the toggle → Light / Dark / System switch the theme immediately; **reload the page and the chosen theme persists** (next-themes writes `localStorage.theme`) with no flash of the wrong theme.
- **Toast:** trigger "Clear session" (existing `page.tsx:101` call) → a Sonner toast now **appears** (it previously no-op'd because `<Toaster />` was unmounted), and it is themed to match light/dark.
- **No hydration warning:** browser console shows **no** "Hydration failed" / "did not match" warning attributable to the theme class on `<html>`.
- **No UX regression:** send a message, upload a file, toggle web search, reset session — all behave exactly as before M0.

---

## 8. Risks & Gotchas

- **next-themes hydration mismatch.** The theme class is set on `<html>` by an inline script before React hydrates, so SSR markup differs from first client paint. Without `suppressHydrationWarning` on `<html>` (Task 9) React logs a hydration error every load. The attribute is scoped to that one element and does **not** mask real mismatches elsewhere. Do not read `theme`/`resolvedTheme` to choose what to render at the top level (it's `undefined` on the server) — the toggle uses CSS `dark:` variants precisely to avoid that.
- **Tailwind v4 + prettier-plugin-tailwindcss ordering.** `prettier-plugin-tailwindcss` **must be the last plugin** in `.prettierrc` `plugins` to sort correctly; with other Prettier plugins it has to load last. Tailwind v4 has no `tailwind.config.*` (config is in `globals.css`), so do **not** set a `tailwindConfig` path — the plugin auto-detects v4. Our `tailwindFunctions: ["cn","cva"]` ensures classes inside `cn()`/`cva()` are also sorted.
- **`eslint-config-prettier` placement.** It must be the **last** flat-config entry (Task 2) so it can turn off ESLint's stylistic rules; if placed earlier, later configs re-enable conflicting rules and `lint` vs `format` disagree, breaking CI.
- **`NEXT_PUBLIC_*` inlining at build, not runtime.** Next inlines `process.env.NEXT_PUBLIC_*` only where statically referenced. `lib/env.ts` therefore references each var explicitly (no `process.env` spread / dynamic key) — otherwise the values would be `undefined` in the browser bundle. Consequence: **changing a public flag requires a rebuild**, not just a restart. Server-only vars (none added in M0) would be read the same way but never reach the client.
- **Zod version skew.** Error formatting differs between Zod v3 (`error.flatten()`) and v4 (`z.treeifyError`). Confirm with `npm ls zod` and adjust the one line in `env.ts` (noted inline).
- **Husky in CI / non-interactive installs.** The `prepare: husky` script runs on every `npm ci`; in CI there are no git hooks to wire and it can error in shallow/odd checkouts. Set `HUSKY=0` in the CI install step (done in `ci.yml`). Locally, `npx husky init` must run once after install so `.husky/` exists.
- **Banning `any` flags existing legacy code.** `page.tsx:78` and `services/api.ts:80` use `any`; Task 2's inline `unknown` fixes keep `lint` green without pulling M1's refactor forward. Skipping those fixes makes CI red.
- **shadcn `dropdown-menu` generation requires network.** `npx shadcn add dropdown-menu` hits the registry; if offline, vendor the file manually (Task 7 note). Re-run `npm run format` on the generated file so `format:check` stays green.
- **Sonner already imports `next-themes`.** `components/ui/sonner.tsx` calls `useTheme()`; it only works once a `ThemeProvider` is in the tree (Task 5/6) — mounting `<Toaster />` without the provider would default to `"system"` but is correct after Task 9.

---

## 9. Exit Criteria (checkable)

- [ ] `npm run lint` passes (next + `jsx-a11y` recommended + `no-explicit-any: error`, zero errors).
- [ ] `npm run format:check` passes on the whole repo (after an initial `npm run format`).
- [ ] `npm run typecheck` (`tsc --noEmit`) passes with zero errors.
- [ ] `npm run build` succeeds.
- [ ] `.prettierrc`, `.prettierignore`, `.lintstagedrc.json`, `.env.example` exist with the content above.
- [ ] `eslint.config.mjs` includes `jsx-a11y` recommended, `eslint-config-prettier` (last), and `no-explicit-any: error`.
- [ ] `package.json` has scripts `format`, `format:check`, `typecheck`, and `prepare: husky`; devDeps include prettier (+tailwind plugin), `eslint-plugin-jsx-a11y`, `eslint-config-prettier`, husky, lint-staged; deps include `zod`.
- [ ] `lib/env.ts` exists, parses `process.env` via Zod, throws on invalid input, exports typed `env`.
- [ ] `lib/flags.ts` exists, derives `streaming`/`auth`/`byok`/`presignedUpload` from `env`, all default `false`.
- [ ] `app/providers.tsx` exists (client) wrapping `ThemeProvider` with a documented QueryClient seam.
- [ ] `components/theme/theme-provider.tsx` and `components/theme/theme-toggle.tsx` exist; `components/ui/dropdown-menu.tsx` added.
- [ ] `app/layout.tsx`: metadata is "Agentic RAG" (not "Create Next App"), `<html lang="en" suppressHydrationWarning>`, `<Providers>` wraps children, `<Toaster />` mounted.
- [ ] `.husky/pre-commit` runs `npx lint-staged`; a staged-file commit triggers ESLint+Prettier.
- [ ] `.github/workflows/ci.yml` runs install → lint → format:check → typecheck → build on push/PR to the branch, and the job is **green**.
- [ ] Manual: theme toggle switches + **persists across reload** with no flash; a toast fires on clear-session; **no hydration warning** in console; no behavioral regression in send/upload/web-search/reset.

---

## 10. Commit Plan

Milestone-sized, reviewable commits on branch `claude/frontend-improvements-planning-1aX4u`:

1. `chore(tooling): add Prettier + tailwind plugin, format scripts, format repo`
   — `.prettierrc`, `.prettierignore`, `package.json` (format/format:check), initial `prettier --write .`.

2. `chore(lint): add jsx-a11y + eslint-config-prettier, ban explicit any`
   — `eslint.config.mjs`; minimal `unknown` fixes in `app/page.tsx` and `services/api.ts`.

3. `feat(config): add Zod-validated env (lib/env.ts) + feature flags (lib/flags.ts) + .env.example`

4. `feat(theme): mount Providers/ThemeProvider/Toaster, fix layout metadata, add theme toggle`
   — `app/providers.tsx`, `components/theme/*`, `components/ui/dropdown-menu.tsx`, `app/layout.tsx` (+ toggle in `sidebar.tsx`), `suppressHydrationWarning`.

5. `chore(ci): add Husky + lint-staged pre-commit, typecheck script, GitHub Actions CI`
   — `.husky/pre-commit`, `.lintstagedrc.json`, `package.json` (typecheck, prepare), `.github/workflows/ci.yml`.

> Alternatively squash 1–5 into a single `feat: M0 tooling & guardrails` commit if the team prefers one commit per milestone. Each commit above is independently green against the gates in §7.
