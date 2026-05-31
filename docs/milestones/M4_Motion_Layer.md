# M4 — Motion Layer

This milestone introduces a centralized, tasteful motion system built on `framer-motion` over the
M3 component set: message enter/exit choreography (`AnimatePresence` + `layout`), a blinking
streaming caret, thinking-steps expand/collapse with staggered children, a spring-driven sidebar,
badge mount transitions, and a skeleton→content crossfade. Every animation is GPU-friendly
(transform/opacity only), gated by `prefers-reduced-motion`, and structured so streaming token
appends never thrash layout animations. No new product features ship here — this is pure
choreography over surfaces M3 already built.

**Status:** Planned · **Depends on:** M3 (chat UX components: `chat-message`, `message-list`,
`thinking-steps`, `sources-panel`, `route-badge`, `chat-input`, `message-loading`,
`components/layout/app-sidebar`) and M0's `app/providers.tsx` provider root · **Unlocks:** M9
(real SSE streaming motion — live token caret and `status`-driven thinking-step stagger reuse the
exact variants and reduced-motion contract defined here) and **M10** (rich `component` blocks —
table/chart/citation/code/callout/media — reuse these motion tokens, e.g. the message-enter /
crossfade variants, for their mount transition, honoring the same reduced-motion contract, so M10
needs no new motion infrastructure).

---

## 1. Objective & Scope

**Objective.** Layer a coherent, minimal motion system across the existing chat surface so the UI
feels alive without feeling animated-for-its-own-sake — and do it once, centrally, so M9's live
streaming inherits the choreography for free.

**IN scope**

- Add `framer-motion` and wire a global `<MotionConfig reducedMotion="user">` (+ `LazyMotion` with
  `domAnimation`) at the provider root.
- A SSR-safe `hooks/use-reduced-motion.ts` for *conditional logic* (deciding whether to mount a
  motion wrapper at all), complementing the global `MotionConfig` gate.
- A `lib/motion.ts` tokens module: shared `variants`, `transition`, and `spring` constants, plus a
  helper that collapses variants to no-ops under reduced motion.
- Motion on existing components only: message enter/exit + `layout` (`message-list` /
  `chat-message`), streaming caret (in `chat-message`), thinking-steps expand/collapse + child
  stagger, route-badge mount, sidebar open/close spring (replacing the CSS width transition),
  skeleton→content crossfade.
- Performance work: memoize messages so token appends don't re-trigger enter animations; assert only
  compositor properties animate; verify 60fps during a simulated stream.

**OUT of scope**

- New features, new components, new routes, or behavioral changes to chat/upload/session logic.
- Real streaming data or SSE wiring (that is M2 dark / M9 live). M4 exercises the caret against a
  *simulated* `status: "streaming"` only.
- Restyling / token migration (that was M3). M4 must not touch colors, spacing, or semantics beyond
  wrapping elements in `motion.*` and adding `variants`/`initial`/`animate`/`exit` props.
- Page-transition / route-level animation, scroll-linked animation, or gesture/drag interactions.

---

## 2. Motion Principles

1. **Minimal and tasteful.** Motion communicates state change (a message arrived, a panel opened, a
   token is streaming), never decoration. Durations are short (120–260ms for tweens), distances
   small (≤ 8px translate), easing calm. If a reviewer notices the animation *as* an animation, it's
   too much.
2. **Transform + opacity only.** Every animated property must be GPU-composited: `opacity`,
   `transform` (`x`/`y`/`scale`). Never animate `width`, `height` (raw), `top`, `left`, `margin`, or
   `box-shadow` in a hot path — they trigger layout/paint and blow the 60fps budget. The two places
   we *appear* to animate size (thinking-steps collapse, sidebar) use framer-motion's `layout`
   projection (which animates via transform under the hood) or an explicit measured height, with the
   tradeoffs documented in §7.
3. **Reduced motion is first-class, not an afterthought.** The default contract: when the user
   prefers reduced motion, **no transform animations fire**. Opacity-only crossfades are permitted
   where they aid comprehension, but the safe default for M4 is "render static." This is enforced two
   ways — globally via `<MotionConfig reducedMotion="user">` (framer-motion strips transform tracks,
   keeps opacity) and locally via the `use-reduced-motion` hook for "don't even mount the wrapper"
   decisions (e.g. the caret renders as a static block).
4. **Spring vs tween.** Use **springs** for physical, interruptible, user-driven motion where the end
   state can change mid-flight: the sidebar open/close, `layout` reflow of the message list. Use
   **tweens** (fixed duration + ease) for discrete, fire-and-forget transitions: message enter/exit,
   badge fade, skeleton crossfade, step stagger. Springs that never settle (low stiffness, high mass)
   feel sluggish — our sidebar spring is tuned firm (stiffness ~320, damping ~30).
5. **Memoization protects 60fps.** The in-flight assistant message re-renders on every token append.
   If its `ChatMessage` is not memoized, React reconciles it (and re-evaluates its `variants`) on
   each token, and any ancestor `layout` animation re-measures — instant jank. Messages are wrapped
   in `React.memo` with a content/status-aware comparator; the *body text* updates, but the motion
   wrapper's `initial`/`animate` identity stays stable so the enter animation fires exactly once.
6. **No layout thrash.** The streaming message must be **excluded from sibling `layout` animation**
   while it streams (its height changes every token; animating that reflow is both pointless and
   expensive). Only enter/exit and reorder of *settled* messages get `layout`.

---

## 3. Decisions & Rationale

**D1 — `framer-motion` over CSS transitions or GSAP.** The plan already selects `framer-motion`.
Three capabilities make it non-negotiable here and impractical in raw CSS:

- **Exit animations** (`AnimatePresence`) — CSS cannot animate an element that React has unmounted.
  Message removal, sidebar close, and skeleton→content all need exit choreography.
- **`layout` animations** — automatic FLIP-based reflow when list items insert/reorder, animated via
  transform (cheap), with zero manual measurement. Hand-rolling FLIP in CSS is error-prone.
- **Springs + interruptibility** — physical, mid-flight-reversible motion (sidebar) that CSS
  `transition` can only fake. GSAP would deliver this too, but it's a heavier, imperative,
  non-React-idiomatic dependency; `framer-motion` is declarative, React-19/Next-16 friendly, and the
  plan's stated choice.

**D2 — Centralize tokens in `lib/motion.ts`.** Inline `variants` scattered across components drift
and make the reduced-motion contract impossible to audit. A single tokens module exports the
canonical `transition`/`spring` constants and `*Variants` objects, plus a `reduceVariants()` helper.
Every component imports from here. This mirrors the backend's "single Settings source" principle —
one place to tune, one place to verify.

**D3 — Dual gating: `<MotionConfig reducedMotion="user">` *and* `use-reduced-motion`.** These are
complementary, not redundant:

- `<MotionConfig reducedMotion="user">` at the root makes framer-motion **automatically drop
  transform animations** for *every* `motion`/`m` component when the OS reports reduced motion,
  keeping opacity. This is the safety net — even a component that forgets to gate is covered.
- The `use-reduced-motion` hook drives **conditional rendering logic** that `MotionConfig` can't
  reach: "render a static `<span>` instead of a blinking caret," "set `initial={false}` so the first
  paint isn't animated," "skip `layout` entirely." Use it where we need to *not mount* motion, not
  merely strip its transforms.

**D4 — `LazyMotion` + `domAnimation`, using the `m` component.** `framer-motion`'s full `motion`
import pulls the entire feature set into the bundle. Wrapping the tree in
`<LazyMotion features={domAnimation} strict>` and importing the lightweight `m` component (e.g.
`<m.div>` instead of `<motion.div>`) loads only DOM animation features (~animations, variants,
`AnimatePresence`, `layout`) and trims the initial JS payload meaningfully. `strict` makes the build
*fail* if anyone imports the heavy `motion` component, enforcing the convention. `domAnimation`
(not `domMax`) is sufficient — we use no drag/layout-group gestures.

**D5 — Spring for sidebar, tween for everything discrete.** See §2.4. The sidebar is the one
user-driven, reversible surface; the rest are discrete state transitions where a tuned tween reads
as more controlled than a bouncy spring.

---

## 4. Current-State Snapshot

What exists today (pre-M4), and what M4 changes:

- **Sidebar transition — CSS width/opacity tween.** `app/page.tsx:107-117` wraps `<Sidebar>` in a
  `<div>` whose width and opacity animate via Tailwind utilities:

  ```tsx
  // app/page.tsx:107-117 (current)
  <div
    className={cn(
      "transition-all duration-300 ease-in-out overflow-hidden",
      isSidebarOpen ? "w-64 opacity-100" : "w-0 opacity-0"
    )}
  >
    <Sidebar ... />
  </div>
  ```

  This animates `width` (a layout property — not compositor-friendly) and uses `transition-all`
  (animates *every* changed property, a common jank source). M4 replaces this with a spring-driven
  `m.aside` / `AnimatePresence`. (Note: by M3, `page.tsx` is a thin shell and the sidebar lives in
  `components/layout/app-sidebar`; M4 targets that component, but the *behavior* to replace is the one
  shown here.)

- **`chat-message` uses `transition-all`, no enter/exit, no caret.**
  `components/chat/chat-message.tsx:23-28` applies `transition-all` to the message container:

  ```tsx
  // components/chat/chat-message.tsx:23-28 (current)
  <div className={cn("flex w-full gap-4 p-5 rounded-xl transition-all", ...)}>
  ```

  There is **no mount/enter animation** — new messages pop in instantly. There is **no exit
  animation** — `app/page.tsx:143-145` maps messages with no `AnimatePresence`, so removal is
  instant. There is **no streaming caret** — the assistant body is rendered by `ReactMarkdown` with
  no in-flight indicator (`chat-message.tsx:65-108`). The `route` badge (`chat-message.tsx:50-54`)
  mounts with no transition.

- **Loading state is a CSS pulse, no crossfade.** `components/chat/message-loading.tsx` uses
  `animate-pulse` and is swapped for real content with no crossfade (`app/page.tsx:146`).

- **No motion infrastructure.** `framer-motion` is **not** installed (see `package.json`
  dependencies). `app/providers.tsx` **does not exist yet** — it is created in M0 and M4 extends it.
  `hooks/use-reduced-motion.ts` is *referenced* by the plan but not yet implemented. `globals.css`
  contains **no** `prefers-reduced-motion` media query. M4 builds all of this.

---

## 5. Target File Tree (delta)

```
hooks/
  use-reduced-motion.ts        NEW  SSR-safe matchMedia subscription hook
lib/
  motion.ts                    NEW  shared variants / transition / spring tokens + reduceVariants()
app/
  providers.tsx                EDIT wrap tree in <LazyMotion features={domAnimation}><MotionConfig reducedMotion="user">
features/chat/components/
  message-list.tsx             EDIT <AnimatePresence> keyed by message id; layout on settled messages
  chat-message.tsx             EDIT m.div enter/exit + layout; <StreamingCaret> in body; React.memo
  streaming-caret.tsx          NEW  blinking m.span (or static block under reduced motion)
  thinking-steps.tsx           EDIT m.div expand/collapse (height/opacity) + staggerChildren on steps
  route-badge.tsx              EDIT m.span mount fade/scale
  message-loading.tsx          EDIT exit fade so AnimatePresence can crossfade skeleton→content
components/layout/
  app-sidebar.tsx              EDIT m.aside open/close spring, replacing CSS width transition
package.json                   EDIT add "framer-motion"
```

> Path note: the plan's target tree (`FRONTEND_IMPROVEMENT_PLAN.md` lines 54-61) places chat
> components under `features/chat/components/*`. If M3 has not yet completed the move and components
> still live under `components/chat/*`, apply each edit to whichever path exists — the diffs are
> identical. The streaming caret is a small new sibling component for testability; it could also be
> inlined into `chat-message.tsx`.

---

## 6. Tasks (ordered)

Execute in order — later tasks import the tokens and hook from earlier ones.

### Task 1 — Install `framer-motion`

**Goal.** Add the motion library to dependencies.

**Files.** `package.json` (+ lockfile).

```bash
npm install framer-motion
```

Pin to the latest stable major compatible with React 19 / Next 16 (12.x at time of writing). Verify
the install:

```bash
npm ls framer-motion
npm run typecheck   # tsc --noEmit must still pass
```

`framer-motion` ships its own types; no `@types/*` package is required.

---

### Task 2 — `hooks/use-reduced-motion.ts` (SSR-safe matchMedia hook)

**Goal.** A hook returning a boolean: does the user prefer reduced motion? Must be SSR-safe (no
`window` at module/first-render time), subscribe to live changes (user toggles OS setting while the
app is open), and clean up its listener.

**Files.** `hooks/use-reduced-motion.ts` (NEW).

We implement it explicitly (rather than re-exporting framer-motion's `useReducedMotion`) because we
need a value we can branch on in plain React logic with guaranteed SSR safety and a stable contract
for tests. `useSyncExternalStore` is the correct primitive: it gives a tearing-free subscription with
a separate server snapshot.

```tsx
// hooks/use-reduced-motion.ts
"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) {
    return () => {};
  }
  const mql = window.matchMedia(QUERY);
  // Safari < 14 used addListener/removeListener; modern browsers use add/removeEventListener.
  if (typeof mql.addEventListener === "function") {
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }
  mql.addListener(onChange);
  return () => mql.removeListener(onChange);
}

function getSnapshot(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(QUERY).matches;
}

function getServerSnapshot(): boolean {
  // On the server we cannot know the user's preference; assume motion is allowed.
  // The client snapshot reconciles immediately after hydration without layout shift,
  // because reduced-motion only gates animation, not layout.
  return false;
}

/**
 * SSR-safe `prefers-reduced-motion` hook. Returns `true` when the user has requested
 * reduced motion. Subscribes to live OS-level changes and cleans up on unmount.
 */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
```

**Notes.**

- `getServerSnapshot` returning `false` means the *first* server-rendered HTML assumes motion is
  allowed. Because reduced-motion never changes *layout* (only whether transforms animate), there is
  no hydration layout shift — the worst case is one suppressed frame of animation on first paint for
  a reduced-motion user, which is the desired outcome anyway.
- Tests drive this hook by stubbing `window.matchMedia` (see §8).

---

### Task 3 — `lib/motion.ts` (shared tokens, variants, and the reduce helper)

**Goal.** One canonical source for every transition curve, spring, and variant set, plus a helper
that collapses any variants object to opacity-only / no-op for reduced motion.

**Files.** `lib/motion.ts` (NEW).

```tsx
// lib/motion.ts
import type { Transition, Variants } from "framer-motion";

/* ------------------------------------------------------------------ */
/* Primitive timing tokens                                             */
/* ------------------------------------------------------------------ */

/** Calm ease for discrete enter/exit tweens. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const; // a soft "ease-out-expo"

export const DURATION = {
  fast: 0.12,
  base: 0.2,
  slow: 0.26,
} as const;

/** Default tween for discrete fire-and-forget transitions. */
export const tween: Transition = {
  type: "tween",
  duration: DURATION.base,
  ease: EASE_OUT,
};

/** Firm, quick-settling spring for user-driven / interruptible surfaces (sidebar, layout). */
export const spring: Transition = {
  type: "spring",
  stiffness: 320,
  damping: 30,
  mass: 0.9,
};

/** Spring used specifically for framer-motion `layout` projection of the message list. */
export const layoutSpring: Transition = {
  type: "spring",
  stiffness: 350,
  damping: 34,
  mass: 0.8,
};

/* ------------------------------------------------------------------ */
/* Variants                                                            */
/* ------------------------------------------------------------------ */

/** Chat message: rises 8px + fades in; fades + slightly scales out on removal. */
export const messageVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: tween },
  exit: { opacity: 0, y: -4, scale: 0.98, transition: { ...tween, duration: DURATION.fast } },
};

/** Thinking-steps container: orchestrates a stagger of its step children. */
export const stepsContainerVariants: Variants = {
  initial: {},
  animate: {
    transition: { staggerChildren: 0.05, delayChildren: 0.04 },
  },
  exit: {},
};

/** Individual thinking step: fades + slides in; honors the container's stagger. */
export const stepVariants: Variants = {
  initial: { opacity: 0, x: -6 },
  animate: { opacity: 1, x: 0, transition: { ...tween, duration: DURATION.fast } },
  exit: { opacity: 0, x: -6, transition: { duration: DURATION.fast } },
};

/** Expandable region (thinking-steps body) — collapses via height + opacity. */
export const collapseVariants: Variants = {
  collapsed: { height: 0, opacity: 0, transition: { ...tween, duration: DURATION.fast } },
  open: { height: "auto", opacity: 1, transition: tween },
};

/** Route badge: small fade + pop on mount. */
export const badgeVariants: Variants = {
  initial: { opacity: 0, scale: 0.85 },
  animate: { opacity: 1, scale: 1, transition: { ...tween, duration: DURATION.fast } },
  exit: { opacity: 0, scale: 0.85, transition: { duration: DURATION.fast } },
};

/** Skeleton ↔ content crossfade (opacity only — safe under reduced motion). */
export const crossfadeVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: tween },
  exit: { opacity: 0, transition: { duration: DURATION.fast } },
};

/** Streaming caret blink — pure opacity, looped. */
export const caretVariants: Variants = {
  blink: {
    opacity: [1, 1, 0, 0],
    transition: { duration: 1, times: [0, 0.5, 0.5, 1], repeat: Infinity, ease: "linear" },
  },
};

/* ------------------------------------------------------------------ */
/* Reduced-motion helper                                              */
/* ------------------------------------------------------------------ */

/**
 * Collapse a variants object to no-op (every state identical, instant) so that
 * when `reduced` is true no transform/opacity *animates* — the element simply
 * renders in its `animate` state. Used where we still want to mount a motion
 * component (to keep AnimatePresence keys stable) but suppress all motion.
 *
 * Prefer this for conditional logic; `<MotionConfig reducedMotion="user">` is the
 * global backstop that additionally strips transforms from any non-gated component.
 */
export function reduceVariants(variants: Variants, reduced: boolean): Variants {
  if (!reduced) return variants;
  const settled = variants.animate ?? {};
  const instant: Transition = { duration: 0 };
  return {
    initial: { ...settled, transition: instant },
    animate: { ...settled, transition: instant },
    exit: { ...settled, transition: instant },
  };
}
```

**Notes.**

- `collapseVariants` animates `height: "auto"` — the one place we knowingly touch a layout property.
  See §7 for why it's acceptable here (small, infrequent, user-initiated) and the measured-height
  alternative.
- `reduceVariants` keeps the element mounted in its `animate` state with zero-duration transitions, so
  `AnimatePresence` keys stay valid and nothing visually moves.

---

### Task 4 — Wrap `app/providers.tsx` with `LazyMotion` + `MotionConfig`

**Goal.** Globally enable lazy-loaded DOM animation features and the reduced-motion backstop. Adopt
the `m` component convention app-wide.

**Files.** `app/providers.tsx` (EDIT — created in M0; this adds the motion wrappers around the
existing provider stack).

```tsx
// app/providers.tsx
"use client";

import { LazyMotion, MotionConfig, domAnimation } from "framer-motion";
// ...existing M0/M1 providers (ThemeProvider, QueryClientProvider, etc.)

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    // ...existing providers wrap the tree; insert the motion wrappers innermost-but-around-children:
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}
```

If `Providers` already nests `ThemeProvider` / `QueryClientProvider`, place
`<LazyMotion><MotionConfig>` as the **innermost** wrapper around `{children}` so motion config covers
the whole app but does not interfere with theme/query context ordering.

**The `m.` usage pattern (app-wide convention).** Because of `strict`, importing the heavy `motion`
component throws at runtime in dev and is flagged. Every animated element uses `m`:

```tsx
import { m, AnimatePresence } from "framer-motion";

// ✅ correct
<m.div initial="initial" animate="animate" exit="exit" variants={messageVariants} />

// ❌ forbidden under LazyMotion strict — will throw
// import { motion } from "framer-motion";
// <motion.div ... />
```

`AnimatePresence` is imported from `framer-motion` as usual (it is feature-agnostic and works with
`m`).

> Consider adding an ESLint `no-restricted-imports` rule to ban the named `motion` import from
> `framer-motion`, surfacing the `strict` violation at lint time rather than runtime. (Optional;
> document it but it can land with M5's lint hardening.)

---

### Task 5 — Message enter/exit + `layout` (`message-list.tsx` / `chat-message.tsx`), memoized

**Goal.** New messages rise + fade in; removed messages fade + lift out; settled messages reflow with
a spring when the list changes — **except** the in-flight streaming message, which is excluded from
`layout` and whose enter animation fires exactly once despite per-token re-renders.

**Files.** `message-list.tsx`, `chat-message.tsx` (EDIT).

**5a — `message-list.tsx`: AnimatePresence keyed by message id.**

```tsx
// features/chat/components/message-list.tsx (relevant excerpt)
"use client";

import { AnimatePresence } from "framer-motion";
import { ChatMessage } from "./chat-message";
import { MessageLoading } from "./message-loading";
import type { Message } from "@/types";

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-10 pt-10">
      <AnimatePresence initial={false} mode="popLayout">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
      </AnimatePresence>

      <AnimatePresence>{isLoading && <MessageLoading key="loading" />}</AnimatePresence>
    </div>
  );
}
```

- `key={message.id}` is **mandatory** for correct enter/exit — `AnimatePresence` tracks presence by
  key. IDs are stable `uuid`s (`page.tsx:58`), so reordering/insertion is tracked correctly.
- `initial={false}` suppresses enter animation on the **first mount** of the list (the existing
  history shouldn't all animate in on page load); only messages added *after* mount animate.
- `mode="popLayout"` lets exiting items pop out of flow so remaining items reflow smoothly via
  `layout` rather than waiting for the exit to finish.

**5b — `chat-message.tsx`: motion wrapper, layout exclusion, memoization.**

```tsx
// features/chat/components/chat-message.tsx (motion wrapper excerpt)
"use client";

import { memo } from "react";
import { m } from "framer-motion";
import { cn } from "@/lib/utils";
import { messageVariants, reduceVariants, layoutSpring } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { StreamingCaret } from "./streaming-caret";
import type { Message } from "@/types";

interface ChatMessageProps {
  message: Message;
}

function ChatMessageImpl({ message }: ChatMessageProps) {
  const reduced = useReducedMotion();
  const isUser = message.role === "user";
  const isStreaming = message.status === "streaming";

  return (
    <m.div
      // Exclude the streaming message from layout projection — its height changes every
      // token and animating that reflow is pure jank. Settled messages get layout.
      layout={isStreaming ? false : "position"}
      layoutTransition={layoutSpring}
      variants={reduceVariants(messageVariants, reduced)}
      initial="initial"
      animate="animate"
      exit="exit"
      className={cn(
        "flex w-full gap-4 p-5 rounded-xl",
        isUser
          ? "bg-primary/5 flex-row-reverse"
          : "bg-card border border-border shadow-sm"
      )}
    >
      {/* ...avatar + header + badge (Task 8) unchanged from M3... */}

      <div className={cn("flex-1 space-y-2 min-w-0", isUser ? "text-right" : "text-left")}>
        {/* ...header... */}

        <div className="text-sm leading-relaxed prose prose-sm max-w-none break-words dark:prose-invert">
          {/* ...existing ReactMarkdown / user <p> body from M3... */}
          {!isUser && isStreaming && <StreamingCaret reduced={reduced} />}
        </div>

        {/* ...sources footer... */}
      </div>
    </m.div>
  );
}

/**
 * Memoized so per-token appends to the *streaming* message don't re-trigger the enter
 * animation of *other* messages, and so settled messages never re-render. The comparator
 * re-renders only when fields that affect render actually change.
 */
export const ChatMessage = memo(ChatMessageImpl, (prev, next) => {
  const a = prev.message;
  const b = next.message;
  return (
    a.id === b.id &&
    a.content === b.content &&
    a.status === b.status &&
    a.route === b.route &&
    a.sourcesCount === b.sourcesCount
  );
});
ChatMessage.displayName = "ChatMessage";
```

- **Why memo matters:** while a message streams, the parent re-renders on each token. Without
  `memo`, *every* `ChatMessage` (including settled ones) reconciles, re-evaluating variants and
  re-running `layout` measurement → dropped frames. With `memo`, only the streaming message
  re-renders (its `content` changed); settled siblings are skipped entirely.
- **`layout="position"` not `layout`:** we animate position changes (insert/reorder) but not size
  changes of settled messages, which avoids animating the message's own internal content reflow.
- **`status` field:** this milestone assumes the unified `Message` shape from M2 carries
  `status?: "streaming" | "done" | ...`. If M2 hasn't landed `status` yet, gate the caret on a local
  `isStreaming` prop threaded from the list; the structure is identical.

---

### Task 6 — Streaming caret component

**Goal.** A blinking cursor at the tail of the in-flight assistant body while `status === "streaming"`.
Under reduced motion it renders a **static** (non-blinking) block so the user still sees an indicator
without animation.

**Files.** `streaming-caret.tsx` (NEW).

```tsx
// features/chat/components/streaming-caret.tsx
"use client";

import { m } from "framer-motion";
import { caretVariants } from "@/lib/motion";

interface StreamingCaretProps {
  /** Pass the resolved reduced-motion value down to avoid a second hook subscription. */
  reduced: boolean;
}

/**
 * Blinking caret shown at the end of a streaming assistant message body.
 * - Reduced motion: a static, solid block (no opacity animation).
 * - Default: a softly blinking block (pure-opacity loop, GPU-cheap).
 */
export function StreamingCaret({ reduced }: StreamingCaretProps) {
  if (reduced) {
    return (
      <span
        aria-hidden="true"
        className="ml-0.5 inline-block h-4 w-[2px] translate-y-[2px] bg-foreground/70 align-baseline"
      />
    );
  }

  return (
    <m.span
      aria-hidden="true"
      variants={caretVariants}
      animate="blink"
      className="ml-0.5 inline-block h-4 w-[2px] translate-y-[2px] bg-foreground/70 align-baseline"
    />
  );
}
```

- The caret is `aria-hidden` — it is a visual affordance; screen readers announce streamed text via
  the live region the message body already provides (M3). Do not animate it for AT users.
- Blink is **opacity only** (`caretVariants`), so even without the reduced check it never costs
  layout/paint. The explicit static branch exists because a *looping* animation is exactly the kind
  of motion reduced-motion users want stopped entirely.

---

### Task 7 — Thinking-steps expand/collapse + staggered children

**Goal.** The thinking-steps panel expands/collapses (height + opacity) and its step rows stagger in
as steps arrive (today: synthesized; M9: live from `status` events).

**Files.** `thinking-steps.tsx` (EDIT).

```tsx
// features/chat/components/thinking-steps.tsx (motion excerpt)
"use client";

import { useState } from "react";
import { AnimatePresence, m } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  collapseVariants,
  stepsContainerVariants,
  stepVariants,
  reduceVariants,
} from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

interface Step {
  id: string;
  label: string;
}

export function ThinkingSteps({ steps }: { steps: Step[] }) {
  const reduced = useReducedMotion();
  const [open, setOpen] = useState(true);

  return (
    <div className="rounded-lg border border-border bg-muted/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-xs font-medium text-muted-foreground"
      >
        <span>Thinking</span>
        <m.span
          aria-hidden="true"
          animate={reduced ? undefined : { rotate: open ? 180 : 0 }}
          transition={{ duration: 0.18 }}
        >
          <ChevronDown className="h-4 w-4" />
        </m.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <m.div
            key="steps-body"
            variants={reduceVariants(collapseVariants, reduced)}
            initial="collapsed"
            animate="open"
            exit="collapsed"
            // Required so height:auto animates without overflow flashing during collapse.
            style={{ overflow: "hidden" }}
          >
            <m.ul
              variants={reduceVariants(stepsContainerVariants, reduced)}
              initial="initial"
              animate="animate"
              className="space-y-1 px-3 pb-3"
            >
              <AnimatePresence initial={false}>
                {steps.map((step) => (
                  <m.li
                    key={step.id}
                    variants={reduceVariants(stepVariants, reduced)}
                    initial="initial"
                    animate="animate"
                    exit="exit"
                    className={cn("flex items-center gap-2 text-xs text-foreground/80")}
                  >
                    {step.label}
                  </m.li>
                ))}
              </AnimatePresence>
            </m.ul>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
```

- **Stagger** comes from `stepsContainerVariants.animate.transition.staggerChildren` orchestrating the
  `stepVariants` children — each step row eases in slightly after the previous. New steps appended
  while open also animate in via the inner `AnimatePresence`.
- **Collapse** animates `height: "auto"` with `overflow: hidden` to clip content during the
  transition. See §7 for the cost note and the measured-height fallback if this janks on long lists.
- Under reduced motion every variant collapses to instant via `reduceVariants`, and the chevron
  rotation is dropped (`animate={undefined}`) — the panel still opens/closes, just without motion.

---

### Task 8 — Route-badge mount transition

**Goal.** The route badge (RAG / WEB / DIRECT / …) fades + pops in when it mounts on an assistant
message, and fades out if it changes/unmounts.

**Files.** `route-badge.tsx` (EDIT).

```tsx
// features/chat/components/route-badge.tsx
"use client";

import { m, AnimatePresence } from "framer-motion";
import { badgeVariants, reduceVariants } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { Badge } from "@/components/ui/badge";
import type { RouteType } from "@/types";

export function RouteBadge({ route }: { route?: RouteType }) {
  const reduced = useReducedMotion();

  return (
    <AnimatePresence mode="wait">
      {route && (
        <m.span
          key={route}
          variants={reduceVariants(badgeVariants, reduced)}
          initial="initial"
          animate="animate"
          exit="exit"
          className="inline-flex"
        >
          <Badge variant="outline" className="text-[10px] px-2 h-5 font-normal text-muted-foreground">
            {route}
          </Badge>
        </m.span>
      )}
    </AnimatePresence>
  );
}
```

- `key={route}` + `mode="wait"` gives a clean swap if the route value changes (old fades out, new
  fades in) rather than a cross-dissolve.
- Wrap only the badge in `m.span` (not the shadcn `Badge` itself) to keep `Badge` styling untouched.

---

### Task 9 — Sidebar open/close spring (replace CSS width transition)

**Goal.** Replace the `transition-all` width/opacity CSS animation (`app/page.tsx:107-117`) with a
spring-driven open/close on `app-sidebar`. The width still changes, but the motion is a tuned spring
and we avoid `transition-all`.

**Files.** `components/layout/app-sidebar.tsx` (EDIT), and the parent that controls `isSidebarOpen`
(thin `page.tsx` / chat-screen shell — pass `open` instead of rendering the CSS-wrapper `<div>`).

```tsx
// components/layout/app-sidebar.tsx (motion excerpt)
"use client";

import { AnimatePresence, m } from "framer-motion";
import { spring, reduceVariants } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

const sidebarVariants = {
  open: { width: 256, opacity: 1, transition: spring },     // 256px == w-64
  closed: { width: 0, opacity: 0, transition: spring },
};

export function AppSidebar({ open, onClearSession, onToggle }: AppSidebarProps) {
  const reduced = useReducedMotion();

  return (
    <m.aside
      initial={false}                                    // don't animate width on first paint
      animate={open ? "open" : "closed"}
      variants={reduceVariants(sidebarVariants, reduced)}
      className="h-full shrink-0 overflow-hidden border-r border-border bg-muted/40"
    >
      {/* Inner content keeps a fixed w-64 so text doesn't reflow while the shell width animates. */}
      <div className="flex h-full w-64 flex-col p-4">
        {/* ...existing sidebar content (header/toggle/cards/reset) from M3... */}
      </div>
    </m.aside>
  );
}
```

- **`initial={false}`** prevents the sidebar from animating its width from 0 on first paint (it should
  render already-open without an entrance animation).
- The animated shell is `m.aside`; the **inner content is fixed at `w-64`** and clipped by
  `overflow-hidden`, so the body text doesn't reflow/squish while the shell width springs — only the
  container width animates, content slides out of view cleanly.
- Width is a layout property; we accept it here because the sidebar toggles infrequently and is
  user-driven (not a 60fps hot path). If profiling shows jank on low-end devices, the fallback is to
  animate `x` (translate the panel off-canvas) with a fixed-width container and animate the sibling's
  margin via `layout` — pure-transform, but more layout plumbing. Document the width approach as the
  default; note the transform fallback.
- Under reduced motion, `reduceVariants` makes the open/close **instant** (snap, no spring).
- The "open when closed" toggle button (`page.tsx:122-133`) stays; only the wrapper changes from a
  CSS `<div>` to `m.aside`.

> If M3 keeps the sidebar in a shadcn `Sheet` on mobile, leave the `Sheet`'s built-in transition
> alone — this task targets the desktop inline sidebar that currently uses the CSS width tween.

---

### Task 10 — Skeleton → content crossfade

**Goal.** When the loading skeleton (`message-loading`) is replaced by the real assistant message,
crossfade rather than hard-swap.

**Files.** `message-loading.tsx` (EDIT — add exit variant so `AnimatePresence` can fade it out);
crossfade orchestration lives in `message-list.tsx` (Task 5a already wraps `MessageLoading` in its own
`AnimatePresence`).

```tsx
// features/chat/components/message-loading.tsx (excerpt)
"use client";

import { m } from "framer-motion";
import { Bot } from "lucide-react";
import { crossfadeVariants, reduceVariants } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export function MessageLoading() {
  const reduced = useReducedMotion();

  return (
    <m.div
      variants={reduceVariants(crossfadeVariants, reduced)}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex w-full gap-4 p-5 rounded-xl bg-card border border-border shadow-sm"
    >
      <Avatar className="h-8 w-8 border shrink-0">
        <AvatarFallback className="bg-muted text-foreground">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 space-y-2 pt-1">
        {/* Skeleton bars. Keep the pulse ONLY when motion is allowed; static under reduced motion. */}
        <div className={cn("h-4 w-24 rounded bg-muted", !reduced && "animate-pulse")} />
        <div className={cn("h-4 w-3/4 rounded bg-muted/70", !reduced && "animate-pulse")} />
        <div className={cn("h-4 w-1/2 rounded bg-muted/70", !reduced && "animate-pulse")} />
      </div>
    </m.div>
  );
}
```

- **Crossfade is opacity-only** (`crossfadeVariants`) — safe and meaningful even under reduced motion,
  but we still route it through `reduceVariants` so reduced-motion users get an instant swap consistent
  with the rest of the app (an opacity fade is borderline; instant is the safer, consistent default).
- The CSS `animate-pulse` shimmer is itself motion — gate it on `!reduced` so reduced-motion users see
  a **static** skeleton, not a pulsing one. This is an easy-to-miss reduced-motion violation; call it
  out explicitly in review.
- The two `AnimatePresence` boundaries in `message-list` (one for messages, one for the loader) keep
  the loader's exit independent of message enter, so they overlap into a true crossfade.

---

## 7. Performance & Reduced Motion

**Verifying 60fps.**

1. Open Chrome DevTools → **Performance** panel. Enable "Screenshots" and the FPS meter (Rendering
   tab → "Frame Rendering Stats").
2. Record while a message streams (simulate via the M2 mock / a throwaway `setInterval` appending
   tokens to one message's `content`). The frame chart should hold ~16.7ms/frame (green ~60fps), no
   long red "Layout"/"Recalculate Style" bars on the main thread during streaming.
3. Inspect the animation in the **Layers** / "Animations" panel — confirm the message/caret animate
   on the **compositor** (transform/opacity), not via "Layout" or "Paint" events per frame.

**Why memoize messages.** The streaming message re-renders on every token. The single most important
perf guarantee in M4 is that this re-render is **isolated** to that one message (via `React.memo`,
Task 5b) and does **not** cause sibling `ChatMessage`s to reconcile or any `layout` projection to
re-measure. Without memo, every token triggers an O(n) reconcile across all messages plus layout
measurement → guaranteed jank past ~20 messages.

**Avoiding `height: auto` cost.** Animating `height: "auto"` (thinking-steps collapse, Task 7) forces
framer-motion to measure the natural height and interpolate — a layout read, not a compositor-only
animation. This is acceptable for the thinking-steps panel because it (a) toggles infrequently,
(b) is user-initiated, and (c) holds a small number of rows. **If profiling shows jank** (e.g. with
many steps, or on low-end hardware), switch that single region to framer-motion's `layout` projection
(which animates the size change via transform/scale instead of height) or a measured-height approach
(`useMeasure` → animate to a pixel value). Do **not** use `height: auto` for the streaming message
body — that's why the streaming message has `layout={false}`.

**The reduced-motion contract.** When `prefers-reduced-motion: reduce` is set:

- The global `<MotionConfig reducedMotion="user">` strips **all transform tracks** from every `m`
  component automatically (opacity is preserved by framer-motion's "user" mode).
- Component-level `reduceVariants(...)` additionally collapses our variants to **instant** (duration 0)
  so even opacity doesn't visibly animate where we've chosen "static is better."
- Looping/decorative motion is **fully suppressed**, not merely shortened: the streaming caret renders
  static (Task 6); the skeleton `animate-pulse` is removed (Task 10); the chevron rotation is dropped
  (Task 7).
- **Assertion:** with reduced motion on, **no transform animation fires anywhere**. This is testable
  (§8) and manually verifiable (toggle OS setting, record Performance — zero transform animation
  entries).

**Reduced-motion / 60fps checklist.**

- [ ] Only `opacity` and `transform` animate in any hot path (DevTools confirms compositor-only).
- [ ] `transition-all` removed from `chat-message` and the sidebar wrapper.
- [ ] Streaming message has `layout={false}` (or `layout="position"` excluding size).
- [ ] All `ChatMessage`s are `React.memo`'d with a content/status comparator.
- [ ] Streaming a long message holds ~60fps with no Layout/Paint bars per frame.
- [ ] Reduced motion on → caret static, skeleton not pulsing, chevron not rotating, no transform anims.
- [ ] `MotionConfig reducedMotion="user"` present at provider root; `LazyMotion strict` enforced.

---

## 8. Testing & Verification

**Constraints with framer-motion + jsdom (RTL).** jsdom has no layout engine and no real
`requestAnimationFrame`-driven compositor, so framer-motion animations don't truly "run" and
`AnimatePresence` exit timing is unreliable in unit tests. We therefore test **behavior and the
reduced-motion branch**, not pixel-level animation:

1. **`useReducedMotion` hook.** Stub `window.matchMedia` and assert the returned boolean tracks the
   query, including a live `change` event:

   ```ts
   function mockMatchMedia(matches: boolean) {
     const listeners = new Set<() => void>();
     window.matchMedia = ((query: string) => ({
       matches,
       media: query,
       addEventListener: (_: string, cb: () => void) => listeners.add(cb),
       removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
       addListener: (cb: () => void) => listeners.add(cb),
       removeListener: (cb: () => void) => listeners.delete(cb),
       dispatchEvent: () => false,
       onchange: null,
     })) as unknown as typeof window.matchMedia;
     return { fire: () => listeners.forEach((cb) => cb()) };
   }
   ```

   Render a probe component using the hook; assert it reads `true`/`false` correctly and re-renders
   when `fire()` is called after flipping `matches`.

2. **Reduced-motion rendering branches.** With `matchMedia` mocked to `matches: true`:
   - `StreamingCaret` renders the **static** `<span>` (no `m.span` blink) — assert the element has no
     animation styles / matches the static branch markup.
   - `MessageLoading` skeleton bars do **not** carry `animate-pulse` (query class list).
   - `ThinkingSteps` chevron has no `rotate` animation prop.

3. **Structural/behavioral tests (motion-agnostic).**
   - `MessageList` renders one `ChatMessage` per message, each keyed by `message.id`.
   - `ChatMessage` memo comparator: re-render with identical `content`/`status` does **not** produce a
     new render (spy on render count); changing `content` does.
   - `ThinkingSteps` toggles `aria-expanded` and shows/hides the steps body on button click.
   - `RouteBadge` renders the badge when `route` is set, nothing when undefined.

4. **Mocking note.** If a test trips on framer-motion internals (rare with `m` + jsdom), mock the
   module to passthrough components: `vi.mock("framer-motion", ...)` returning `m.div = <div>` etc.,
   plus `AnimatePresence` as a fragment. Prefer the reduced-motion branch tests above; reserve the
   full mock for cases where presence/exit timing would otherwise hang the test.

**Manual verification (the plan's gate).**

- Toggle OS reduced motion **on** (macOS: Accessibility → Display → Reduce motion; GNOME: `gsettings
  set org.gnome.desktop.interface enable-animations false`; Windows: Settings → Accessibility → Visual
  effects → Animation effects off). Reload. Confirm: messages appear instantly, sidebar snaps,
  thinking-steps snap, caret is a static block, skeleton is static, **no transforms** in the
  Performance recording.
- Toggle reduced motion **off**. Confirm: messages rise+fade in, exit fades out, sidebar springs,
  thinking-steps expand with staggered rows, badge pops, caret blinks.
- Simulate a stream (mock token append). Confirm the caret blinks at the body tail and the
  Performance panel holds ~60fps with no per-frame Layout/Paint.

**Per-milestone gates (must pass):** `npm run lint`, `prettier --check`, `tsc --noEmit`,
`vitest run`, `next build`.

---

## 9. Risks & Gotchas

- **`AnimatePresence` + keys + reorder.** Presence is tracked by `key`. Using array index as key
  breaks enter/exit on insert/reorder. We key by stable `message.id` (uuid) — never index. Verify no
  duplicate keys (would silently drop animations and React-warn).
- **`layout` jank with frequently-updating content.** Applying `layout` to a node whose content
  changes every frame (the streaming message) makes framer-motion re-measure and animate the reflow
  every token → severe jank. **Mitigation:** the streaming message sets `layout={false}`; only settled
  messages get `layout="position"`. This is the single most important gotcha — get it wrong and
  streaming visibly stutters.
- **Memo comparator correctness.** If the comparator omits a field that affects render (e.g. forgets
  `status`), the caret won't appear/disappear correctly; if it's too loose it can show stale content.
  Keep it to exactly the render-affecting fields (Task 5b) and test it (§8).
- **SSR / hydration.** `m` components render to plain DOM on the server, so no hydration mismatch from
  framer-motion itself. The risk is the reduced-motion *value*: `getServerSnapshot` returns `false`,
  so server HTML assumes motion. Because reduced-motion gates only animation (not layout/markup
  difference — note the static caret vs `m.span` *do* differ in markup), ensure any markup that
  *differs* by reduced-motion (the caret's static `<span>` vs `m.span`) is inside a `"use client"`
  boundary and only rendered after mount for the streaming case, or accept a one-frame post-hydration
  reconcile (the caret only exists during streaming, which is always post-mount, so this is moot in
  practice). Keep all motion components under `"use client"`.
- **Bundle size.** The full `motion` import is heavy. We use `LazyMotion` + `domAnimation` + the `m`
  component with `strict` to fail-fast on accidental heavy imports. Watch the bundle in `next build`
  output; if `framer-motion` shows up large, confirm no stray `import { motion } from "framer-motion"`
  slipped in (the `strict` flag + optional ESLint rule guard this).
- **Reduced motion for the caret.** A looping opacity animation is exactly what reduced-motion users
  want stopped. The caret has an explicit static branch (Task 6) — don't rely on `MotionConfig` alone,
  which only strips *transforms* and would leave the opacity blink running.
- **`animate-pulse` is motion too.** The skeleton's Tailwind pulse is a CSS animation that
  `MotionConfig` does **not** touch. Gate it on `!reduced` (Task 10) or it violates the contract.
- **`overflow: hidden` during collapse.** Without it, `height: auto` collapse flashes overflowing
  content. Set it on the collapsing region (Task 7).
- **`MotionConfig` placement.** Must wrap the whole interactive tree (provider root). If it sits below
  some animated component, that component won't inherit the reduced-motion backstop.

---

## 10. Exit Criteria (checkable)

- [ ] `framer-motion` installed; `npm run typecheck`, `lint`, `next build` pass.
- [ ] `hooks/use-reduced-motion.ts` exists, SSR-safe, subscribes to live `change`, unit-tested.
- [ ] `lib/motion.ts` exports the documented `variants`/`transition`/`spring` tokens + `reduceVariants`.
- [ ] `app/providers.tsx` wraps the tree in `<LazyMotion features={domAnimation} strict>` +
      `<MotionConfig reducedMotion="user">`; app uses the `m` component (no heavy `motion` imports).
- [ ] Messages animate enter (rise+fade) and exit (fade+lift) via `AnimatePresence` keyed by id;
      settled messages reflow via `layout`; the streaming message is excluded from `layout`.
- [ ] All `ChatMessage`s are `React.memo`'d; streaming a message does not re-render siblings.
- [ ] Streaming caret blinks while `status === "streaming"`; renders static under reduced motion.
- [ ] Thinking-steps expand/collapse animates; step children stagger in.
- [ ] Route badge animates on mount/swap.
- [ ] Sidebar opens/closes with a spring (CSS `transition-all` width tween removed); snaps under
      reduced motion.
- [ ] Skeleton → content crossfades; skeleton pulse disabled under reduced motion.
- [ ] **Reduced motion on → no transform animations fire** (verified in DevTools Performance).
- [ ] **~60fps held during a simulated stream** (no per-frame Layout/Paint; compositor-only).
- [ ] Component/hook tests green (`vitest run`).

---

## 11. Commit Plan

Milestone-sized commits on the working branch (`claude/frontend-improvements-planning-1aX4u`), each
independently reviewable:

1. `chore(deps): add framer-motion` — Task 1 (package.json + lockfile).
2. `feat(motion): SSR-safe use-reduced-motion hook` — Task 2 (+ its unit test).
3. `feat(motion): centralize variants/transitions/springs in lib/motion.ts` — Task 3.
4. `feat(motion): LazyMotion + MotionConfig reducedMotion=user at provider root` — Task 4.
5. `feat(chat): message enter/exit + layout via AnimatePresence; memoize ChatMessage` — Task 5.
6. `feat(chat): streaming caret (static under reduced motion)` — Task 6.
7. `feat(chat): thinking-steps expand/collapse + staggered children` — Task 7.
8. `feat(chat): route-badge mount/swap transition` — Task 8.
9. `feat(layout): sidebar open/close spring, replace CSS width transition` — Task 9.
10. `feat(chat): skeleton→content crossfade; disable pulse under reduced motion` — Task 10.
11. `test(motion): reduced-motion branches + memo comparator + hook` — §8 tests (may fold into 2/5).

Each commit keeps `lint`/`typecheck`/`build` green. The series is bisectable: motion can be reviewed
component-by-component, and reverting any single commit leaves the app functional (just less animated).
