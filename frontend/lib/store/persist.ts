// lib/store/persist.ts
//
// Shared zustand persist boilerplate for the localStorage-backed stores
// (auth + provider selection). Both stores persist to localStorage with the
// same `createJSONStorage(() => localStorage)` wiring; this helper centralizes
// that wiring and the localStorage key strings so a typo can't desync them.
//
// The helper is intentionally thin: it forwards `partialize` / `onRehydrateStorage`
// (and any other PersistOptions) untouched via `opts`, so each store keeps exactly
// its own persistence semantics — auth uses partialize + onRehydrateStorage to gate
// on a hydration flag; provider deliberately does NOT, and must not gain one.
import { persist, createJSONStorage } from "zustand/middleware";
import type { PersistOptions } from "zustand/middleware";
import type { StateCreator } from "zustand";

/**
 * Canonical localStorage key strings. The VALUES are part of the on-disk
 * contract — changing one would orphan every user's previously stored state,
 * so they must stay byte-identical.
 */
export const STORAGE_KEYS = {
  auth: "rag_auth",
  providerSelection: "rag_provider_selection",
} as const;

/**
 * Wrap a store initializer with the standard localStorage `persist` middleware.
 * `opts` is spread last so per-store options (`partialize`, `onRehydrateStorage`,
 * etc.) pass through unchanged; omit it to persist the whole state.
 */
export function persistLocal<T, P = T>(
  name: string,
  initializer: StateCreator<T>,
  opts?: Omit<PersistOptions<T, P>, "name" | "storage">
) {
  return persist(initializer, {
    name,
    storage: createJSONStorage(() => localStorage),
    ...opts,
  });
}
