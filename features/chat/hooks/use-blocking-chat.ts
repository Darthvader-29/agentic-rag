import { useMutation } from "@tanstack/react-query";
import { sendMessage } from "@/features/chat/api/chat.api";
import { useChatStore, createMessage } from "@/features/chat/store/chat.store";
import { isApiError } from "@/lib/api/api-error";
import type { ChatResponse } from "@/features/chat/api/chat.schemas";
import type { Source } from "@/types";

interface SendVars {
  text: string;
  webSearch: boolean;
}

interface Ctx {
  assistantId: string;
}

function synthSources(count: number): Source[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `synthetic-${i}`,
    title: `Source chunk ${i + 1}`,
    snippet: undefined,
    url: undefined,
  }));
}

export function useBlockingChat() {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendContent = useChatStore((s) => s.appendContent);
  const pushStep = useChatStore((s) => s.pushStep);
  const setSources = useChatStore((s) => s.setSources);
  const setRoute = useChatStore((s) => s.setRoute);
  const setSourcesCount = useChatStore((s) => s.setSourcesCount);
  const setStatus = useChatStore((s) => s.setStatus);
  const finalize = useChatStore((s) => s.finalize);
  const setLoading = useChatStore((s) => s.setLoading);

  const mutation = useMutation<ChatResponse, unknown, SendVars, Ctx>({
    mutationFn: ({ text, webSearch }) => sendMessage(text, webSearch),

    onMutate: ({ text }) => {
      addMessage(
        createMessage({ role: "user", content: text, status: "done" })
      );
      const assistant = createMessage({
        role: "assistant",
        content: "",
        status: "streaming",
      });
      addMessage(assistant);
      setLoading(true);
      return { assistantId: assistant.id };
    },

    onSuccess: (res, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      appendContent(assistantId, res.answer);
      setRoute(assistantId, res.route);
      setSources(assistantId, synthSources(res.context_count));
      setSourcesCount(assistantId, res.context_count);
      pushStep(assistantId, { label: "done", state: "complete" });
      finalize(assistantId);
    },

    onError: (err, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      const message = isApiError(err)
        ? err.userMessage
        : err instanceof Error
          ? err.message
          : "The AI service returned an error. Please try again later.";
      appendContent(assistantId, message);
      setRoute(assistantId, "ERROR");
      setStatus(assistantId, "error");
    },

    onSettled: () => setLoading(false),
  });

  return {
    sendMessage: (text: string, webSearch: boolean) =>
      mutation.mutate({ text, webSearch }),
    isPending: mutation.isPending,
    reset: mutation.reset,
  };
}
