"use client";

import * as React from "react";
import {
  Brain,
  ChevronDown,
  Check,
  Loader2,
  AlertCircle,
  CircleDot,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
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
  const [open, setOpen] = React.useState(false);
  const hasActive = steps.some((s) => s.state === "active");

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="border-border bg-muted/40 rounded-lg border"
    >
      <CollapsibleTrigger
        className="text-muted-foreground hover:text-foreground flex w-full items-center gap-2 px-3 py-2 text-xs font-medium transition-colors motion-reduce:transition-none"
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
        <ol className="border-border space-y-1.5 border-l pl-3">
          {steps.map((step, i) => (
            <li key={i} className="flex items-center gap-2 text-xs">
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
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
