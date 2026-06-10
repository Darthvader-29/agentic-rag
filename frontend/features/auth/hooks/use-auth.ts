"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/features/auth/store/auth.store";
import { resetIdentityState } from "@/features/auth/lib/reset-identity";

/**
 * Selector facade over the auth store + logout. A guest counts as authenticated for the
 * purpose of frictionless chat (they hold a real token), but `isGuest` lets surfaces like
 * the user-menu show the "Register to save your keys" upgrade CTA.
 */
export function useAuth() {
  const router = useRouter();
  const qc = useQueryClient();
  const accessToken = useAuthStore((s) => s.accessToken);
  const email = useAuthStore((s) => s.email);
  const userId = useAuthStore((s) => s.userId);
  const isGuest = useAuthStore((s) => s.isGuest);
  const hasHydrated = useAuthStore((s) => s.hasHydrated);
  const clear = useAuthStore((s) => s.clear);

  return {
    isAuthenticated: Boolean(accessToken),
    /** True only for a registered (non-guest) authenticated user. */
    isRegistered: Boolean(accessToken) && !isGuest,
    isGuest: Boolean(accessToken) && isGuest,
    email,
    userId,
    hasHydrated,
    logout: () => {
      clear();
      // Drop user-scoped caches so the next identity can't read the previous one's data.
      qc.clear();
      // Rotate the chat session id + wipe in-memory chat state so the next identity on a shared
      // device can't inherit this user's session (which would 403) or see their conversation.
      resetIdentityState();
      router.replace("/login");
    },
  };
}
