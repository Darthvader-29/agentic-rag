"use client";

import * as React from "react";
import { FileText, Layers, ExternalLink } from "lucide-react";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import { ProvenanceBadge } from "@/features/chat/components/rich/provenance-badge";
import type { Source } from "@/types";

interface SourcesPanelProps {
  sources?: Source[];
  count?: number;
}

export function SourcesPanel({ sources, count }: SourcesPanelProps) {
  const total = count ?? sources?.length ?? 0;
  const [open, setOpen] = React.useState(false);

  if (total <= 0) return null;

  if (!sources || sources.length === 0) {
    return (
      <div className="border-border text-muted-foreground mt-3 flex items-center gap-2 border-t pt-3 text-xs">
        <Layers className="h-3.5 w-3.5" />
        <span>
          Referenced {total} chunk{total === 1 ? "" : "s"} from your documents
        </span>
      </div>
    );
  }

  return (
    <CollapsibleSection
      open={open}
      onOpenChange={setOpen}
      triggerLabel="Toggle sources"
      contentClassName="mt-2 space-y-1.5"
      triggerContent={
        <>
          <Layers className="h-3.5 w-3.5" />
          <span>
            Referenced {total} chunk{total === 1 ? "" : "s"}
          </span>
        </>
      }
    >
      {sources.map((s) => (
        <a
          key={s.id}
          href={s.url ?? "#"}
          target={s.url ? "_blank" : undefined}
          rel="noopener noreferrer"
          className="bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground flex items-start gap-2 rounded-md p-2 text-xs transition-colors motion-reduce:transition-none"
        >
          <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="text-foreground block truncate font-medium">
                {s.title}
              </span>
              {/* Phase 7: retrieval-layer provenance. Renders nothing when `layer` is absent
                    (flag off / legacy contract). This is the SINGLE provenance-badge render
                    site for BOTH the generic sources path and the citation path (which delegates
                    here via CitationComponent), so R7's "never show provenance twice" holds. */}
              <ProvenanceBadge layer={s.layer} />
            </span>
            {s.snippet && (
              <span className="line-clamp-2 block">{s.snippet}</span>
            )}
          </span>
          {s.url && <ExternalLink className="h-3 w-3 shrink-0" />}
        </a>
      ))}
    </CollapsibleSection>
  );
}
