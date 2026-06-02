// lib/sse/stream-chat.ts
import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { authStore } from "@/features/auth/store/auth.store";
import { parseSSE } from "@/lib/sse/parser";
import {
  SseStatusSchema,
  SseTokenSchema,
  SseDoneSchema,
  SseErrorSchema,
  SseComponentSchema,
  type SseRoute,
  type SseComponent,
} from "@/features/chat/api/chat.schemas";

/** The POST body for /api/chat — identical to the blocking ChatRequest. */
export interface StreamChatPayload {
  message: string;
  session_id: string;
  web_search_allowed: boolean;
}

export interface StreamChatHandlers {
  /** A status stage arrived (routing | retrieving | searching web | synthesizing). */
  onStatus?: (stage: string) => void;
  /** A token chunk arrived; `text` is the raw chunk to append to the body. */
  onToken?: (text: string) => void;
  /** A whole rich-output component block arrived (09 `component` event). Dark until M10. */
  onComponent?: (component: SseComponent) => void;
  /** The stream completed with the final answer + backend route (flat enum, legacy tolerated). */
  onDone?: (result: { answer: string; route: SseRoute | null }) => void;
  /** A typed `error` event OR a transport failure occurred. */
  onError?: (error: Error) => void;
  /** AbortController.signal that powers the Stop button. */
  signal?: AbortSignal;
}

/**
 * Stream a chat turn over SSE. Resolves when the stream completes, errors, or is
 * aborted. Never throws for an aborted stream (clean Stop). All other failures
 * are reported via onError and then the promise resolves (the hook owns UI state).
 */
export async function streamChat(
  payload: StreamChatPayload,
  handlers: StreamChatHandlers
): Promise<void> {
  const { onStatus, onToken, onComponent, onDone, onError, signal } = handlers;

  // M6: attach Bearer for the streaming POST too (it bypasses http-client and does its own
  // fetch). Flag-gated — with auth OFF this header is never set (byte-for-byte today). The
  // SSE path can't run the 401→refresh→retry dance (no re-fetch of an open stream), so a
  // mid-stream 401 surfaces via onError; M9 owns any richer streaming-refresh handling.
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (flags.auth) {
    const token = authStore.getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${env.NEXT_PUBLIC_API_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (isAbortError(err)) return; // aborted before headers — clean stop
    onError?.(toError(err));
    return;
  }

  if (!res.ok) {
    // Non-stream HTTP error (auth/rate-limit raised BEFORE the stream opened).
    let detail = `Backend error: ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body */
    }
    onError?.(new Error(detail));
    return;
  }

  if (!res.body) {
    onError?.(new Error("Streaming response had no body."));
    return;
  }

  try {
    for await (const { event, data } of parseSSE(res.body)) {
      switch (event) {
        case "status": {
          const parsed = SseStatusSchema.safeParse(safeJson(data));
          if (parsed.success) onStatus?.(parsed.data.stage);
          break;
        }
        case "token": {
          const parsed = SseTokenSchema.safeParse(safeJson(data));
          if (parsed.success) onToken?.(parsed.data.text);
          break;
        }
        case "component": {
          // Loose-validate the catalog `type` only (M10 owns the strict per-type union).
          // Drop on failure — mirrors the backend "invalid component degrades to
          // prose-only, never 500"; an unparseable block must never break the stream.
          const parsed = SseComponentSchema.safeParse(safeJson(data));
          if (parsed.success) onComponent?.(parsed.data);
          break;
        }
        case "done": {
          const parsed = SseDoneSchema.safeParse(safeJson(data));
          if (parsed.success) {
            onDone?.({
              answer: parsed.data.answer,
              route: parsed.data.route ?? null,
            });
          }
          return; // typed completion terminates the stream
        }
        case "error": {
          const parsed = SseErrorSchema.safeParse(safeJson(data));
          onError?.(
            new Error(parsed.success ? parsed.data.detail : "Stream error")
          );
          return; // backend closes the stream cleanly after an error event
        }
        default:
          // Unknown/"message" events are ignored (forward-compatible).
          break;
      }
    }
  } catch (err) {
    if (isAbortError(err)) return; // Stop pressed mid-stream — clean
    onError?.(toError(err));
  }
}

function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null; // safeParse will then fail closed; we never throw on bad JSON
  }
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function toError(err: unknown): Error {
  return err instanceof Error ? err : new Error(String(err));
}
