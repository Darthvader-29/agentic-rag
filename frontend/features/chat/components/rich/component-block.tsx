// features/chat/components/rich/component-block.tsx
"use client";

import { flags } from "@/lib/flags";
import { CodeBlock } from "@/features/chat/components/code-block"; // M3 (reused for raw fallback + code type)
import { safeParseComponent, type ComponentSpec } from "./component.schemas";
import { ComponentErrorBoundary } from "./component-error-boundary";
import { TableComponent } from "./table";
import { ChartComponent } from "./chart";
import { CitationComponent } from "./citation";
import { CalloutComponent } from "./callout";
import { CodeComponent } from "./code";
import { MediaComponent } from "./media";

interface ComponentBlockProps {
  /** One opaque spec from Message.components (validated here). */
  spec: unknown;
  /** A message-scoped index, used for stable keys by the caller. */
  index?: number;
}

/** Flag-OFF (or unserializable) fallback: show the spec as collapsed, copyable raw JSON. */
function RawFallback({ spec }: { spec: unknown }) {
  let json: string;
  try {
    json = JSON.stringify(spec, null, 2);
  } catch {
    return null; // unserializable → nothing (never crash)
  }
  return (
    <details className="border-border bg-muted/40 my-3 rounded-md border">
      <summary className="text-muted-foreground hover:text-foreground cursor-pointer px-3 py-1.5 text-xs select-none">
        Rich component (raw)
      </summary>
      <div className="px-3 pb-3">
        <CodeBlock language="json" value={json} />
      </div>
    </details>
  );
}

/** Dispatch a validated spec to its renderer (no flag/validation concerns — that's the caller's). */
function renderComponent(parsed: ComponentSpec) {
  switch (parsed.type) {
    case "table":
      return <TableComponent spec={parsed} />;
    case "chart":
      return <ChartComponent spec={parsed} />;
    case "citation":
      return <CitationComponent spec={parsed} />;
    case "callout":
      return <CalloutComponent spec={parsed} />;
    case "code":
      return <CodeComponent spec={parsed} />;
    case "media":
      return <MediaComponent spec={parsed} />;
    default: {
      // Exhaustiveness guard: a new union member must add a case above or this fails to compile.
      const _never: never = parsed;
      return _never ?? null;
    }
  }
}

export function ComponentBlock({ spec }: ComponentBlockProps) {
  // Dark-launch choke point (R9): the flag is read ONLY here. Off ⇒ never render rich UI; show the
  // raw block so the data is still visible (D3). On ⇒ validate and dispatch.
  if (!flags.richComponents) return <RawFallback spec={spec} />;

  const parsed: ComponentSpec | null = safeParseComponent(spec);
  if (!parsed) return null; // drop-invalid → prose/siblings still render (D2 / §2.5)

  // R17 (H-F5): a schema-VALID spec can still throw at render time (recharts edge case, a failed
  // lazy chunk, etc.). Wrap each renderer in an error boundary so a single bad block degrades to
  // the collapsed raw-JSON fallback instead of unmounting the whole chat surface. Siblings, which
  // each get their own boundary, stay mounted.
  return (
    <ComponentErrorBoundary fallback={<RawFallback spec={spec} />}>
      {renderComponent(parsed)}
    </ComponentErrorBoundary>
  );
}
