import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/features/chat/api/chat.api", () => ({
  sendMessage: vi.fn(),
  getSessionId: () => "s1", // success path invalidates the session-memory query
}));

import { sendMessage } from "@/features/chat/api/chat.api";
import { useBlockingChat } from "./use-blocking-chat";
import { useChatStore } from "@/features/chat/store/chat.store";

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: 0 } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("useBlockingChat", () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], isLoading: false });
    vi.clearAllMocks();
  });

  it("on success writes a unified assistant Message (synthesized step + sources)", async () => {
    (sendMessage as ReturnType<typeof vi.fn>).mockResolvedValue({
      answer: "The answer.",
      route: "RAG",
      context_count: 2,
      session_id: "s1",
    });

    const { result } = renderHook(() => useBlockingChat(), { wrapper });
    act(() => result.current.sendMessage("question", false));

    await waitFor(() => {
      const msgs = useChatStore.getState().messages;
      expect(msgs).toHaveLength(2);
      const assistant = msgs[1];
      expect(assistant.role).toBe("assistant");
      expect(assistant.content).toBe("The answer.");
      expect(assistant.route).toBe("RAG");
      expect(assistant.status).toBe("done");
      expect(assistant.sources).toHaveLength(2);
      expect(assistant.sourcesCount).toBe(2);
      expect(assistant.steps).toEqual([{ label: "done", state: "complete" }]);
    });
    expect(useChatStore.getState().isLoading).toBe(false);
  });

  it("on error writes an ERROR assistant bubble with the backend detail", async () => {
    (sendMessage as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Gemini 429")
    );
    const { result } = renderHook(() => useBlockingChat(), { wrapper });
    act(() => result.current.sendMessage("q", true));

    await waitFor(() => {
      const assistant = useChatStore.getState().messages[1];
      expect(assistant.route).toBe("ERROR");
      expect(assistant.status).toBe("error");
      expect(assistant.content).toBe("Gemini 429");
    });
  });
});
