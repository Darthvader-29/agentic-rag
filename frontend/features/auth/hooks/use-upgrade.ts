"use client";

import { authApi } from "@/features/auth/api/auth.api";
import type { UpgradeRequest } from "@/features/auth/api/auth.schemas";
import { useAuthMutation } from "@/features/auth/hooks/use-auth-mutation";

/**
 * Upgrades the CURRENT guest to a registered account, preserving the same user_id.
 *
 * The request is sent authenticated as the guest (authApi.upgrade uses auth:true, so the
 * interceptor attaches the guest's Bearer). The backend keeps the user_id and returns a
 * fresh TokenPair for the now-registered user, which replaces the guest tokens in place.
 * We do NOT clear the Query cache here — the user_id (and thus their sessions/keys) carry
 * over, so caches remain valid; we invalidate instead so newly unlocked queries refetch.
 */
export function useUpgrade() {
  return useAuthMutation<UpgradeRequest>({
    mutationFn: (body) => authApi.upgrade(body),
    successToast: "Account created — your work is saved",
    redirectTo: "/",
    cache: "invalidate",
    errorStatus: 409,
    errorStatusMessage: "That email or username is already taken.",
    genericMessage: "Could not create your account. Please try again.",
  });
}
