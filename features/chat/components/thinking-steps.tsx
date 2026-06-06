"use client";

import { useState } from "react";
import { AnimatePresence, m } from "framer-motion";
import {
  Brain,
  ChevronDown,
  Check,
  Loader2,
  AlertCircle,
  CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { COLLAPSIBLE_TRIGGER_CLASS } from "@/components/ui/collapsible-section";
import {
  collapseVariants,
  stepsContainerVariants,
  stepVariants,
  reduceVariants,
} from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { Step } from "@/types";

function StatusIcon({ state }: { state: Step["state"] }) {
  if (state === "active")
    return <Loader2 className="text-primary h-3.5 w-3.5 animate-spin" />;
  if (state === "complete")
    return <Check className="text-primary h-3.5 w-3.5" />;
  if (state === "error")
    return <AlertCircle className="text-destructive h-3.5 w-3.5" />;
  return <CircleDot className="text-muted-foreground/50 h-3.5 w-3.5" />;
}

interface ThinkingStepsProps {
  steps: Step[];
}

export function ThinkingSteps({ steps }: ThinkingStepsProps) {
  const [open, setOpen] = useState(false);
  const reduced = useReducedMotion();
  const hasActive = steps.some((s) => s.state === "active");

  return (
    <div className="border-border bg-muted/40 rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Toggle reasoning steps"
        className={cn(COLLAPSIBLE_TRIGGER_CLASS, "px-3 py-2")}
      >
        <Brain className="h-3.5 w-3.5" />
        <span>{hasActive ? "Thinking…" : "Reasoning"}</span>
        <span className="text-muted-foreground/60">({steps.length})</span>
        <m.span
          aria-hidden="true"
          className="ml-auto"
          animate={{ rotate: open ? 180 : 0 }}
          transition={reduced ? { duration: 0 } : { duration: 0.18 }}
        >
          <ChevronDown className="h-4 w-4" />
        </m.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <m.div
            key="steps-body"
            variants={collapseVariants}
            initial="collapsed"
            animate="open"
            exit="collapsed"
            // Named-state variants (collapsed/open) need transition passed directly
            // for reduced-motion control; reduceVariants only handles initial/animate/exit keys.
            transition={reduced ? { duration: 0 } : undefined}
            style={{ overflow: "hidden" }}
            className="px-3 pb-3"
          >
            <m.ol
              variants={reduceVariants(stepsContainerVariants, reduced)}
              initial="initial"
              animate="animate"
              className="border-border space-y-1.5 border-l pl-3"
            >
              <AnimatePresence initial={false}>
                {steps.map((step, i) => (
                  <m.li
                    key={i}
                    variants={reduceVariants(stepVariants, reduced)}
                    initial="initial"
                    animate="animate"
                    exit="exit"
                    className="flex items-center gap-2 text-xs"
                  >
                    <StatusIcon state={step.state} />
                    <span
                      className={cn(
                        step.state === "active"
                          ? "text-foreground"
                          : "text-muted-foreground"
                      )}
                    >
                      {step.label}
                    </span>
                    {step.detail && (
                      <span className="text-muted-foreground/60 truncate">
                        — {step.detail}
                      </span>
                    )}
                  </m.li>
                ))}
              </AnimatePresence>
            </m.ol>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
