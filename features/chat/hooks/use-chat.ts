import { useChatStore } from "@/features/chat/store/chat.store";
import { useBlockingChat } from "./use-blocking-chat";
import { cleanupSession } from "@/features/chat/api/chat.api";
import { flags } from "@/lib/flags";
import type { Message } from "@/types";

export interface UseChat {
  messages: Message[];
  isStreaming: boolean;
  sendMessage: (text: string, webSearch: boolean) => void;
  stop: () => void;
  retry: () => void;
}

export function useChat(): UseChat {
  const messages = useChatStore((s) => s.messages);

  // M2 will add: const streaming = useStreamingChat();
  const blocking = useBlockingChat();

  if (flags.streaming) {
    // M2: return streaming-backed implementation here.
    // Falls through to blocking in M1 because flags.streaming === false (dark).
  }

  return {
    messages,
    isStreaming: blocking.isPending,
    sendMessage: blocking.sendMessage,
    stop: () => {
      // M2: AbortController.abort(); no-op in blocking M1.
    },
    retry: blocking.reset,
  };
}

export async function resetSession(): Promise<void> {
  await cleanupSession();
  useChatStore.getState().reset();
}
