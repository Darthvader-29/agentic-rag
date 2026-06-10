import { useMutation, useQueryClient } from "@tanstack/react-query";
import { sendMessage, getSessionId } from "@/features/chat/api/chat.api";
import { useChatStore, errorToTurn } from "@/features/chat/store/chat.store";
import { invalidateSessionMemory } from "@/features/memory/hooks/use-session-memory";
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
  const beginTurn = useChatStore((s) => s.beginTurn);
  const appendContent = useChatStore((s) => s.appendContent);
  const addComponent = useChatStore((s) => s.addComponent);
  const pushStep = useChatStore((s) => s.pushStep);
  const setSources = useChatStore((s) => s.setSources);
  const setRoute = useChatStore((s) => s.setRoute);
  const setSourcesCount = useChatStore((s) => s.setSourcesCount);
  const setStatus = useChatStore((s) => s.setStatus);
  const setErrorCode = useChatStore((s) => s.setErrorCode);
  const finalize = useChatStore((s) => s.finalize);
  const setLoading = useChatStore((s) => s.setLoading);
  const queryClient = useQueryClient();

  const mutation = useMutation<ChatResponse, unknown, SendVars, Ctx>({
    mutationFn: ({ text, webSearch }) => sendMessage(text, webSearch),

    onMutate: ({ text, webSearch }) => {
      const assistantId = beginTurn(text, webSearch);
      setLoading(true);
      return { assistantId };
    },

    onSuccess: (res, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      appendContent(assistantId, res.answer);
      // Store every rich block the backend parsed (table/chart/citation/code/callout/media) so the
      // blocking path renders the same components the streaming path does (M10).
      (res.components ?? []).forEach((c) => addComponent(assistantId, c));
      setRoute(assistantId, res.route);
      setSources(assistantId, synthSources(res.context_count));
      setSourcesCount(assistantId, res.context_count);
      pushStep(assistantId, { label: "done", state: "complete" });
      finalize(assistantId);
      // A finalized turn means the backend rewrote this session's memory — refresh the panel.
      invalidateSessionMemory(queryClient, getSessionId());
    },

    onError: (err, _vars, ctx) => {
      if (!ctx) return;
      const { assistantId } = ctx;
      // Shared error→turn recipe; this hook keeps its own store-write calls.
      const { content, errorCode } = errorToTurn(err);
      appendContent(assistantId, content);
      setRoute(assistantId, "ERROR");
      // Capture the machine-readable code (free_tier_exhausted etc.) so the BYOK CTA fires.
      setErrorCode(assistantId, errorCode);
      setStatus(assistantId, "error");
    },

    onSettled: () => setLoading(false),
  });

  return {
    sendMessage: (text: string, webSearch: boolean) =>
      mutation.mutate({ text, webSearch }),
    isStreaming: mutation.isPending,
    stop: () => {},
    isPending: mutation.isPending,
    reset: mutation.reset,
  };
}
