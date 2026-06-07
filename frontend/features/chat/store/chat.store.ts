import { create } from "zustand";
import { v4 as uuidv4 } from "uuid";
import { isApiError } from "@/lib/api/api-error";
import {
  FREE_TIER_EXHAUSTED,
  SseErrorSchema,
} from "@/features/chat/api/chat.schemas";
import { StreamError } from "@/lib/sse/stream-chat";
import type {
  Message,
  Step,
  Source,
  RichComponent,
  MessageStatus,
  RouteType,
  MessageStats,
} from "@/types";

interface ChatState {
  messages: Message[];
  draft: string;
  webSearchAllowed: boolean;
  isLoading: boolean;
  isStreaming: boolean;

  addMessage: (msg: Message) => void;
  /**
   * Optimistic two-message bootstrap shared by both chat strategies: push the user message
   * (status "done") then an empty assistant message (status "streaming") and return the
   * assistant id the caller streams/writes into. Single source for the optimistic turn so the
   * blocking and streaming hooks can't drift. Both messages are built via `createMessage`.
   */
  beginTurn: (content: string, webSearchAllowed: boolean) => string;
  appendContent: (id: string, chunk: string) => void;
  pushStep: (id: string, step: Step) => void;
  /** Sets a message's sources. Each Source may carry an optional `layer` (Phase 7 provenance). */
  setSources: (id: string, sources: Source[]) => void;
  /** Legacy: sets sourcesCount for the unmodified chat-message.tsx footer. Dropped in M3. */
  setSourcesCount: (id: string, count: number) => void;
  /**
   * Set/replace a message's Phase 7 observability stats (FE-4 renders it). Whole-object set; the
   * streaming hook accumulates stages locally and writes the snapshot here (and again on done).
   */
  setStats: (id: string, stats: MessageStats) => void;
  /** Append a backend P6 rich component. Dark in M1; rendered by M10. */
  addComponent: (id: string, component: RichComponent) => void;
  setStatus: (id: string, status: MessageStatus) => void;
  setRoute: (id: string, route: RouteType) => void;
  /** Record the backend error code (e.g. "free_tier_exhausted") on a failed turn. */
  setErrorCode: (id: string, code: string | undefined) => void;
  /** Flip status to "done" and optionally apply a partial patch (e.g. overwrite content with done.answer). */
  finalize: (id: string, patch?: Partial<Message>) => void;
  /** Returns the most-recent user message; used by the retry callback. */
  lastUserMessage: () => Message | undefined;

  setDraft: (draft: string) => void;
  setWebSearchAllowed: (v: boolean) => void;
  setLoading: (v: boolean) => void;
  setStreaming: (v: boolean) => void;
  reset: () => void;
}

export function createMessage(
  partial: {
    role: "user" | "assistant";
    content: string;
    id?: string;
    timestamp?: number;
  } & Partial<Omit<Message, "id" | "timestamp" | "role" | "content">>
): Message {
  const { id, timestamp, steps, sources, status, ...rest } = partial;
  return {
    id: id ?? uuidv4(),
    timestamp: timestamp ?? Date.now(),
    steps: steps ?? [],
    sources: sources ?? [],
    status: status ?? "pending",
    ...rest,
  };
}

/** Generic user-facing fallback shared by both chat strategies' error paths. */
const GENERIC_ERROR =
  "The AI service returned an error. Please try again later.";

/**
 * Single source for the error → user-facing turn recipe shared by both chat strategies.
 * Derives the message body + optional machine-readable `errorCode` from a failed request,
 * covering both delivery paths:
 *   - blocking: the free-tier guard arrives as an HTTP 4xx `ApiError` whose JSON body
 *     `{detail, code}` is stashed on `.payload`; `userMessage` is the body's detail.
 *   - streaming: a terminal `error` event / pre-stream 4xx surfaces as a `StreamError`
 *     whose `.code` distinguishes free-tier-exhausted (BYOK upsell copy) from a generic error.
 * Each hook keeps its own store-write calls; only this value-derivation is shared. Returns
 * undefined `errorCode` for a generic error so the BYOK CTA stays off.
 */
export function errorToTurn(err: unknown): {
  content: string;
  errorCode?: string;
} {
  if (isApiError(err)) {
    const parsed = SseErrorSchema.safeParse(err.payload);
    return {
      content: err.userMessage,
      errorCode: parsed.success ? parsed.data.code : undefined,
    };
  }
  if (err instanceof StreamError) {
    return {
      content:
        err.code === FREE_TIER_EXHAUSTED
          ? err.message ||
            "You've used up the free Gemini tier. Add your own API key to continue."
          : err.message || GENERIC_ERROR,
      errorCode: err.code,
    };
  }
  if (err instanceof Error) {
    return { content: err.message || GENERIC_ERROR };
  }
  return { content: GENERIC_ERROR };
}

const updateMessage = (
  messages: Message[],
  id: string,
  fn: (m: Message) => Message
): Message[] => messages.map((m) => (m.id === id ? fn(m) : m));

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  draft: "",
  webSearchAllowed: false,
  isLoading: false,
  isStreaming: false,

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  beginTurn: (content, webSearchAllowed) => {
    const { addMessage } = get();
    addMessage(
      createMessage({
        role: "user",
        content,
        status: "done",
        webSearchAllowed,
      })
    );
    const assistant = createMessage({
      role: "assistant",
      content: "",
      status: "streaming",
    });
    addMessage(assistant);
    return assistant.id;
  },

  appendContent: (id, chunk) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        content: m.content + chunk,
      })),
    })),

  pushStep: (id, step) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => {
        const existing = m.steps.findIndex((st) => st.label === step.label);
        const steps =
          existing >= 0
            ? m.steps.map((st, i) => (i === existing ? step : st))
            : [...m.steps, step];
        return { ...m, steps };
      }),
    })),

  setSources: (id, sources) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, sources })),
    })),

  setStats: (id, stats) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, stats })),
    })),

  setSourcesCount: (id, count) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        sourcesCount: count,
      })),
    })),

  addComponent: (id, component) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        components: [...(m.components ?? []), component],
      })),
    })),

  setStatus: (id, status) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, status })),
    })),

  setRoute: (id, route) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, route })),
    })),

  setErrorCode: (id, errorCode) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({ ...m, errorCode })),
    })),

  finalize: (id, patch) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        ...patch,
        status: "done" as MessageStatus,
      })),
    })),

  lastUserMessage: () =>
    [...get().messages].reverse().find((m) => m.role === "user"),

  setDraft: (draft) => set({ draft }),
  setWebSearchAllowed: (webSearchAllowed) => set({ webSearchAllowed }),
  setLoading: (isLoading) => set({ isLoading }),
  setStreaming: (isStreaming) => set({ isStreaming }),
  reset: () => set({ messages: [], isLoading: false, isStreaming: false }),
}));
