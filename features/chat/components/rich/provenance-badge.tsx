// features/chat/components/rich/provenance-badge.tsx
//
// PROVENANCE+STATS lane (Phase 7). A tiny, leaf badge that labels which retrieval LAYER a source
// came from (vector | graph | web | memory). Rendered exactly once per source from inside the
// sources panel (R7: never show provenance twice — citations flow through the same panel).
//
// LEGACY-SAFE: renders NOTHING when `layer` is absent (the pre-Phase-7 contract omits it) or when
// it's an unknown future value. No flag is read here — the panel above it is the gate; this badge
// just refuses to render without data, so it's inert everywhere it's dropped in.
//
// NO PROVIDER DEPENDENCY: accessibility uses a native `title` (hover tooltip) + `aria-label`
// (screen readers) rather than a Radix Tooltip, so the badge is self-contained and renders in any
// tree without a TooltipProvider.
"use client";

import { Database, Network, Globe, Brain, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RetrievalLayer } from "@/types";

type Variant = React.ComponentProps<typeof Badge>["variant"];

interface LayerMeta {
  label: string;
  /** Hover/SR description of where the answer drew from. */
  description: string;
  variant: Variant;
  Icon: LucideIcon;
  /** Semantic chart-token tone (color-coded per layer, theme-aware — never a raw hex). */
  className: string;
}

// Color-coded by layer using the design-system chart tokens (same tone family the callout uses,
// so light/dark themes stay consistent). Each layer gets a distinct hue + a distinct lucide icon.
const LAYER_META: Record<RetrievalLayer, LayerMeta> = {
  vector: {
    label: "Vector",
    description: "Retrieved from the vector index (semantic similarity).",
    variant: "outline",
    Icon: Database,
    className: "border-chart-1/30 bg-chart-1/10 text-chart-1",
  },
  graph: {
    label: "Graph",
    description: "Retrieved from the knowledge graph (entity relations).",
    variant: "outline",
    Icon: Network,
    className: "border-chart-2/30 bg-chart-2/10 text-chart-2",
  },
  web: {
    label: "Web",
    description: "Retrieved from a live web search.",
    variant: "outline",
    Icon: Globe,
    className: "border-chart-4/30 bg-chart-4/10 text-chart-4",
  },
  memory: {
    label: "Memory",
    description: "Recalled from conversation memory.",
    variant: "outline",
    Icon: Brain,
    className: "border-chart-5/30 bg-chart-5/10 text-chart-5",
  },
};

interface ProvenanceBadgeProps {
  /** Which retrieval layer produced the source. Absent ⇒ render nothing (legacy-safe). */
  layer?: RetrievalLayer;
  className?: string;
}

export function ProvenanceBadge({ layer, className }: ProvenanceBadgeProps) {
  if (!layer) return null;
  const meta = LAYER_META[layer];
  if (!meta) return null; // unknown future layer → nothing, never crash
  const { label, description, variant, Icon, className: tone } = meta;
  return (
    <Badge
      variant={variant}
      // Compact, inline next to a source title. `title` = native hover tooltip (no provider needed).
      className={cn(
        "h-4 gap-1 px-1.5 text-[10px] font-normal",
        tone,
        className
      )}
      title={description}
      aria-label={`Source layer: ${label}. ${description}`}
    >
      <Icon className="h-2.5 w-2.5" aria-hidden="true" />
      {label}
    </Badge>
  );
}

export default ProvenanceBadge;
