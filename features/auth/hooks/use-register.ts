"use client";

import { authApi } from "@/features/auth/api/auth.api";
import type { RegisterRequest } from "@/features/auth/api/auth.schemas";
import { useAuthMutation } from "@/features/auth/hooks/use-auth-mutation";

/**
 * Registers a brand-new account. Phase 6's /api/auth/register returns a TokenPair
 * directly (no separate login round-trip needed), which we persist and then redirect home.
 *
 * NOTE: this is "register from scratch" (no prior token). To convert an existing GUEST to
 * a registered account while preserving its user_id, use `useUpgrade` instead.
 */
export function useRegister() {
  return useAuthMutation<RegisterRequest>({
    mutationFn: (body) => authApi.register(body),
    successToast: "Account created",
    redirectTo: "/",
    cache: "clear",
    errorStatus: 409,
    errorStatusMessage: "That email or username is already taken.",
    genericMessage: "Registration failed. Please try again.",
  });
}
