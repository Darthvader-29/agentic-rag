// features/memory/hooks/use-session-memory.ts
//
// TanStack Query data layer for the Phase-7 conversation-memory panel. One query reads the running
// summary the backend maintains for a session (GET /api/sessions/{id}/memory). The panel renders the
// markdown body + a relative "updated N ago" stamp.
//
// The query is gated on `flags.memory && !!sessionId` — the feature ships dark (DEFAULT OFF) and there
// is nothing to fetch without a session id. When the gate is closed the hook returns the empty,
// non-loading baseline (no network, no error), so the panel degrades cleanly to its empty state.
//
// `refetch` is surfaced deliberately, and the chat strategies call `invalidateSessionMemory` (below)
// when a turn finalizes so an enabled panel re-pulls fresh memory immediately. Absent that, it would
// only refresh on the global staleTime or a manual refresh.
"use client";

import type { QueryClient } from "@tanstack/react-query";

import { flags } from "@/lib/flags";
import { fetchSessionMemory } from "@/features/memory/api/memory.api";
import {
  EMPTY_MEMORY,
  type SessionMemory,
} from "@/features/memory/api/memory.schemas";
import { useFlaggedSessionQuery } from "@/features/_shared/use-flagged-session-query";

/**
 * Stable query-key factory. Exported so the chat strategies can invalidate this query when a turn
 * finalizes (see `invalidateSessionMemory`) — the single source of the key for fetch + invalidation.
 */
export const sessionMemoryQueryKey = (sessionId: string) =>
  ["session-memory", sessionId] as const;

/**
 * Invalidate a session's memory query so an ENABLED MemoryPanel re-pulls the running summary right
 * after a chat turn finalizes — the backend rewrites the markdown memory as a side effect of a turn,
 * so without this the panel only updates on the 30s staleTime or a manual refresh. Flag-gated and
 * id-guarded (no-op when the memory feature is dark or there's no session), so the chat hooks can
 * call it unconditionally on their success paths.
 */
export function invalidateSessionMemory(
  queryClient: QueryClient,
  sessionId: string
): void {
  if (!flags.memory || !sessionId) return;
  void queryClient.invalidateQueries({
    queryKey: sessionMemoryQueryKey(sessionId),
  });
}

export function useSessionMemory(sessionId: string) {
  // Shared mechanics: flag + session gate → enabled, session-scoped key, 30s stale window, and the
  // `data ?? EMPTY_MEMORY` normalization + enabled-gated isLoading. (See useFlaggedSessionQuery.)
  const { data, isLoading, enabled, query } =
    useFlaggedSessionQuery<SessionMemory>({
      flagOn: flags.memory,
      sessionId,
      queryKey: sessionMemoryQueryKey(sessionId),
      queryFn: ({ signal }) => fetchSessionMemory(sessionId, signal),
      fallback: EMPTY_MEMORY,
    });

  return {
    content: data.content,
    updatedAt: data.updated_at,
    /** True only while an ENABLED query is in flight (disabled ⇒ never "loading"). */
    isLoading,
    isError: query.isError,
    error: query.error,
    enabled,
    /** Re-pull fresh memory (e.g. after a chat turn finalizes). */
    refetch: query.refetch,
  };
}
