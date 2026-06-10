"use client";

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useAuthStore } from "@/features/auth/store/auth.store";
import type { TokenPair } from "@/features/auth/api/auth.schemas";
import { isApiError } from "@/lib/api/api-error";
import { resetIdentityState } from "@/features/auth/lib/reset-identity";

/**
 * Shared factory behind useLogin / useRegister / useUpgrade. All three are the same
 * TanStack mutation: call an authApi method that returns a TokenPair, then on success
 * persist tokens (never a guest), persist the typed email, run a cache op, toast, and
 * redirect; on error, map a single status code to a specific message else a generic one.
 *
 * The differences are parametrized below. Search-param-dependent redirects (login's
 * `?next`) are kept in the wrapper and passed in via `redirectTo`, so this factory never
 * needs to read `useSearchParams`.
 */
export interface AuthMutationConfig<TVars extends { email: string }> {
  /** The authApi call (login / register / upgrade). */
  mutationFn: (body: TVars) => Promise<TokenPair>;
  /** Success toast copy. */
  successToast: string;
  /** Where to send the user after success (login resolves `?next` in its wrapper). */
  redirectTo: string;
  /** Cache strategy: "clear" wipes prior (guest) caches; "invalidate" refreshes them. */
  cache: "clear" | "invalidate";
  /** HTTP status that maps to `conflictMessage`; otherwise `genericMessage` is used. */
  errorStatus: number;
  /** Message shown when the error is an ApiError with `errorStatus`. */
  errorStatusMessage: string;
  /** Fallback message for any other error. */
  genericMessage: string;
}

export function useAuthMutation<TVars extends { email: string }>(
  config: AuthMutationConfig<TVars>
): UseMutationResult<TokenPair, Error, TVars> {
  const router = useRouter();
  const qc = useQueryClient();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setEmail = useAuthStore((s) => s.setEmail);

  return useMutation({
    mutationFn: (body: TVars) => config.mutationFn(body),
    onSuccess: (tokens, vars) => {
      setTokens(tokens, { isGuest: false });
      setEmail(vars.email);
      if (config.cache === "clear") {
        // login / register switch to a DIFFERENT user_id (the prior caches + chat session belong
        // to another identity). Wipe caches AND rotate the chat session, else the stale
        // rag_session_id 403s every chat for the new identity. "invalidate" (upgrade) keeps the
        // same user_id, so its session/conversation stay valid and must NOT be reset.
        qc.clear();
        resetIdentityState();
      } else {
        qc.invalidateQueries();
      }
      toast.success(config.successToast);
      router.replace(config.redirectTo);
    },
    onError: (err) => {
      const msg =
        isApiError(err) && err.status === config.errorStatus
          ? config.errorStatusMessage
          : config.genericMessage;
      toast.error(msg);
    },
  });
}
