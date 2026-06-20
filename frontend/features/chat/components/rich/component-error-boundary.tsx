// features/chat/components/rich/component-error-boundary.tsx
"use client";

import * as React from "react";
import { captureError } from "@/lib/observability/sentry";

interface ComponentErrorBoundaryProps {
  /** Rendered when the wrapped subtree throws during render/commit. */
  fallback: React.ReactNode;
  children: React.ReactNode;
}

interface ComponentErrorBoundaryState {
  hasError: boolean;
}

/**
 * Isolates a render crash to a single rich-component block (R17 / H-F5).
 *
 * A chart/table/etc. can be schema-valid yet still throw while rendering (e.g. a recharts
 * edge case, a failed lazy chunk, an `Infinity` axis). React unmounts the NEAREST error
 * boundary's subtree on a render throw — with no boundary that's the whole chat surface. By
 * wrapping each ComponentBlock renderer in this boundary we collapse the blast radius to the
 * one offending block (it degrades to the raw-JSON fallback) while every sibling block and the
 * surrounding conversation stay mounted.
 *
 * Must be a class component — `getDerivedStateFromError`/`componentDidCatch` have no hook
 * equivalent. Kept generic over `fallback` so the caller passes the collapsed RawFallback.
 */
export class ComponentErrorBoundary extends React.Component<
  ComponentErrorBoundaryProps,
  ComponentErrorBoundaryState
> {
  state: ComponentErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ComponentErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    // Best-effort report (no-op unless Sentry is enabled). componentStack is non-PII.
    captureError(error, { componentStack: info.componentStack });
  }

  render(): React.ReactNode {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}
