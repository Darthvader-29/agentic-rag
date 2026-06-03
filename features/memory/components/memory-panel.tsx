// features/memory/components/memory-panel.tsx
//
// Phase-7 conversation-memory panel. Lazy-loaded + flag-gated (flags.memory) by chat-screen.tsx's
// Insights drawer (FE-0 wired the dynamic import to THIS default export with a `sessionId` prop —
// the contract is preserved exactly: `export default function MemoryPanel({ sessionId })`).
//
// Renders the running memory summary the backend maintains for the session:
//   - loading skeleton while the (enabled) query is in flight
//   - empty state ("No memory yet") on a 404 / empty body — the dark-launch-safe default
//   - error state with a retry affordance on any other failure
//   - the markdown body (react-markdown + remark-gfm, same renderer config as chat-message) plus a
//     relative "updated N ago" stamp and a manual refresh button
//
// Degrades cleanly when the flag is off (chat-screen never mounts it) or the endpoint 404s (empty
// state). Motion respects hooks/use-reduced-motion via a motion-reduce: utility on the spinner.
"use client";

import * as React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Brain, RefreshCw, AlertCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionMemory } from "@/features/memory/hooks/use-session-memory";

interface MemoryPanelProps {
  /** The session whose conversation memory to render. Empty string ⇒ empty state (no fetch). */
  sessionId: string;
}

// Module-scope stable renderer map — keeps react-markdown from rebuilding its tree on re-render.
// Compact, prose-friendly variants sized for the narrow Insights drawer.
const markdownComponents: Components = {
  a: ({ children, ...props }) => (
    <a
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline-offset-2 hover:underline"
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ ...props }) => <ul className="list-disc space-y-1 pl-4" {...props} />,
  ol: ({ ...props }) => (
    <ol className="list-decimal space-y-1 pl-4" {...props} />
  ),
  code: ({ children, ...props }) => (
    <code
      className="bg-muted text-foreground rounded px-1 py-0.5 font-mono text-xs"
      {...props}
    >
      {children}
    </code>
  ),
};

/**
 * Formats an ISO-8601 timestamp as a coarse relative string ("just now", "5m ago", "3h ago",
 * "2d ago"). Self-contained — the repo has no date library — and defensive: an unparseable input
 * returns null so the caller simply omits the stamp.
 */
export function formatRelativeTime(
  iso: string | null,
  now: number = Date.now()
): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;

  const diffSec = Math.round((now - then) / 1000);
  if (diffSec < 0) return "just now"; // clock skew / future stamp → don't show a negative
  if (diffSec < 45) return "just now";

  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;

  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;

  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

function PanelShell({
  children,
  onRefresh,
  refreshing,
  showRefresh,
}: {
  children: React.ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  showRefresh?: boolean;
}) {
  return (
    <section
      aria-label="Conversation memory"
      className="border-border bg-card/40 rounded-lg border p-3"
    >
      <header className="mb-2 flex items-center justify-between">
        <span className="text-foreground flex items-center gap-1.5 text-xs font-semibold">
          <Brain className="h-3.5 w-3.5" aria-hidden="true" />
          Memory
        </span>
        {showRefresh && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="h-6 w-6"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Refresh memory"
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                refreshing && "animate-spin motion-reduce:animate-none"
              )}
              aria-hidden="true"
            />
          </Button>
        )}
      </header>
      {children}
    </section>
  );
}

function MemoryPanelImpl({ sessionId }: MemoryPanelProps) {
  const { content, updatedAt, isLoading, isError, refetch } =
    useSessionMemory(sessionId);
  const [refreshing, setRefreshing] = React.useState(false);

  const handleRefresh = React.useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);

  // Loading: enabled query in flight. Skeleton lines stand in for the markdown body.
  if (isLoading) {
    return (
      <PanelShell>
        <div className="space-y-2" aria-busy="true" aria-live="polite">
          <Skeleton className="h-3 w-4/5" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      </PanelShell>
    );
  }

  // Error: any failure other than the 404 the api layer already absorbed into the empty state.
  if (isError) {
    return (
      <PanelShell onRefresh={handleRefresh} refreshing={refreshing} showRefresh>
        <div className="text-muted-foreground flex flex-col items-start gap-2 text-xs">
          <span className="text-destructive flex items-center gap-1.5">
            <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
            Couldn&apos;t load memory.
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                refreshing && "animate-spin motion-reduce:animate-none"
              )}
              aria-hidden="true"
            />
            Retry
          </Button>
        </div>
      </PanelShell>
    );
  }

  const trimmed = content.trim();

  // Empty: no memory yet (404 → EMPTY_MEMORY, or a blank body). The dark-launch-safe default.
  if (!trimmed) {
    return (
      <PanelShell onRefresh={handleRefresh} refreshing={refreshing} showRefresh>
        <p className="text-muted-foreground text-xs">No memory yet</p>
      </PanelShell>
    );
  }

  const relative = formatRelativeTime(updatedAt);

  return (
    <PanelShell onRefresh={handleRefresh} refreshing={refreshing} showRefresh>
      <div className="prose prose-sm dark:prose-invert text-muted-foreground max-w-none text-xs leading-relaxed break-words">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={markdownComponents}
        >
          {content}
        </ReactMarkdown>
      </div>
      {relative && (
        <p className="text-muted-foreground/70 mt-2 text-[10px]">
          Updated {relative}
        </p>
      )}
    </PanelShell>
  );
}

// Default export preserves FE-0's dynamic-import contract (chat-screen imports the default).
export default function MemoryPanel(props: MemoryPanelProps) {
  return <MemoryPanelImpl {...props} />;
}
