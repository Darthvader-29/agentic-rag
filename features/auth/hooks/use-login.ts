"use client";

import { useSearchParams } from "next/navigation";
import { authApi } from "@/features/auth/api/auth.api";
import type { LoginRequest } from "@/features/auth/api/auth.schemas";
import { useAuthMutation } from "@/features/auth/hooks/use-auth-mutation";

/** Logs an existing user in, persists the TokenPair, and redirects to `next` or `/`. */
export function useLogin() {
  const params = useSearchParams();
  return useAuthMutation<LoginRequest>({
    mutationFn: (body) => authApi.login(body),
    successToast: "Signed in",
    redirectTo: params.get("next") ?? "/", // login honors ?next; others go home
    cache: "clear", // drop any prior (guest) session caches
    errorStatus: 401,
    errorStatusMessage: "Invalid email or password.",
    genericMessage: "Sign-in failed. Please try again.",
  });
}
