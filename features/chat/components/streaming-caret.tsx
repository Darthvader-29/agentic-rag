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
        className="bg-foreground/70 ml-0.5 inline-block h-4 w-[2px] translate-y-[2px] align-baseline"
      />
    );
  }

  return (
    <m.span
      aria-hidden="true"
      variants={caretVariants}
      animate="blink"
      className="bg-foreground/70 ml-0.5 inline-block h-4 w-[2px] translate-y-[2px] align-baseline"
    />
  );
}
