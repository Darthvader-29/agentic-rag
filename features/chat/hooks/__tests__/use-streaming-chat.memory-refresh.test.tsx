import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Memory feature ON for this file so the invalidation wiring is live.
vi.mock("@/lib/flags", () => ({
  flags: {
    streaming: true,
    auth: false,
    byok: false,
    presignedUpload: false,
    richComponents: true,
    memory: true,
    knowledgeGraph: false,
    observability: false,
  },
}));

// Fixed session id so we can assert the exact invalidated key.
vi.mock("@/features/chat/api/chat.api", () => ({
  getSessionId: () => "sess-xyz",
}));

let script: (h: Record<string, (...a: unknown[]) => void>) => void = () => {};
vi.mock("@/lib/sse/stream-chat", async () => {
  const actual = await vi.importActual<typeof import("@/lib/sse/stream-chat")>(
    "@/lib/sse/stream-chat"
  );
  return {
    ...actual,
    streamChat: vi.fn(
      async (_p: unknown, h: Record<string, (...a: unknown[]) => void>) =>
        script(h)
    ),
  };
});

import { useChatStore } from "@/features/chat/store/chat.store";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";
import { sessionMemoryQueryKey } from "@/features/memory/hooks/use-session-memory";

function setup() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useStreamingChat(), { wrapper });
  return { result, spy };
}

beforeEach(() => {
  useChatStore.setState({ messages: [], isStreaming: false });
});

describe("useStreamingChat — memory refresh on turn finalize (Bug 3)", () => {
  it("invalidates the session-memory query after a successful done", async () => {
    script = (h) => {
      h.onToken?.("answer");
      h.onDone?.({ answer: "answer", route: "DIRECT" });
    };
    const { result, spy } = setup();
    await act(async () => {
      await result.current.sendMessage("q", false);
    });
    expect(spy).toHaveBeenCalledWith({
      queryKey: sessionMemoryQueryKey("sess-xyz"),
    });
  });

  it("does NOT refresh memory when the turn errors", async () => {
    script = (h) => h.onError?.(new Error("boom"));
    const { result, spy } = setup();
    await act(async () => {
      await result.current.sendMessage("q", false);
    });
    expect(spy).not.toHaveBeenCalled();
  });
});
