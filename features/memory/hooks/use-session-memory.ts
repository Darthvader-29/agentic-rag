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

import { useQuery } from "@tanstack/react-query";
import { flags } from "@/lib/flags";
import { fetchSessionMemory } from "@/features/memory/api/memory.api";
import {
  EMPTY_MEMORY,
  type SessionMemory,
} from "@/features/memory/api/memory.schemas";

/** Stable query-key factory so a future mutation/invalidation can target a session's memory. */
export const sessionMemoryQueryKey = (sessionId: string) =>
  ["session-memory", sessionId] as const;

export function useSessionMemory(sessionId: string) {
  // Gate: feature flag on AND we actually have a session to ask about.
  const enabled = flags.memory && Boolean(sessionId);

  const query = useQuery({
    queryKey: sessionMemoryQueryKey(sessionId),
    queryFn: ({ signal }) => fetchSessionMemory(sessionId, signal),
    enabled,
    // Memory changes only when a turn writes it; a short stale window avoids redundant refetches
    // (the caller forces freshness via `refetch` on turn-finalize).
    staleTime: 30_000,
  });

  // Normalize to the canonical empty value while disabled or before the first load so the panel can
  // render its empty state without special-casing `undefined`.
  const data: SessionMemory = query.data ?? EMPTY_MEMORY;

  return {
    content: data.content,
    updatedAt: data.updated_at,
    /** True only while an ENABLED query is in flight (disabled ⇒ never "loading"). */
    isLoading: enabled && query.isLoading,
    isError: query.isError,
    error: query.error,
    enabled,
    /** Re-pull fresh memory (e.g. after a chat turn finalizes). */
    refetch: query.refetch,
  };
}
