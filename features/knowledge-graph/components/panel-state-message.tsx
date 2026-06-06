// features/knowledge-graph/components/panel-state-message.tsx
//
// Small shared state-row for the knowledge-graph panel's non-data states (error + empty). Both
// states render the SAME chrome inside <GraphShell>: a lucide icon, a one-line message, and an
// `outline`/`sm` action button (RefreshCw + a label) that calls back. Extracting this removes the
// two near-identical blocks in graph-panel.tsx without changing any markup or class names.
//
// Scoped to the knowledge-graph feature on purpose: the memory panel's equivalent rows diverge
// (a persistent card header owns its refresh button, its error row uses a vertical layout + a
// `text-destructive` span + a spinner/disabled affordance, and its empty row has no button at all),
// so folding both panels behind one component would require branch-soup that distorts each. See the
// note in memory-panel.tsx.
"use client";

import * as React from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export function PanelStateMessage({
  icon,
  message,
  actionLabel,
  onAction,
}: {
  /** Leading status icon (e.g. <TriangleAlert /> for error, <Network /> for empty). */
  icon: React.ReactNode;
  /** One-line status text. */
  message: React.ReactNode;
  /** Action button label ("Retry" on error, "Refresh" on empty). */
  actionLabel: string;
  /** Action handler (re-fetch). */
  onAction: () => void;
}) {
  return (
    <>
      {icon}
      <span>{message}</span>
      <Button
        variant="outline"
        size="sm"
        className="mt-1 gap-1"
        onClick={onAction}
      >
        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
        {actionLabel}
      </Button>
    </>
  );
}
