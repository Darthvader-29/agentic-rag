// features/chat/components/stats-panel.tsx
//
// PROVENANCE+STATS lane (Phase 7, FE-4). A collapsible, per-turn observability panel rendered
// under an assistant message. Lazy-loaded + flag-gated by chat-message.tsx (the chunk is never
// fetched when `flags.observability` is off), and self-guarding here so a missing/partial `stats`
// degrades to nothing.
//
// It visualizes the MessageStats that use-streaming-chat collects:
//   • per-stage durations — the CONSECUTIVE deltas between stage `atMs` marks (not the cumulative
//     offsets), drawn as a compact horizontal bar + a labeled list,
//   • total latency (stats.totalMs, filled on `done`),
//   • the resolved route (small badge),
//   • token counts when the backend reported them,
//   • a copyable trace-id chip with a "view trace" affordance — deep-links to Langfuse when
//     NEXT_PUBLIC_LANGFUSE_HOST is set, otherwise copies the id for manual lookup.
//
// MOTION: the per-stage bar widths use a CSS width transition that is suppressed for users who
// prefer reduced motion (both via the `motion-reduce:` utility AND a JS guard that drops the
// transition entirely). No framer-motion here ⇒ no LazyMotion provider needed, so the panel
// renders standalone (e.g. in unit tests) without extra context.
"use client";

import * as React from "react";
import { ChevronDown, Activity, Check, Copy, ExternalLink } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import type { MessageStats } from "@/types";

interface StatsPanelProps {
  /** Per-turn stats. Absent (blocking path / observability off) ⇒ render nothing. */
  stats?: MessageStats;
}

interface StageDelta {
  stage: string;
  /** ms spent IN this stage = this stage's atMs − the previous stage's atMs (first uses 0). */
  durationMs: number;
}

/**
 * Derive per-stage durations from the cumulative `atMs` marks. `atMs` is each stage's arrival
 * offset from request start, so the time spent IN a stage is the gap to the PREVIOUS mark
 * (the first stage is measured from 0 = request start). Negative gaps (clock skew / out-of-order
 * marks) are clamped to 0 so a bar never goes negative.
 */
function toStageDeltas(stages: MessageStats["stages"]): StageDelta[] {
  let prev = 0;
  return stages.map((s) => {
    const durationMs = Math.max(0, s.atMs - prev);
    prev = s.atMs;
    return { stage: s.stage, durationMs };
  });
}

function formatMs(ms: number): string {
  return `${Math.round(ms)} ms`;
}

/** Build a Langfuse trace deep-link when a host is configured, else null (copy-only). */
function langfuseTraceUrl(traceId: string): string | null {
  // NEXT_PUBLIC_* is inlined at build time; absent ⇒ no deep-link, just a copy affordance.
  const host = process.env.NEXT_PUBLIC_LANGFUSE_HOST;
  if (!host) return null;
  return `${host.replace(/\/+$/, "")}/trace/${traceId}`;
}

/** The copyable trace-id chip + "view trace" affordance (deep-link when Langfuse is configured). */
function TraceChip({ traceId }: { traceId: string }) {
  const { copied, copy } = useCopyToClipboard();
  const href = langfuseTraceUrl(traceId);
  // Show a short, readable prefix; the full id is copied / linked.
  const short = traceId.length > 12 ? `${traceId.slice(0, 12)}…` : traceId;

  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        onClick={() => void copy(traceId)}
        aria-label={copied ? "Trace id copied" : `Copy trace id ${traceId}`}
        title={`Copy trace id\n${traceId}`}
        className="bg-muted/60 hover:bg-muted text-foreground inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors motion-reduce:transition-none"
      >
        {copied ? (
          <Check className="h-3 w-3" aria-hidden="true" />
        ) : (
          <Copy className="h-3 w-3" aria-hidden="true" />
        )}
        {short}
      </button>
      {href && (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="View trace in Langfuse"
          title="View trace in Langfuse"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-0.5 text-[10px] transition-colors motion-reduce:transition-none"
        >
          view trace
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      )}
    </span>
  );
}

function StatsPanel({ stats }: StatsPanelProps) {
  const [open, setOpen] = React.useState(false);
  const reduced = useReducedMotion();

  // Self-guard: nothing to show without stats (blocking path / observability off).
  if (!stats) return null;

  const deltas = toStageDeltas(stats.stages);
  // Scale bars to the slowest stage so the longest fills the track (relative, at-a-glance read).
  const maxDelta = deltas.reduce((m, d) => Math.max(m, d.durationMs), 0);
  const totalLabel =
    typeof stats.totalMs === "number" ? formatMs(stats.totalMs) : "…";
  const hasTokens =
    !!stats.tokens && (!!stats.tokens.input || !!stats.tokens.output);

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="border-border mt-3 border-t pt-3"
    >
      <CollapsibleTrigger
        className="text-muted-foreground hover:text-foreground flex w-full items-center gap-2 text-xs font-medium transition-colors motion-reduce:transition-none"
        aria-label="Toggle turn stats"
      >
        <Activity className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Stats · {totalLabel}</span>
        {stats.route && (
          <Badge
            variant="outline"
            className="h-4 px-1.5 text-[10px] font-normal"
            aria-label={`Route: ${stats.route}`}
          >
            {stats.route}
          </Badge>
        )}
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "ml-auto h-4 w-4 transition-transform motion-reduce:transition-none",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>

      <CollapsibleContent className="text-muted-foreground mt-2 space-y-3 text-xs">
        {/* Per-stage durations: a compact bar + the delta in ms, one row per stage. */}
        {deltas.length > 0 && (
          <ul className="space-y-1.5" aria-label="Stage durations">
            {deltas.map((d, i) => {
              const pct = maxDelta > 0 ? (d.durationMs / maxDelta) * 100 : 0;
              return (
                <li key={i} className="space-y-0.5">
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="truncate">{d.stage}</span>
                    <span className="shrink-0 tabular-nums">
                      {formatMs(d.durationMs)}
                    </span>
                  </div>
                  <div
                    className="bg-muted h-1 w-full overflow-hidden rounded-full"
                    role="presentation"
                  >
                    <div
                      className={cn(
                        "bg-primary/70 h-full rounded-full",
                        !reduced &&
                          "transition-[width] duration-300 ease-out motion-reduce:transition-none"
                      )}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="space-y-1">
          {/* Total latency (mirrors the trigger; explicit row inside the body too). */}
          <div className="flex justify-between gap-4">
            <span>Total</span>
            <span className="text-foreground font-medium tabular-nums">
              {totalLabel}
            </span>
          </div>

          {hasTokens && (
            <div className="flex justify-between gap-4">
              <span>Tokens</span>
              <span className="tabular-nums">
                {stats.tokens?.input ?? 0} in / {stats.tokens?.output ?? 0} out
              </span>
            </div>
          )}

          {stats.traceId && (
            <div className="flex items-center justify-between gap-4">
              <span>Trace</span>
              <TraceChip traceId={stats.traceId} />
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export default StatsPanel;
