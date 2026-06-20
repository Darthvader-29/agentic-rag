import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { useAuthMutation } from "@/features/auth/hooks/use-auth-mutation";
import { useChatStore, createMessage } from "@/features/chat/store/chat.store";

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: 0 } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

const TOKENS = {
  access_token: "new-access",
  refresh_token: "new-refresh",
  token_type: "bearer",
} as const;

function configFor(cache: "clear" | "invalidate") {
  return {
    mutationFn: async () => TOKENS,
    successToast: "ok",
    redirectTo: "/",
    cache,
    errorStatus: 401,
    errorStatusMessage: "x",
    genericMessage: "y",
  };
}

const SESSION_KEY = "rag_session_id";

describe("useAuthMutation identity reset (B02)", () => {
  beforeEach(() => {
    localStorage.clear();
    useChatStore.getState().reset();
  });

  it("rotates rag_session_id and wipes chat state on login/register (cache:clear)", async () => {
    // Simulate a prior identity's session + on-screen conversation.
    localStorage.setItem(SESSION_KEY, "previous-identity-session");
    useChatStore.getState().addMessage(
      createMessage({ role: "user", content: "previous user's message" })
    );

    const { result } = renderHook(() => useAuthMutation(configFor("clear")), {
      wrapper,
    });
    await act(async () => {
      await result.current.mutateAsync({ email: "newuser@example.com" });
    });

    await waitFor(() => {
      // the stale session id is dropped → next getSessionId() mints a fresh, owned one
      expect(localStorage.getItem(SESSION_KEY)).toBeNull();
      // the previous identity's messages no longer linger
      expect(useChatStore.getState().messages).toHaveLength(0);
    });
  });

  it("preserves rag_session_id and chat state on guest upgrade (cache:invalidate)", async () => {
    // Upgrade keeps the same user_id, so the session + conversation stay valid.
    localStorage.setItem(SESSION_KEY, "same-user-session");
    useChatStore.getState().addMessage(
      createMessage({ role: "user", content: "in-progress message" })
    );

    const { result } = renderHook(
      () => useAuthMutation(configFor("invalidate")),
      { wrapper }
    );
    await act(async () => {
      await result.current.mutateAsync({ email: "guest@example.com" });
    });

    // give any onSuccess side effects a tick, then assert nothing was reset
    await waitFor(() => {
      expect(localStorage.getItem(SESSION_KEY)).toBe("same-user-session");
    });
    expect(useChatStore.getState().messages).toHaveLength(1);
  });
});
