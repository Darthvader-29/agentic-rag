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
// `refetch` is surfaced deliberately: memory is (re)written by the backend as a side effect of a chat
// turn, so the panel can re-pull fresh memory when a turn finalizes (the caller wires this — e.g. in a
// done-event effect). Otherwise it follows the global staleTime.
"use client";

import { flags } from "@/lib/flags";
import { fetchSessionMemory } from "@/features/memory/api/memory.api";
import {
  EMPTY_MEMORY,
  type SessionMemory,
} from "@/features/memory/api/memory.schemas";
import { useFlaggedSessionQuery } from "@/features/_shared/use-flagged-session-query";

/**
 * Stable query-key factory so a future mutation/invalidation can target a session's memory. Used
 * only inside this hook today (non-exported); promote to an export if a mutation needs to target it.
 */
const sessionMemoryQueryKey = (sessionId: string) =>
  ["session-memory", sessionId] as const;

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
