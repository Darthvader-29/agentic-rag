import type { z } from "zod";
import { SseLayerEnum } from "@/features/chat/api/chat.schemas";

// Re-exported (not re-inferred) from the chat schemas so these can't drift from their source.
export type {
  RouteType,
  ChatRequest,
  ChatResponse,
} from "@/features/chat/api/chat.schemas";
import type { RouteType } from "@/features/chat/api/chat.schemas";

export type MessageStatus = "pending" | "streaming" | "done" | "error";

/**
 * Which retrieval layer a source/citation came from (Phase 7 multi-layer retrieval). Carried
 * optionally on citation items and done-event sources; absent ⇒ render no badge (legacy-safe).
 * Derived from the chat schemas' `SseLayerEnum` (single source) so it can't drift.
 */
export type RetrievalLayer = z.infer<typeof SseLayerEnum>;

/**
 * Per-turn observability stats (Phase 7). Populated by use-streaming-chat when
 * `flags.observability` is on; rendered by the assistant stats panel (FE-4). The timing fields
 * are `performance.now()` milliseconds (monotonic, relative to navigation), NOT wall-clock.
 *   startedAtMs  request start mark
 *   stages       one entry per SSE status stage, with its arrival offset
 *   totalMs      filled on `done` (now - startedAtMs)
 *   route        the resolved RouteType (from done.route), for at-a-glance display
 *   tokens       backend-reported token usage when present, else null (never undefined on done)
 *   traceId      the trace-id segment of the W3C traceparent we sent, for cross-system lookup
 */
export interface MessageStats {
  startedAtMs: number;
  stages: { stage: string; atMs: number }[];
  totalMs?: number;
  route?: RouteType;
  tokens?: { input?: number; output?: number } | null;
  traceId?: string | null;
}

export interface Step {
  label: string;
  state: "active" | "complete" | "error";
  detail?: string;
}

export interface Source {
  id: string;
  title: string;
  snippet?: string;
  url?: string;
  /** Phase 7: which retrieval layer produced this source. Absent ⇒ no provenance badge. */
  layer?: RetrievalLayer;
}

/**
 * Opaque forward-compat carrier for the backend Phase-6 `component` SSE event.
 * Refined into a validated discriminated union by M10; nothing in M1/M2 reads a typed shape.
 *
 * NOTE: intentionally NOT aliased to chat.schemas' `SseComponent` — narrowing `.type` to the
 * catalog enum would reject deliberately-invalid `type` literals that consumers construct (e.g.
 * the rich-component drop-unknown test builds `{ type: "widget" }`), breaking compilation.
 */
export interface RichComponent {
  type: string;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** epoch milliseconds — serializable */
  timestamp: number;
  status: MessageStatus;
  steps: Step[];
  sources: Source[];
  route?: RouteType;
  /** Backend P6 `component` event payloads; empty on the blocking path; rendered by M10. */
  components?: RichComponent[];
  /** Legacy alias kept so the unmodified chat-message.tsx renders the chunk count in M1. */
  sourcesCount?: number;
  /** Stored on user messages so the retry function can re-send with the same web-search setting. */
  webSearchAllowed?: boolean;
  /**
   * Machine-readable backend error code (docs/09 §3), e.g. "free_tier_exhausted", captured
   * when a turn fails with a typed code. Set by the streaming strategy on the error path
   * (and surfaced on the blocking path) so M7's BYOK upsell CTA can key off it
   * (`errorCode === "free_tier_exhausted"`). Undefined on a generic error or a success.
   */
  errorCode?: string;
  /**
   * Phase 7 per-turn observability stats. Populated on assistant messages by use-streaming-chat
   * when `flags.observability` is on (undefined otherwise / on the blocking path); rendered by
   * the lazy stats panel (FE-4).
   */
  stats?: MessageStats;
}
