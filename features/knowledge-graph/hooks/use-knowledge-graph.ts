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

import { useQuery } from "@tanstack/react-query";
import { graphApi } from "@/features/knowledge-graph/api/graph.api";
import {
  EMPTY_GRAPH,
  type GraphData,
} from "@/features/knowledge-graph/api/graph.schemas";
import { flags } from "@/lib/flags";

/** Stable, session-scoped query key. */
export const graphQueryKey = (sessionId: string) =>
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
  const enabled = flags.knowledgeGraph && Boolean(sessionId);

  const query = useQuery({
    queryKey: graphQueryKey(sessionId),
    queryFn: ({ signal }) => graphApi.fetchGraph(sessionId, signal),
    enabled,
    staleTime: 30_000,
  });

  return {
    graph: query.data ?? EMPTY_GRAPH,
    // Gate the loading flag so a disabled query (idle, never fetched) never reports loading.
    isLoading: enabled && query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    enabled,
    refetch: () => {
      void query.refetch();
    },
  };
}
