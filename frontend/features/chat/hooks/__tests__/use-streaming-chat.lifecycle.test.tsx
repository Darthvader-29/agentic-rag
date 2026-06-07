import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// A controllable streamChat: every call registers its handlers + a manual `resolve`, and resolves
// itself when its AbortSignal fires — so a test can drive Stop / abort-previous / unmount and a
// stream that ends WITHOUT a terminal event (the dropped-malformed-`done` case from lib/sse).
const { calls } = vi.hoisted(() => ({
  calls: [] as Array<{
    handlers: Record<string, (...a: unknown[]) => void>;
    resolve: () => void;
    aborted: boolean;
  }>,
}));

vi.mock("@/lib/sse/stream-chat", async () => {
  const actual = await vi.importActual<typeof import("@/lib/sse/stream-chat")>(
    "@/lib/sse/stream-chat"
  );
  return {
    ...actual, // keep the real StreamError
    streamChat: vi.fn(
      (
        _payload: unknown,
        handlers: Record<string, (...a: unknown[]) => void> & {
          signal?: AbortSignal;
        }
      ) =>
        new Promise<void>((resolve) => {
          const call = { handlers, resolve, aborted: false };
          handlers.signal?.addEventListener("abort", () => {
            call.aborted = true;
            resolve();
          });
          calls.push(call);
        })
    ),
  };
});

import { useChatStore } from "@/features/chat/store/chat.store";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

const assistants = () =>
  useChatStore.getState().messages.filter((m) => m.role === "assistant");

beforeEach(() => {
  calls.length = 0;
  useChatStore.setState({ messages: [], isStreaming: false });
});

describe("useStreamingChat — lifecycle safety net (Bug 1 + Bug 2)", () => {
  it("finalizes the message when the stream ends with no done/error event (dropped malformed done)", async () => {
    const { result } = renderHook(() => useStreamingChat(), { wrapper });

    let p!: Promise<void>;
    act(() => {
      p = result.current.sendMessage("q", false) as Promise<void>;
    });
    // Tokens stream, then the body closes with NO done/error — exactly what the hook sees when
    // lib/sse drops a malformed `done`. Previously this left the message stuck on "streaming".
    act(() => {
      calls[0].handlers.onToken?.("partial answer");
      calls[0].resolve();
    });
    await act(async () => {
      await p;
    });

    const a = assistants()[0];
    expect(a.status).toBe("done"); // finalized, not a forever-spinner
    expect(a.content).toBe("partial answer"); // keeps whatever streamed
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("aborts the previous in-flight stream on a second send and finalizes the abandoned turn", async () => {
    const { result } = renderHook(() => useStreamingChat(), { wrapper });

    let p1!: Promise<void>;
    act(() => {
      p1 = result.current.sendMessage("q1", false) as Promise<void>;
    });
    act(() => {
      calls[0].handlers.onToken?.("first partial");
    });

    // Second send while the first is still streaming (the composer isn't disabled mid-stream).
    let p2!: Promise<void>;
    act(() => {
      p2 = result.current.sendMessage("q2", false) as Promise<void>;
    });

    await act(async () => {
      await p1; // resolves because the new send aborted controller #1
    });

    expect(calls[0].aborted).toBe(true); // previous stream aborted, not orphaned
    const [first, second] = assistants();
    expect(first.status).toBe("done"); // abandoned turn finalized with its partial content
    expect(first.content).toBe("first partial");
    expect(second.status).toBe("streaming"); // newer turn still live
    // #1's post-stream cleanup must NOT clobber the newer stream's shared state.
    expect(useChatStore.getState().isStreaming).toBe(true);

    // Finish the second turn cleanly.
    act(() => {
      calls[1].handlers.onDone?.({ answer: "second answer", route: "DIRECT" });
      calls[1].resolve();
    });
    await act(async () => {
      await p2;
    });
    expect(assistants()[1].status).toBe("done");
    expect(assistants()[1].content).toBe("second answer");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("aborts the in-flight stream when the hook unmounts", async () => {
    const { result, unmount } = renderHook(() => useStreamingChat(), {
      wrapper,
    });
    act(() => {
      void result.current.sendMessage("q", false);
    });
    expect(calls[0].aborted).toBe(false);

    unmount();
    expect(calls[0].aborted).toBe(true); // unmount cleanup aborted the dangling fetch

    // Flush the resolved stream promise's continuation (harmless store writes post-unmount).
    await act(async () => {});
  });
});
