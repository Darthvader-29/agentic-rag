// features/knowledge-graph/components/graph-panel.tsx
//
// Phase-7 knowledge-graph panel (forward-compat; DEFAULT OFF, gated by flags.knowledgeGraph at
// the chat-screen mount). Renders the session's entity graph with react-force-graph-2d.
//
// Mount contract (chat-screen.tsx): default export, props `{ sessionId }`, lazy-loaded with
// `dynamic(..., { ssr: false })` because the force graph needs canvas + window. We ALSO import the
// force-graph itself via `next/dynamic({ ssr:false })` here so the canvas module never evaluates
// server-side (defense in depth) and so tests can mock the bare "react-force-graph-2d" specifier.
//
// Behavior:
//   - data via useKnowledgeGraph (404 ⇒ empty graph, not an error)
//   - node label = entity id, link label = relation
//   - node size + color derived from degree (more-connected entities are bigger/warmer)
//   - loading / empty / error states (degrades cleanly; never throws)
//   - reduced motion ⇒ no force simulation animation (settles in one synchronous layout pass)
"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { Network, RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { AsyncPanel } from "@/components/async-panel";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useKnowledgeGraph } from "@/features/knowledge-graph/hooks/use-knowledge-graph";
import { PanelStateMessage } from "@/features/knowledge-graph/components/panel-state-message";
import type { GraphData } from "@/features/knowledge-graph/api/graph.schemas";

// force-graph mutates nodes/links in place (adds x/y; resolves source/target to node objects), so
// its accessor callbacks receive these loose runtime shapes rather than our strict wire types.
type FGNode = { id?: string | number; [k: string]: unknown };
type FGLink = {
  source?: string | number | { id?: string | number };
  target?: string | number | { id?: string | number };
  relation?: unknown;
  [k: string]: unknown;
};

// Lazy + ssr:false: the force-graph pulls in canvas/window APIs that don't exist on the server.
// A literal specifier keeps it mockable in tests (vi.mock("react-force-graph-2d", ...)).
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => <GraphSkeleton />,
});

interface GraphPanelProps {
  /** The session whose knowledge graph to render. Empty string ⇒ empty state. */
  sessionId: string;
}

const PANEL_HEIGHT = 320;
const NODE_REL_SIZE = 4;

/**
 * Map a node's degree (0..maxDegree) to a hue from cool (low) to warm (high). Degree 0 stays
 * neutral. Returns an HSL string consumable by force-graph's `nodeColor`.
 */
function degreeColor(degree: number, maxDegree: number): string {
  if (maxDegree <= 0) return "hsl(217 91% 60%)"; // single isolated nodes — neutral blue
  const t = Math.min(1, degree / maxDegree);
  // 210° (blue) → 12° (orange-red) as connectivity rises.
  const hue = 210 - t * 198;
  return `hsl(${Math.round(hue)} 85% 55%)`;
}

/**
 * Compute per-node degree from the link list (undirected count — an edge contributes to both
 * endpoints). Used to size + color nodes; returns the degree map and the max for normalization.
 */
function computeDegrees(graph: GraphData): {
  degree: Map<string, number>;
  maxDegree: number;
} {
  const degree = new Map<string, number>();
  for (const n of graph.nodes) degree.set(n.id, 0);
  for (const l of graph.links) {
    const s = String(l.source);
    const t = String(l.target);
    degree.set(s, (degree.get(s) ?? 0) + 1);
    degree.set(t, (degree.get(t) ?? 0) + 1);
  }
  let maxDegree = 0;
  for (const d of degree.values()) if (d > maxDegree) maxDegree = d;
  return { degree, maxDegree };
}

function GraphSkeleton() {
  return (
    <div
      className="flex items-center justify-center"
      style={{ height: PANEL_HEIGHT }}
      aria-hidden="true"
    >
      <Skeleton className="h-full w-full rounded-lg" />
    </div>
  );
}

function GraphShell({
  ariaLabel,
  children,
}: {
  ariaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className="border-border text-muted-foreground flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center text-xs"
    >
      {children}
    </section>
  );
}

export default function GraphPanel({ sessionId }: GraphPanelProps) {
  const { graph, isLoading, isError, refetch, enabled } =
    useKnowledgeGraph(sessionId);
  const reducedMotion = useReducedMotion();

  // Measure the container so the canvas gets explicit pixel dims (force-graph needs them).
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = React.useState(0);
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { degree, maxDegree } = React.useMemo(
    () => computeDegrees(graph),
    [graph]
  );

  // disabled → loading → error → empty → data ladder (shared branch order via <AsyncPanel>). Each
  // slot keeps this panel's own chrome verbatim (bare aria-busy div + GraphSkeleton for loading,
  // the dashed GraphShell + PanelStateMessage for error/empty, the bespoke <section> + canvas for
  // data), so the rendered markup/aria are unchanged — only the duplicated branch sequence is
  // hoisted out. `disabled` reproduces the old `if (!enabled) return null` guard exactly.
  return (
    <AsyncPanel
      disabled={!enabled}
      isLoading={isLoading}
      isError={isError}
      isEmpty={graph.nodes.length === 0}
      renderLoading={() => (
        <div aria-label="Knowledge graph" aria-busy="true">
          <GraphSkeleton />
        </div>
      )}
      renderError={() => (
        <GraphShell ariaLabel="Knowledge graph">
          <PanelStateMessage
            icon={<TriangleAlert className="h-5 w-5" aria-hidden="true" />}
            message="Couldn't load the knowledge graph."
            actionLabel="Retry"
            onAction={() => refetch()}
          />
        </GraphShell>
      )}
      renderEmpty={() => (
        <GraphShell ariaLabel="Knowledge graph">
          <PanelStateMessage
            icon={<Network className="h-5 w-5" aria-hidden="true" />}
            message="No knowledge graph yet — it grows as documents are ingested."
            actionLabel="Refresh"
            onAction={() => refetch()}
          />
        </GraphShell>
      )}
      renderData={() => (
        <section aria-label="Knowledge graph" className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
              <Network className="h-3.5 w-3.5" aria-hidden="true" />
              {graph.nodes.length} entities · {graph.links.length} relations
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Refresh knowledge graph"
              onClick={() => refetch()}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>

          <div
            ref={containerRef}
            className={cn(
              "border-border bg-muted/20 overflow-hidden rounded-lg border"
            )}
            style={{ height: PANEL_HEIGHT }}
          >
            {width > 0 && (
              <ForceGraph2D
                width={width}
                height={PANEL_HEIGHT}
                graphData={graph}
                nodeId="id"
                nodeRelSize={NODE_REL_SIZE}
                // Label = entity id (shown on hover).
                nodeLabel={(n: FGNode) => String(n.id ?? "")}
                // Size by degree: 1 + degree, so isolated nodes are still visible.
                nodeVal={(n: FGNode) =>
                  1 + (degree.get(String(n.id ?? "")) ?? 0)
                }
                // Color by degree (cool → warm).
                nodeColor={(n: FGNode) =>
                  degreeColor(degree.get(String(n.id ?? "")) ?? 0, maxDegree)
                }
                // Link label = relation (falls back to nothing when absent).
                linkLabel={(l: FGLink) =>
                  typeof l.relation === "string" ? l.relation : ""
                }
                linkDirectionalArrowLength={3}
                linkDirectionalArrowRelPos={1}
                linkColor={() => "hsl(215 16% 47% / 0.4)"}
                enableNodeDrag={!reducedMotion}
                // Reduced motion: settle the simulation in one synchronous pass (no animated layout).
                warmupTicks={reducedMotion ? 60 : 0}
                cooldownTicks={reducedMotion ? 0 : undefined}
                cooldownTime={reducedMotion ? 0 : undefined}
              />
            )}
          </div>
        </section>
      )}
    />
  );
}
