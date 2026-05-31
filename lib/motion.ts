import type { Transition, Variants } from "framer-motion";

/* ------------------------------------------------------------------ */
/* Primitive timing tokens                                             */
/* ------------------------------------------------------------------ */

/** Calm ease for discrete enter/exit tweens. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;

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
  exit: {
    opacity: 0,
    y: -4,
    scale: 0.98,
    transition: { ...tween, duration: DURATION.fast },
  },
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
  animate: {
    opacity: 1,
    x: 0,
    transition: { ...tween, duration: DURATION.fast },
  },
  exit: { opacity: 0, x: -6, transition: { duration: DURATION.fast } },
};

/** Expandable region (thinking-steps body) — collapses via height + opacity. */
export const collapseVariants: Variants = {
  collapsed: {
    height: 0,
    opacity: 0,
    transition: { ...tween, duration: DURATION.fast },
  },
  open: { height: "auto", opacity: 1, transition: tween },
};

/** Route badge: small fade + pop on mount. */
export const badgeVariants: Variants = {
  initial: { opacity: 0, scale: 0.85 },
  animate: {
    opacity: 1,
    scale: 1,
    transition: { ...tween, duration: DURATION.fast },
  },
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
    transition: {
      duration: 1,
      times: [0, 0.5, 0.5, 1],
      repeat: Infinity,
      ease: "linear",
    },
  },
};

/* ------------------------------------------------------------------ */
/* Reduced-motion helper                                              */
/* ------------------------------------------------------------------ */

/**
 * Collapse a variants object to no-op (every state identical, instant) so that
 * when `reduced` is true no transform/opacity animates — the element simply
 * renders in its `animate` state. Used where we still want to mount a motion
 * component (to keep AnimatePresence keys stable) but suppress all motion.
 *
 * Prefer this for conditional logic; `<MotionConfig reducedMotion="user">` is the
 * global backstop that additionally strips transforms from any non-gated component.
 *
 * NOTE: only works with variants using `initial`/`animate`/`exit` keys.
 * For named-state variants (e.g. `collapsed`/`open`), pass `transition` directly.
 */
export function reduceVariants(variants: Variants, reduced: boolean): Variants {
  if (!reduced) return variants;
  const settled = (variants.animate as Record<string, unknown>) ?? {};
  const instant: Transition = { duration: 0 };
  return {
    initial: { ...settled, transition: instant },
    animate: { ...settled, transition: instant },
    exit: { ...settled, transition: instant },
  };
}
