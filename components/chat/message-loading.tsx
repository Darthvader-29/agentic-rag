"use client";

import { m } from "framer-motion";
import { Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";
import { crossfadeVariants, reduceVariants } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

export function MessageLoading() {
  const reduced = useReducedMotion();

  return (
    <m.div
      variants={reduceVariants(crossfadeVariants, reduced)}
      initial="initial"
      animate="animate"
      exit="exit"
      className="border-border bg-card flex w-full gap-4 rounded-xl border p-5 shadow-sm"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <Avatar className="border-border h-8 w-8 shrink-0 border">
        <AvatarFallback className="bg-muted text-muted-foreground">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 space-y-2 pt-1">
        {/* Gate animate-pulse so reduced-motion users see a static skeleton. */}
        <div
          className={cn(
            "bg-muted h-4 w-24 rounded",
            !reduced && "animate-pulse"
          )}
        />
        <div
          className={cn(
            "bg-muted/70 h-4 w-3/4 rounded",
            !reduced && "animate-pulse"
          )}
        />
        <div
          className={cn(
            "bg-muted/70 h-4 w-1/2 rounded",
            !reduced && "animate-pulse"
          )}
        />
      </div>
      <span className="sr-only">Assistant is generating a response…</span>
    </m.div>
  );
}
