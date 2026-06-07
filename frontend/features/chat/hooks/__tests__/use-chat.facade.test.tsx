import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const blockingSend = vi.fn();
const streamingSend = vi.fn();

vi.mock("@/features/chat/hooks/use-blocking-chat", () => ({
  useBlockingChat: () => ({
    sendMessage: blockingSend,
    stop: vi.fn(),
    isStreaming: false,
    isPending: false,
    reset: vi.fn(),
  }),
}));
vi.mock("@/features/chat/hooks/use-streaming-chat", () => ({
  useStreamingChat: () => ({
    sendMessage: streamingSend,
    stop: vi.fn(),
    isStreaming: false,
  }),
}));
vi.mock("@/lib/flags", () => ({ flags: { streaming: false } }));

import { flags } from "@/lib/flags";
import { useChat } from "@/features/chat/hooks/use-chat";

describe("useChat facade strategy switch", () => {
  beforeEach(() => vi.clearAllMocks());

  it("delegates to the blocking strategy when flags.streaming is false", () => {
    (flags as { streaming: boolean }).streaming = false;
    const { result } = renderHook(() => useChat());
    result.current.sendMessage("hi", false);
    expect(blockingSend).toHaveBeenCalledWith("hi", false);
    expect(streamingSend).not.toHaveBeenCalled();
  });

  it("delegates to the streaming strategy when flags.streaming is true", () => {
    (flags as { streaming: boolean }).streaming = true;
    const { result } = renderHook(() => useChat());
    result.current.sendMessage("hi", true);
    expect(streamingSend).toHaveBeenCalledWith("hi", true);
    expect(blockingSend).not.toHaveBeenCalled();
  });
});
