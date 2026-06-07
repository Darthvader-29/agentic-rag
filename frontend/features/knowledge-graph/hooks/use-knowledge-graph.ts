// features/knowledge-graph/hooks/use-knowledge-graph.ts
//
// TanStack Query data layer for the Phase-7 knowledge graph (forward-compat; DEFAULT OFF).
// One read query per session is the source of truth for the graph panel.
//
// The query is gated on `flags.knowledgeGraph && Boolean(sessionId)` — the feature ships dark, so
// with the flag off (or before a session exists) the hook fires NO network call and returns an
// empty, non-loading graph. The api layer maps a 404 to the empty graph, so "no graph yet" is a
// success with `nodes: []`, not an error. A manual `refetch` lets the panel offer a refresh
// control (the graph grows as the conversation ingests documents).
"use client";

import { graphApi } from "@/features/knowledge-graph/api/graph.api";
import {
  EMPTY_GRAPH,
  type GraphData,
} from "@/features/knowledge-graph/api/graph.schemas";
import { flags } from "@/lib/flags";
import { useFlaggedSessionQuery } from "@/features/_shared/use-flagged-session-query";

/**
 * Stable, session-scoped query key. Used only inside this hook today (non-exported); promote to an
 * export if a mutation/invalidation needs to target a session's graph.
 */
const graphQueryKey = (sessionId: string) =>
  ["knowledge-graph", sessionId] as const;

export interface UseKnowledgeGraph {
  /** Parsed graph; `EMPTY_GRAPH` while disabled, loading, or when the session has no graph. */
  graph: GraphData;
  /** True only while an enabled query is in flight (never true when the gate is closed). */
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  /** True when the gate (flag + sessionId) is open and the query is active. */
  enabled: boolean;
  /** Manual refetch — the graph grows as documents are ingested into the session. */
  refetch: () => void;
}

/**
 * Reads the session's knowledge graph. Enabled only when the knowledge-graph flag is live AND a
 * session id is present. Returns `graph: EMPTY_GRAPH` while disabled or loading so the panel can
 * render its empty/loading state without special-casing `undefined`.
 */
export function useKnowledgeGraph(sessionId: string): UseKnowledgeGraph {
  // Shared mechanics: flag + session gate → enabled, session-scoped key, 30s stale window, and the
  // `data ?? EMPTY_GRAPH` normalization + enabled-gated isLoading. (See useFlaggedSessionQuery.)
  const { data, isLoading, enabled, query } = useFlaggedSessionQuery<GraphData>(
    {
      flagOn: flags.knowledgeGraph,
      sessionId,
      queryKey: graphQueryKey(sessionId),
      queryFn: ({ signal }) => graphApi.fetchGraph(sessionId, signal),
      fallback: EMPTY_GRAPH,
    }
  );

  return {
    graph: data,
    isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    enabled,
    refetch: () => {
      void query.refetch();
    },
  };
}
