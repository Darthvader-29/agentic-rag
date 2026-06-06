// components/async-panel.tsx
//
// Shared loading → error → empty → data state ladder for the Phase-7 flag-gated session panels
// (features/memory + features/knowledge-graph). Both panels hand-rolled the SAME branch order off
// their data hook; this component owns that order in one place.
//
// DELIBERATELY PRESENTATION-NEUTRAL: AsyncPanel renders NO chrome of its own — no wrapper element,
// no className, no aria. It selects exactly one of the caller's render slots and returns that node
// verbatim. That is what lets BOTH panels keep their visually-distinct shells unchanged:
//   - memory wraps every state in its persistent <PanelShell> card (header + Brain + optional
//     refresh) and passes shell-wrapped nodes for each slot;
//   - the knowledge-graph passes a bare aria-busy <div> + <GraphSkeleton> for loading, its dashed
//     <GraphShell> + <PanelStateMessage> for error/empty, and a bespoke <section> + canvas for data,
//     plus a `disabled` guard that collapses to null (flag off / no session).
// Because the only thing unified is the BRANCH ORDER (not the markup), the rendered DOM, classNames,
// and aria-labels for each panel are byte-for-byte what they were before. (See the note in
// memory-panel.tsx / panel-state-message.tsx for why the shells themselves are NOT folded together.)
"use client";

import * as React from "react";

/**
 * The loading → error → empty → data ladder shared by the flag-gated session panels, plus an
 * optional leading `disabled` guard (the knowledge-graph collapses to nothing when the flag/session
 * gate is closed; the memory panel never passes it, so its behavior is unchanged).
 *
 * Precedence (first match wins, matching what both panels hand-rolled):
 *   disabled → loading → error → empty → data
 *
 * Each `render*` slot is a thunk so the non-selected branches are never evaluated (e.g. the data
 * renderer that computes node degrees or mounts a canvas only runs when actually shown).
 */
export interface AsyncPanelProps {
  /** When true, render `renderDisabled` (default: nothing). Opt-in — omit it to skip the guard. */
  disabled?: boolean;
  /** Enabled query in flight. */
  isLoading: boolean;
  /** Query failed (after the api layer already absorbed the not-found → empty case). */
  isError: boolean;
  /** Loaded successfully but there is nothing to show (e.g. blank memory / zero graph nodes). */
  isEmpty: boolean;

  /** Collapsed render for the closed gate. Defaults to `null` (render nothing) when omitted. */
  renderDisabled?: () => React.ReactNode;
  renderLoading: () => React.ReactNode;
  renderError: () => React.ReactNode;
  renderEmpty: () => React.ReactNode;
  /** The success render (data present). */
  renderData: () => React.ReactNode;
}

/**
 * Selects and returns ONE caller-supplied state node for the current async state. Adds no markup,
 * className, or aria of its own — the returned node IS the panel's output for that state.
 */
export function AsyncPanel({
  disabled = false,
  isLoading,
  isError,
  isEmpty,
  renderDisabled,
  renderLoading,
  renderError,
  renderEmpty,
  renderData,
}: AsyncPanelProps): React.ReactNode {
  if (disabled) return renderDisabled ? renderDisabled() : null;
  if (isLoading) return renderLoading();
  if (isError) return renderError();
  if (isEmpty) return renderEmpty();
  return renderData();
}
