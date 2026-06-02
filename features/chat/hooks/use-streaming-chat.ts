"use client";

import { useCallback, useRef } from "react";
import { v4 as uuidv4 } from "uuid";

import { useChatStore } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { streamChat, StreamError } from "@/lib/sse/stream-chat";
import { getChatModelSelection } from "@/features/keys/store/provider.store";
import { FREE_TIER_EXHAUSTED } from "@/features/chat/api/chat.schemas";
import type { SseRoute } from "@/features/chat/api/chat.schemas";
import type { RouteType } from "@/types";

/**
 * Map the backend `done.route` to the frontend RouteType union so a streamed Message
 * is shape-identical to a blocking one. Authoritative form is the FLAT enum from 09
 * Appendix A (`RAG | WEB | BOTH | DIRECT`): `BOTH`→"WEB+RAG"; `RAG`/`WEB`/`DIRECT`
 * pass through. The legacy 07 `{destination, relevant}` object is tolerated defensively
 * (reconciled in M9).
 */
function mapRoute(route: SseRoute | null): RouteType {
  if (!route) return "DIRECT";
  // Legacy object form (07): map by destination.
  if (typeof route === "object") {
    return route.destination === "web_search" ? "WEB" : "RAG";
  }
  // Flat enum (09, authoritative).
  if (route === "BOTH") return "WEB+RAG";
  return route; // "RAG" | "WEB" | "DIRECT" are valid RouteType members
}

export function useStreamingChat() {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendContent = useChatStore((s) => s.appendContent);
  const pushStep = useChatStore((s) => s.pushStep);
  const addComponent = useChatStore((s) => s.addComponent);
  const setRoute = useChatStore((s) => s.setRoute);
  const finalize = useChatStore((s) => s.finalize);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const isStreaming = useChatStore((s) => s.isStreaming);

  // One in-flight stream at a time; the controller powers the Stop button.
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (text: string, webSearchAllowed: boolean) => {
      // 1) user message
      addMessage({
        id: uuidv4(),
        role: "user",
        content: text,
        status: "done",
        steps: [],
        sources: [],
        timestamp: Date.now(),
        webSearchAllowed,
      });

      // 2) empty assistant message we stream INTO
      const assistantId = uuidv4();
      addMessage({
        id: assistantId,
        role: "assistant",
        content: "",
        steps: [],
        sources: [],
        status: "streaming",
        timestamp: Date.now(),
      });

      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);

      await streamChat(
        {
          message: text,
          session_id: getSessionId(),
          web_search_allowed: webSearchAllowed,
          // M7: optional per-conversation provider/model. Spreads to nothing when no
          // provider is selected ⇒ the backend default (free Gemini tier).
          ...getChatModelSelection(),
        },
        {
          signal: controller.signal,
          onStatus: (stage) => {
            // status stage → a live thinking step (feeds ThinkingSteps panel)
            pushStep(assistantId, { label: stage, state: "active" });
          },
          onToken: (chunk) => {
            // token chunk → append to the streaming body (+ M4 caret rides this)
            appendContent(assistantId, chunk);
          },
          onComponent: (component) => {
            // whole rich block → append to message.components.
            // Dark in M2 (no renderer yet); rendered by M10.
            addComponent(assistantId, component);
          },
          onDone: ({ answer, route }) => {
            // Canonical final body is done.answer (== concatenated tokens).
            setRoute(assistantId, mapRoute(route));
            finalize(assistantId, { content: answer });
          },
          onError: (error) => {
            pushStep(assistantId, { label: "error", state: "error" });
            setRoute(assistantId, "ERROR");
            // Branch on the machine-readable CODE (docs/09 §3), not the HTTP status. A
            // free-tier-exhausted code (from either delivery path) is captured on the
            // message so M7's BYOK "add your own key" CTA can key off `errorCode`.
            const code = error instanceof StreamError ? error.code : undefined;
            finalize(assistantId, {
              errorCode: code,
              content:
                code === FREE_TIER_EXHAUSTED
                  ? error.message ||
                    "You've used up the free Gemini tier. Add your own API key to continue."
                  : error.message ||
                    "The AI service returned an error. Please try again later.",
            });
          },
        }
      );

      abortRef.current = null;
      setStreaming(false);
    },
    [
      addMessage,
      appendContent,
      pushStep,
      addComponent,
      setRoute,
      finalize,
      setStreaming,
    ]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort(); // AbortError → streamChat resolves cleanly
    abortRef.current = null;
    setStreaming(false);
  }, [setStreaming]);

  return { sendMessage, stop, isStreaming };
}
