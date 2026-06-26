import { describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Replay a scripted SSE sequence by invoking the handlers streamChat would call.
// Scripts the 09 contract: a whole `component` block + a FLAT-enum done.route.
vi.mock("@/lib/sse/stream-chat", () => ({
  streamChat: vi.fn(async (_payload: unknown, h: StreamChatHandlers) => {
    h.onStatus?.("routing");
    h.onStatus?.("retrieving");
    h.onStatus?.("synthesizing");
    h.onToken?.("Grounded ");
    h.onToken?.("answer.");
    h.onComponent?.({
      type: "citation",
      items: [{ label: "doc.pdf · p.4" }],
    });
    h.onDone?.({ answer: "Grounded answer.", route: "BOTH" }); // flat enum (09)
  }),
}));

import { useChatStore } from "@/features/chat/store/chat.store";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";
import { streamChat, type StreamChatHandlers } from "@/lib/sse/stream-chat";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

describe("useStreamingChat end-to-end", () => {
  it("ends with a finalized assistant Message of the canonical shape", async () => {
    useChatStore.setState({ messages: [], isStreaming: false });
    const { result } = renderHook(() => useStreamingChat(), { wrapper });

    await act(async () => {
      await result.current.sendMessage("what is X?", false);
    });

    const msgs = useChatStore.getState().messages;
    const assistant = msgs.find((m) => m.role === "assistant")!;
    expect(assistant.content).toBe("Grounded answer.");
    expect(assistant.route).toBe("WEB+RAG"); // mapRoute("BOTH") → "WEB+RAG"
    expect(assistant.status).toBe("done");
    expect(assistant.steps.map((s) => s.label)).toEqual([
      "routing",
      "retrieving",
      "synthesizing",
    ]);
    // B22: after a completed stream NO step is still "active" — the "Thinking…" spinner stops.
    expect(assistant.steps.every((s) => s.state !== "active")).toBe(true);
    // Sources are DERIVED from the citation component (09 §5: citation = sources channel).
    expect(assistant.sources).toHaveLength(1);
    expect(assistant.sources[0].title).toBe("doc.pdf · p.4");
    // Opaque component ALSO captured for M10 rendering (storage is M9's job).
    expect(assistant.components).toHaveLength(1);
    expect(assistant.components![0].type).toBe("citation");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("keeps streamed content when done.answer is empty (B26)", async () => {
    useChatStore.setState({ messages: [], isStreaming: false });
    vi.mocked(streamChat).mockImplementationOnce(
      async (_payload: unknown, h: StreamChatHandlers) => {
        h.onToken?.("Hello");
        h.onToken?.(" world");
        h.onDone?.({ answer: "", route: "RAG" }); // empty done.answer must not wipe content
      }
    );

    const { result } = renderHook(() => useStreamingChat(), { wrapper });
    await act(async () => {
      await result.current.sendMessage("q", false);
    });

    const assistant = useChatStore
      .getState()
      .messages.find((m) => m.role === "assistant")!;
    expect(assistant.content).toBe("Hello world"); // streamed accumulation preserved
    expect(assistant.status).toBe("done");
  });

  it("a single done.layer fills the citation source's provenance layer (B07)", async () => {
    useChatStore.setState({ messages: [], isStreaming: false });
    vi.mocked(streamChat).mockImplementationOnce(
      async (_payload: unknown, h: StreamChatHandlers) => {
        h.onComponent?.({
          type: "citation",
          items: [{ label: "doc.pdf · p.4" }],
        });
        h.onDone?.({ answer: "A.", route: "RAG", layers: ["vector"] });
      }
    );

    const { result } = renderHook(() => useStreamingChat(), { wrapper });
    await act(async () => {
      await result.current.sendMessage("q", false);
    });

    const assistant = useChatStore
      .getState()
      .messages.find((m) => m.role === "assistant")!;
    expect(assistant.sources).toHaveLength(1);
    expect(assistant.sources[0].layer).toBe("vector"); // single contributing layer → attributed
  });

  it("multiple done.layers do NOT guess a per-source layer (B07)", async () => {
    useChatStore.setState({ messages: [], isStreaming: false });
    vi.mocked(streamChat).mockImplementationOnce(
      async (_payload: unknown, h: StreamChatHandlers) => {
        h.onComponent?.({
          type: "citation",
          items: [{ label: "doc.pdf · p.4" }],
        });
        h.onDone?.({ answer: "A.", route: "BOTH", layers: ["vector", "web"] });
      }
    );

    const { result } = renderHook(() => useStreamingChat(), { wrapper });
    await act(async () => {
      await result.current.sendMessage("q", false);
    });

    const assistant = useChatStore
      .getState()
      .messages.find((m) => m.role === "assistant")!;
    expect(assistant.sources).toHaveLength(1);
    expect(assistant.sources[0].layer).toBeUndefined(); // ambiguous set → left to citations
  });
});
