// features/_shared/use-flagged-session-query.ts
//
// Shared internals for the Phase-7 panel data hooks (memory + knowledge-graph). Both hooks are
// structural twins: a single read query that is (a) flag-gated AND session-gated into `enabled`,
// (b) keyed by a session-scoped query key, (c) given the same short stale window, (d) normalized
// to a canonical empty value so callers never special-case `undefined`, and (e) reports `isLoading`
// only while an ENABLED query is in flight.
//
// This factory captures exactly those internals. It deliberately does NOT homogenize the two hooks'
// public return shapes — each hook wraps this and exposes its own typed surface (memory returns
// content/updatedAt + a Promise-returning refetch; the graph returns its UseKnowledgeGraph interface
// incl. isFetching + a void-returning refetch). Only the shared mechanics live here.
"use client";

import {
  useQuery,
  type QueryKey,
  type UseQueryResult,
} from "@tanstack/react-query";

/**
 * Stale window shared by the flag-gated session panels. Memory is rewritten only when a chat turn
 * finalizes and the graph only grows on ingestion, so a short stale window suppresses redundant
 * refetches while the caller still forces freshness via `refetch`.
 */
export const PANEL_STALE_MS = 30_000;

export interface FlaggedSessionQuery<TData> {
  /** Canonicalized data: the query result, or `fallback` while disabled / before the first load. */
  data: TData;
  /** True only while an ENABLED query is in flight (a disabled/idle query never reports loading). */
  isLoading: boolean;
  /** True when the flag gate AND a non-empty session id are both satisfied. */
  enabled: boolean;
  /** The underlying TanStack query — hooks read `isError`/`error`/`isFetching`/`refetch` off this. */
  query: UseQueryResult<TData>;
}

/**
 * Build the shared query mechanics for a flag-gated, session-scoped panel read.
 *
 * @param flagOn      the feature flag for this panel (already read from `flags`)
 * @param sessionId   the session to scope the query to (empty string ⇒ gate closed)
 * @param queryKey    the session-scoped query key (each hook supplies its own factory's output)
 * @param queryFn     fetches the panel's data for the session (receives the abort signal)
 * @param fallback    the canonical empty value returned while disabled / before the first load
 */
export function useFlaggedSessionQuery<TData>({
  flagOn,
  sessionId,
  queryKey,
  queryFn,
  fallback,
}: {
  flagOn: boolean;
  sessionId: string;
  queryKey: QueryKey;
  queryFn: (ctx: { signal: AbortSignal }) => Promise<TData>;
  fallback: TData;
}): FlaggedSessionQuery<TData> {
  // Gate: feature flag on AND we actually have a session to ask about.
  const enabled = flagOn && Boolean(sessionId);

  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => queryFn({ signal }),
    enabled,
    staleTime: PANEL_STALE_MS,
  });

  return {
    // Normalize to the canonical empty value while disabled or before the first load.
    data: query.data ?? fallback,
    // Gate the loading flag so a disabled query (idle, never fetched) never reports loading.
    isLoading: enabled && query.isLoading,
    enabled,
    query,
  };
}
