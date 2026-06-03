// features/memory/__tests__/use-session-memory.test.tsx
//
// Gating + data-shape tests for the memory query hook. Follows the repo convention (see
// features/keys/hooks/__tests__): mock the flags + the api module, drive the hook through a
// throwaway QueryClient. The REAL fetch/parse path is covered separately in memory.api.test.ts.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Toggle the memory flag per test via a mutable getter (matches free-tier-banner.test.tsx).
let memory = true;
vi.mock("@/lib/flags", () => ({
  get flags() {
    return { memory, auth: false, streaming: false };
  },
}));

// Mock the network layer; assert the hook calls fetch with the right session id.
const fetchMemory = vi.fn();
vi.mock("@/features/memory/api/memory.api", () => ({
  fetchSessionMemory: (id: string, signal?: AbortSignal) =>
    fetchMemory(id, signal),
}));

import { useSessionMemory } from "@/features/memory/hooks/use-session-memory";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

describe("useSessionMemory", () => {
  beforeEach(() => {
    memory = true;
    fetchMemory.mockReset();
  });

  it("fetches and exposes content + updatedAt when enabled", async () => {
    fetchMemory.mockResolvedValue({
      session_id: "s1",
      content: "remembered things",
      updated_at: "2026-06-03T10:00:00.000Z",
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSessionMemory("s1"), { wrapper });

    await waitFor(() =>
      expect(result.current.content).toBe("remembered things")
    );
    expect(result.current.updatedAt).toBe("2026-06-03T10:00:00.000Z");
    expect(result.current.enabled).toBe(true);
    expect(fetchMemory).toHaveBeenCalledTimes(1);
    expect(fetchMemory).toHaveBeenCalledWith("s1", expect.anything());
  });

  it("does NOT fetch when the flag is OFF (gate closed) and returns the empty baseline", () => {
    memory = false;
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSessionMemory("s1"), { wrapper });

    expect(result.current.enabled).toBe(false);
    expect(result.current.content).toBe("");
    expect(result.current.updatedAt).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(fetchMemory).not.toHaveBeenCalled();
  });

  it("does NOT fetch without a session id (empty string ⇒ gate closed)", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSessionMemory(""), { wrapper });

    expect(result.current.enabled).toBe(false);
    expect(result.current.content).toBe("");
    expect(fetchMemory).not.toHaveBeenCalled();
  });

  it("surfaces the empty value when the api resolves EMPTY_MEMORY (404 absorbed upstream)", async () => {
    fetchMemory.mockResolvedValue({ content: "", updated_at: null });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSessionMemory("s1"), { wrapper });

    await waitFor(() => expect(fetchMemory).toHaveBeenCalled());
    expect(result.current.content).toBe("");
    expect(result.current.updatedAt).toBeNull();
    expect(result.current.isError).toBe(false);
  });

  it("exposes isError on a thrown failure and a callable refetch", async () => {
    fetchMemory.mockRejectedValue(new Error("network"));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSessionMemory("s1"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(typeof result.current.refetch).toBe("function");

    // refetch re-invokes the query fn (now succeeding) and clears the error.
    fetchMemory.mockResolvedValue({ content: "back", updated_at: null });
    await result.current.refetch();
    await waitFor(() => expect(result.current.content).toBe("back"));
    expect(result.current.isError).toBe(false);
  });
});
