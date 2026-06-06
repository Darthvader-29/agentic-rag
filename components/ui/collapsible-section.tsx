"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/**
 * Shared muted collapsible-header trigger chrome. Lives here so the panels that hand-roll their
 * own trigger (ThinkingSteps drives its chevron + body with framer-motion, not Radix) can reuse
 * the exact same row styling without duplicating the class string. `CollapsibleSection` applies it
 * internally; ThinkingSteps composes it with its own padding via `cn`.
 */
export const COLLAPSIBLE_TRIGGER_CLASS =
  "text-muted-foreground hover:text-foreground flex w-full items-center gap-2 text-xs font-medium transition-colors motion-reduce:transition-none";

interface CollapsibleSectionProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** aria-label for the trigger button. */
  triggerLabel: string;
  /** Trigger content rendered before the trailing rotating chevron. */
  triggerContent: React.ReactNode;
  /** Collapsible body. */
  children: React.ReactNode;
  /** Class for the outer Collapsible root. */
  className?: string;
  /** Class for the CollapsibleContent body wrapper. */
  contentClassName?: string;
  /** Mark the trailing chevron aria-hidden (decorative). */
  chevronAriaHidden?: boolean;
}

/**
 * Shared collapsible-header chrome used by the per-turn observability panels (sources, stats).
 * A muted trigger row with a trailing ChevronDown that rotates 180° when open. Wraps Radix
 * Collapsible so the rotation is driven by the same `open` state + CSS class as before.
 */
export function CollapsibleSection({
  open,
  onOpenChange,
  triggerLabel,
  triggerContent,
  children,
  className,
  contentClassName,
  chevronAriaHidden,
}: CollapsibleSectionProps) {
  return (
    <Collapsible
      open={open}
      onOpenChange={onOpenChange}
      className={cn("border-border mt-3 border-t pt-3", className)}
    >
      <CollapsibleTrigger
        className={COLLAPSIBLE_TRIGGER_CLASS}
        aria-label={triggerLabel}
      >
        {triggerContent}
        <ChevronDown
          aria-hidden={chevronAriaHidden ? "true" : undefined}
          className={cn(
            "ml-auto h-4 w-4 transition-transform motion-reduce:transition-none",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>

      <CollapsibleContent className={contentClassName}>
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
