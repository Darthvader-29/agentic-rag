"use client";

import { useCallback } from "react";

import { flags } from "@/lib/flags";
import { useChatStore } from "@/features/chat/store/chat.store";
import { cleanupSession } from "@/features/chat/api/chat.api";
import { useBlockingChat } from "@/features/chat/hooks/use-blocking-chat";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";
import type { Message } from "@/types";

export interface UseChat {
  messages: Message[];
  isStreaming: boolean;
  sendMessage: (text: string, webSearch: boolean) => Promise<void> | void;
  stop: () => void;
  retry: () => void;
}

export function useChat(): UseChat {
  // Both hooks are called every render (Rules of Hooks); we SELECT one strategy.
  // Each is cheap and side-effect-free until its sendMessage is invoked.
  const blocking = useBlockingChat();
  const streaming = useStreamingChat();

  const strategy = flags.streaming ? streaming : blocking;

  const messages = useChatStore((s) => s.messages);
  const lastUserMessage = useChatStore((s) => s.lastUserMessage);
  const webSearchAllowed = useChatStore((s) => s.webSearchAllowed);

  const retry = useCallback(() => {
    const last = lastUserMessage();
    if (last)
      strategy.sendMessage(
        last.content,
        last.webSearchAllowed ?? webSearchAllowed
      );
  }, [strategy, lastUserMessage, webSearchAllowed]);

  return {
    messages,
    isStreaming: strategy.isStreaming,
    sendMessage: strategy.sendMessage,
    stop: strategy.stop,
    retry,
  };
}

/** Reset flow used by the sidebar (parity with page.tsx handleClearSession). */
export async function resetSession(): Promise<void> {
  await cleanupSession();
  useChatStore.getState().reset();
}
