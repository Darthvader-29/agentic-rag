# M2 — Streaming-Ready Core (Dark Launch)

This milestone builds the entire SSE streaming pipeline — a robust async-generator
`parseSSE` parser, a `streamChat` transport, and a `useStreamingChat` strategy — and wires it
into the `useChat` facade behind a feature flag. **It ships completely dark:** the facade reads
`flags.streaming`, which is `false` until M9, so at runtime the app still delegates to the
blocking strategy from M1 and the UI is byte-for-byte identical. M2 is pure plumbing plus tests;
no visible UX changes.

**Status:** Planned · **Depends on:** M1 (feature folders, `chat.store`, `use-blocking-chat`,
`use-chat` facade, `http-client`, Zod schemas) · **Unlocks:** M9 (flip `NEXT_PUBLIC_FEATURE_STREAMING=true`
for real token streaming + live thinking-steps), M4 (streaming caret animation that rides on
`appendContent`).

> **Ships behind `NEXT_PUBLIC_FEATURE_STREAMING=false`.** Flipping the flag on is explicitly
> **out of scope** for M2 — it is the entire content of M9, gated on the backend Phase 6 SSE
> endpoint landing in production.

---

## 1. Objective & Scope

### In scope

- `lib/sse/parser.ts` — `async function* parseSSE(stream)`: a transport-agnostic SSE frame
  parser over a `ReadableStream<Uint8Array>` that is correct under chunk boundaries that fall
  anywhere (mid-line, mid-frame, mid-multibyte-UTF-8-codepoint).
- `lib/sse/stream-chat.ts` — `streamChat(payload, handlers)`: a POST `fetch` + `ReadableStream`
  transport that drives `parseSSE` and dispatches typed callbacks (`onStatus`, `onToken`,
  `onDone`, `onError`).
- Zod schemas (`features/chat/api/chat.schemas.ts`) for the two SSE `data:` payloads (`status`
  stage, `token` text) and the `done` payload, so every event the parser yields is runtime-validated.
- `features/chat/hooks/use-streaming-chat.ts` — a streaming strategy hook exposing the **exact
  same surface** as `use-blocking-chat`: `{ sendMessage, stop, isStreaming }`, writing through the
  **same store actions** and producing the **identical `Message` shape**.
- `features/chat/hooks/use-chat.ts` — facade updated to read `flags.streaming` and pick the
  strategy at runtime, exposing the stable `{ messages, isStreaming, sendMessage, stop, retry }`.
- Vitest unit tests: `parseSSE` (multi-line `data:`, partial frame split across two reads,
  `[DONE]`, keep-alive comments, malformed lines), the facade strategy switch (flag on → streaming,
  flag off → blocking), and a scripted mock-SSE end-to-end test asserting the store ends in the
  correct state.

### Out of scope

- **Flipping the flag on** — that is M9 (gated on backend Phase 6).
- **Any visible UX change.** No component renders differently. The caret animation that consumes
  the streaming buffer is M4; the thinking-steps / sources panels are M3. M2 only guarantees the
  data plane is correct and tested.
- Reconnection / retry-on-drop logic (the SSE transport is single-shot; a dropped stream surfaces
  an error step — auto-reconnect is a future concern, see §8).
- Backend work. The contract in §2 is the immutable target we parse against.

---

## 2. Backend SSE Contract

> **Source:** `Python-Agentic-RAG-Backend/docs/07_Phase6_LangGraph_and_Streaming.md`,
> **Appendix C — SSE event-type catalog + helpers** (lines 191–213) and **§5 Task 6 / Appendix F**
> (event-ordering test, lines 86, 379–390). This is the QUALITY BAR; our parser and types MUST
> match it exactly.

### 2.1 Transport & framing

The backend serves `POST /api/chat` as a `StreamingResponse(media_type="text/event-stream")`
(Appendix C, line 264). Events are framed with the standard SSE wire format — each event is a
block of `field: value` lines terminated by a **blank line** (`\n\n`). The backend's framing
helper is verbatim (Appendix C, lines 207–213):

```python
def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
```

So every frame on the wire is exactly two lines plus the blank-line terminator:

```
event: <name>\ndata: <json>\n\n
```

`<json>` is a single-line `json.dumps(...)` (no embedded newlines), but our parser MUST NOT
assume single-line `data:` — the SSE spec permits multiple `data:` lines joined by `\n`, and a
future backend or a proxy could re-chunk. We handle the general case.

### 2.2 Event catalog (verbatim from Appendix C, lines 195–203)

| `event:`  | `data:` payload                          | Emitted when                                            |
| --------- | ---------------------------------------- | ------------------------------------------------------- |
| `status`  | `{"stage": "routing"}`                   | supervisor node starts                                  |
| `status`  | `{"stage": "searching web"}`             | web node starts                                         |
| `status`  | `{"stage": "retrieving"}`                | vector node starts                                      |
| `status`  | `{"stage": "synthesizing"}`              | synthesis node starts                                   |
| `token`   | `{"text": "..."}`                        | each generated chunk (or one final chunk if no stream)  |
| `done`    | `{"answer": "...", "route": {...}}`      | stream complete; final answer + route                   |
| `error`   | `{"detail": "..."}`                      | any node raises; closes the stream cleanly              |

**Status stage progression** (Appendix F event-order assertion, line 386 — the observed order for
a vectorstore route is `["routing", "retrieving", "synthesizing"]`; the web route substitutes
`"searching web"` for `"retrieving"`, and a fan-out route may emit both):

```
routing → (retrieving | searching web | both) → synthesizing → [token …] → done
```

**Important contract notes extracted from the doc:**

- The `token` `data:` payload is a JSON object `{"text": "..."}` — **not** a bare string. (The
  plan's milestone prose loosely calls it a `"chunk"` string; the authoritative backend doc,
  Appendix C line 201 and the test at line 387 `e["data"]["text"]`, defines it as
  `{"text": ...}`. **We parse `data.text`.**)
- `done` carries `{"answer", "route"}` (Appendix C line 202). The concatenated `token` texts equal
  `done.answer` (Appendix F line 387–388: `"".join(token texts) == final["answer"]`). We treat
  `done.answer` as the canonical final body and `done.route` as the canonical route.
- `error` carries `{"detail": "..."}` and **closes the stream cleanly** — the backend never leaks
  a mid-stream HTTP 500 (Appendix C line 203, line 259 comment "never leak a 500 mid-stream"). On
  the wire an error is therefore a normal SSE `error` event, not a transport failure.
- `route` here is the backend `RouteDecision` `{"destination": "...", "relevant": bool}` (state doc
  Appendix B, lines 156–159), **not** the frontend `RouteType` union. The streaming strategy maps
  it to the existing `RouteType` (see §5 Task 4) so the `Message` shape stays identical to blocking.

### 2.3 `[DONE]` sentinel

The backend doc's primary terminator is the typed `event: done` frame. However, the **frontend
plan** (`FRONTEND_IMPROVEMENT_PLAN.md` line 86) and this milestone's verify line both mandate that
`parseSSE` handle a `[DONE]` **sentinel** — the near-universal SSE convention (OpenAI-style
`data: [DONE]`) used as a defensive stream terminator. We therefore support **both**:

- A `data: [DONE]` line → parser stops iterating (returns). This is the generic sentinel.
- An `event: done` frame → yielded as a normal event; `streamChat` treats it as completion.

Supporting `[DONE]` costs nothing and makes the parser robust to either the typed `done` frame, a
sentinel, or both. The transport (`streamChat`) treats whichever arrives first as completion.

### 2.4 Sample raw event-stream transcript

A vectorstore-routed turn for the query "what is X?" with the answer "Grounded answer." streamed
in three token chunks. `␊` denotes `\n`; a blank line is the `\n\n` frame terminator.

```
event: status␊
data: {"stage": "routing"}␊
␊
event: status␊
data: {"stage": "retrieving"}␊
␊
event: status␊
data: {"stage": "synthesizing"}␊
␊
event: token␊
data: {"text": "Grounded "}␊
␊
event: token␊
data: {"text": "answer"}␊
␊
event: token␊
data: {"text": "."}␊
␊
event: done␊
data: {"answer": "Grounded answer.", "route": {"destination": "vectorstore", "relevant": true}}␊
␊
```

Concatenated token texts (`"Grounded " + "answer" + "."`) = `"Grounded answer."` = `done.answer`,
exactly as Appendix F asserts. A keep-alive heartbeat (a comment line `: keep-alive\n\n`, used by
many SSE servers / proxies to hold the connection open) may appear between any two frames and MUST
be ignored by the parser.

---

## 3. Decisions & Rationale

| Decision | Rationale | Alternatives considered |
| -------- | --------- | ----------------------- |
| **POST `fetch` + `ReadableStream`, NOT `EventSource`** | `/api/chat` is a POST with a JSON body (`message`, `session_id`, `web_search_allowed`) and — once Phase 3 lands (M6) — an `Authorization: Bearer` header. The browser `EventSource` API can issue **only GET with no custom headers and no body**. `fetch` + `res.body` (a `ReadableStream<Uint8Array>`) gives us POST, arbitrary headers, and an `AbortController` for the Stop button. | `EventSource` (can't POST, can't send a body, can't set auth header, no abort) — non-starter. WebSockets (bidirectional overhead we don't need; backend chose SSE, Appendix-table line 29). |
| **Async-generator parser `async function* parseSSE`** | Decouples wire-format parsing from transport and from React. The parser is a pure, synchronously-testable unit: feed it any `ReadableStream`, get back an async iterator of typed `{event,data}` frames. No DOM, no fetch, no store — trivially unit-testable with an in-memory stream. | A callback-based parser (harder to test, inverts control); parsing inside the hook (couples wire format to React, untestable in isolation). |
| **`TextDecoderStream` for bytes→text** | The network delivers `Uint8Array` chunks that can split a multibyte UTF-8 codepoint across reads. `TextDecoderStream` is a stateful streaming decoder that buffers a partial codepoint internally and only emits complete characters — so we never corrupt a token mid-emoji. | Manual `new TextDecoder().decode(chunk)` per chunk (re-creates state each call → mojibake on split codepoints); `TextDecoder` with `{stream:true}` (works but `TextDecoderStream` composes natively with `pipeThrough`). See §8. |
| **`onStatus(stage)` → `pushStep`** | The backend `status` stages (`routing`/`retrieving`/`searching web`/`synthesizing`) are exactly the "thinking / agent-steps" feed the UI's ThinkingSteps panel renders. Mapping each stage to a store `step` means the panel animates live in M9 with zero hook changes. | Buffer stages and render at the end (loses the live thinking UX that is the whole point of streaming). |
| **`onToken(chunk)` → `appendContent`** | Token-by-token append into the in-flight assistant message is the streaming body; the M4 caret rides on the same buffer. `appendContent` is O(1) string concat in Zustand, kept **out of the Query cache** (high-frequency writes; plan "State split", line 70). | Replacing the whole `content` each token (O(n²) re-render churn); storing tokens in TanStack Query (cache thrash on every token). |
| **Both strategies write the SAME store actions / `Message` shape** | The facade's contract is that the UI never knows which strategy ran. Blocking synthesizes a single `done` step + `context_count` sources; streaming pushes live steps and appends tokens — but **both end at `finalize(id)` with an identical `Message`** (`{id, role, content, route, sources, steps, status, timestamp}`). This is the invariant that makes flipping the flag a no-op for every component. | Two divergent message shapes behind a flag (every consumer would need flag-aware branches — exactly what the facade exists to prevent). |
| **Dark launch behind `flags.streaming`** | The backend SSE endpoint does not exist in production until Phase 6. Shipping the parser + transport + hook **now** (fully tested) but gated `false` means M9 is a one-line flag flip against a battle-tested data plane, not a big-bang integration. The plan calls this out explicitly (line 31, line 90). | Hold all streaming code until M9 (a giant risky drop landing parser + transport + hook + flag flip + backend integration at once). |

---

## 4. Target File Tree (delta)

Files **added** (✚) or **modified** (✎) by M2. Everything else from M1 is untouched.

```
lib/
  sse/
✚   parser.ts                         async function* parseSSE(stream)
✚   stream-chat.ts                    streamChat(payload, handlers) — POST fetch transport
✚   __tests__/
✚     parser.test.ts                  multi-line / partial / [DONE] / keep-alive / malformed
✚     stream-chat.test.ts             scripted mock-fetch → typed callbacks, AbortError swallow

features/chat/
  api/
✎   chat.schemas.ts                   + SseStatusSchema, SseTokenSchema, SseDoneSchema, SseErrorSchema
  hooks/
✚   use-streaming-chat.ts             streaming strategy: { sendMessage, stop, isStreaming }
✎   use-chat.ts                       facade: read flags.streaming, pick strategy
✚   __tests__/
✚     use-chat.facade.test.tsx        flag on → streaming hook; flag off → blocking hook
✚     use-streaming-chat.test.tsx     mock streamChat → store ends in correct Message shape

test/
✎   msw/handlers.ts                   + sse chat handler (text/event-stream scripted body)
✚   utils/mock-stream.ts              in-memory ReadableStream<Uint8Array> helper for parser tests
```

**Pre-existing from M1 that M2 depends on (read-only here):** `features/chat/store/chat.store.ts`
(the `addMessage`/`appendContent`/`pushStep`/`setSources`/`setStatus`/`finalize` actions, §6),
`features/chat/hooks/use-blocking-chat.ts` (the strategy whose surface streaming must mirror),
`lib/flags.ts` (`flags.streaming`), `lib/env.ts` (`env.NEXT_PUBLIC_API_URL`,
`env.NEXT_PUBLIC_FEATURE_STREAMING`), `lib/api/http-client.ts`, `features/chat/api/chat.schemas.ts`,
`types/index.ts` (the unified `Message` shape with `steps`/`sources`/`status`).

---

## 5. Tasks (ordered)

Build bottom-up: parser → schemas → transport → strategy hook → facade switch. Each layer is fully
unit-testable before the next is written.

### Task 1 — `lib/sse/parser.ts`: the robust SSE frame parser

**Goal.** A transport-agnostic `async function* parseSSE(stream)` that consumes a
`ReadableStream<Uint8Array>` and yields validated-shape `{ event, data }` frames. It must be
correct when network chunk boundaries fall **anywhere**: mid-line, mid-frame (between the two
lines of a frame), or mid-multibyte-codepoint. It joins multiple `data:` lines with `\n`, ignores
comment/keep-alive lines, stops on a `data: [DONE]` sentinel, and flushes a trailing frame that
has no terminating blank line at stream end.

**Files.** `lib/sse/parser.ts`.

```ts
// lib/sse/parser.ts
//
// Transport-agnostic Server-Sent-Events frame parser.
//
// Consumes a ReadableStream<Uint8Array> (e.g. fetch Response.body), decodes it
// as a UTF-8 stream, splits the text into SSE frames on the blank-line ("\n\n")
// terminator, and yields one ParsedSseEvent per frame.
//
// Wire format (per the WHATWG SSE spec and the backend Phase-6 contract):
//   event: <name>\n
//   data:  <payload-line-1>\n
//   data:  <payload-line-2>\n   (multiple data: lines are joined with "\n")
//   \n                           (blank line terminates the frame)
//
// Robustness guarantees:
//   - Chunk boundaries may fall anywhere: we accumulate text in `buffer` and only
//     emit complete frames (split on "\n\n"); the trailing partial stays buffered.
//   - Multibyte UTF-8 codepoints split across byte-chunks are handled by
//     TextDecoderStream (stateful streaming decode) — never corrupted.
//   - Comment / keep-alive lines (starting with ":") are ignored.
//   - A `data: [DONE]` sentinel terminates iteration.
//   - "\r\n" and "\r" line endings are normalised to "\n".

export interface ParsedSseEvent {
  /** The SSE `event:` field. Absent field defaults to "message" per the SSE spec. */
  event: string;
  /** The joined `data:` payload (multiple data: lines joined with "\n"). */
  data: string;
}

const DONE_SENTINEL = "[DONE]";

/**
 * Parse one already-delimited SSE frame (the text between two blank lines, with
 * the trailing terminator stripped) into a ParsedSseEvent. Returns null if the
 * frame has no data lines (e.g. a pure comment/keep-alive block) so the caller
 * can skip it. Returns the DONE_SENTINEL marker via the `data` field unchanged
 * so the caller can detect it.
 */
function parseFrame(frame: string): ParsedSseEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const rawLine of frame.split("\n")) {
    // A line starting with ":" is a comment (keep-alive heartbeat). Ignore it.
    if (rawLine.startsWith(":")) continue;

    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    // Per spec: strip exactly one leading space after the colon.
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "event":
        event = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      // "id" and "retry" are part of the SSE spec but unused by this backend;
      // accept-and-ignore for forward compatibility. Unknown fields are ignored.
      default:
        break;
    }
  }

  if (dataLines.length === 0) return null; // comment-only / empty frame
  return { event, data: dataLines.join("\n") };
}

export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ParsedSseEvent, void, unknown> {
  // Stateful streaming UTF-8 decode: a codepoint split across byte-chunks is
  // buffered internally and only emitted once complete.
  const textStream = stream.pipeThrough(new TextDecoderStream());
  const reader = textStream.getReader();

  let buffer = "";

  try {
    for (;;) {
      const { value, done } = await reader.read();

      if (value) {
        // Normalise CRLF / CR to LF so "\n\n" framing is uniform.
        buffer += value.replace(/\r\n?/g, "\n");

        // Emit every COMPLETE frame currently in the buffer. A frame is complete
        // once we have seen its terminating blank line ("\n\n"). The final
        // element after the last "\n\n" is a (possibly empty) partial frame that
        // stays in the buffer for the next read.
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);

          const parsed = parseFrame(frame);
          if (parsed === null) continue; // keep-alive / comment-only

          if (parsed.data === DONE_SENTINEL) return; // sentinel: stop cleanly
          yield parsed;
        }
      }

      if (done) {
        // Stream ended. Flush any trailing frame that lacked a terminating
        // blank line (some servers/proxies drop the final "\n\n").
        const tail = buffer.trim();
        if (tail.length > 0) {
          const parsed = parseFrame(tail);
          if (parsed !== null && parsed.data !== DONE_SENTINEL) {
            yield parsed;
          }
        }
        return;
      }
    }
  } finally {
    // Always release the lock so the underlying stream can be cancelled/GC'd.
    reader.releaseLock();
  }
}
```

**Acceptance.** Pure function of its input stream; no fetch/DOM/store. Handles multi-line `data:`,
partial frames across reads, `[DONE]`, keep-alive comments, malformed lines, and a missing final
terminator. Releases the reader lock in `finally`.

---

### Task 2 — Zod schemas for SSE payloads (`features/chat/api/chat.schemas.ts`)

**Goal.** Runtime-validate every SSE `data:` payload the parser yields, so a malformed/unexpected
frame from the backend can never silently corrupt the store. Mirrors the M1 convention of
`z.infer` re-exports keeping runtime + compile-time locked (plan line 82).

**Files.** Append to `features/chat/api/chat.schemas.ts`.

```ts
// features/chat/api/chat.schemas.ts  (M2 additions)
import { z } from "zod";

/** event: status  →  data: {"stage": "routing" | ...} */
export const SseStatusSchema = z.object({
  stage: z.enum([
    "routing",
    "retrieving",
    "searching web",
    "synthesizing",
    "done",
    "error",
  ]),
});
export type SseStatus = z.infer<typeof SseStatusSchema>;

/** event: token  →  data: {"text": "..."} */
export const SseTokenSchema = z.object({
  text: z.string(),
});
export type SseToken = z.infer<typeof SseTokenSchema>;

/** The backend RouteDecision carried by the done event (Appendix B). */
export const SseRouteDecisionSchema = z.object({
  destination: z.string(), // "vectorstore" | "web_search"
  relevant: z.boolean().optional(),
});
export type SseRouteDecision = z.infer<typeof SseRouteDecisionSchema>;

/** event: done  →  data: {"answer": "...", "route": {...}} */
export const SseDoneSchema = z.object({
  answer: z.string(),
  route: SseRouteDecisionSchema.nullable().optional(),
});
export type SseDone = z.infer<typeof SseDoneSchema>;

/** event: error  →  data: {"detail": "..."} */
export const SseErrorSchema = z.object({
  detail: z.string(),
});
export type SseError = z.infer<typeof SseErrorSchema>;
```

> The `stage` enum lists `done`/`error` defensively in case the backend ever emits them as
> `status` stages; the canonical completion/error frames are the typed `done`/`error` events.

**Acceptance.** Each schema parses its sample payload from §2.4 and rejects a payload missing the
required key.

---

### Task 3 — `lib/sse/stream-chat.ts`: the POST-fetch SSE transport

**Goal.** `streamChat(payload, handlers)` POSTs the chat request with `Accept: text/event-stream`,
guards a non-ok response and a null body, drives `parseSSE(res.body)`, Zod-validates each frame's
`data`, and dispatches typed callbacks. Swallows `AbortError` (user pressed Stop) as a clean stop.

**Files.** `lib/sse/stream-chat.ts`.

```ts
// lib/sse/stream-chat.ts
import { env } from "@/lib/env";
import { parseSSE } from "@/lib/sse/parser";
import {
  SseStatusSchema,
  SseTokenSchema,
  SseDoneSchema,
  SseErrorSchema,
  type SseRouteDecision,
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
  /** The stream completed with the final answer + backend route decision. */
  onDone?: (result: { answer: string; route: SseRouteDecision | null }) => void;
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
  handlers: StreamChatHandlers,
): Promise<void> {
  const { onStatus, onToken, onDone, onError, signal } = handlers;

  let res: Response;
  try {
    res = await fetch(`${env.NEXT_PUBLIC_API_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        // M6 (Phase 3): the http-client auth interceptor injects Bearer here.
      },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (isAbortError(err)) return; // aborted before headers — clean stop
    onError?.(toError(err));
    return;
  }

  if (!res.ok) {
    // Non-stream HTTP error (auth/rate-limit raised BEFORE the stream opened —
    // see backend Appendix C "401/429 raised before StreamingResponse").
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
        case "done": {
          const parsed = SseDoneSchema.safeParse(safeJson(data));
          if (parsed.success) {
            onDone?.({ answer: parsed.data.answer, route: parsed.data.route ?? null });
          }
          return; // typed completion terminates the stream
        }
        case "error": {
          const parsed = SseErrorSchema.safeParse(safeJson(data));
          onError?.(new Error(parsed.success ? parsed.data.detail : "Stream error"));
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
```

**Acceptance.** POSTs with `Accept: text/event-stream`; guards `!res.ok` and `!res.body`;
dispatches typed callbacks; `done`/`error` terminate; `AbortError` resolves cleanly without calling
`onError`; bad JSON / failed Zod parse is dropped silently (fail-closed), never throws.

---

### Task 4 — `features/chat/hooks/use-streaming-chat.ts`: the streaming strategy

**Goal.** Expose the **same surface as `use-blocking-chat`** — `{ sendMessage, stop, isStreaming }`
— while driving `streamChat` and writing through the **same store actions**. On send: push a user
message + an empty assistant message, open an `AbortController`, then map `onStatus → pushStep`,
`onToken → appendContent`, `onDone → finalize`, `onError → error step + finalize`. `stop()` aborts.

**Files.** `features/chat/hooks/use-streaming-chat.ts`.

```ts
// features/chat/hooks/use-streaming-chat.ts
"use client";

import { useCallback, useRef } from "react";
import { v4 as uuidv4 } from "uuid";

import { useChatStore } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { streamChat } from "@/lib/sse/stream-chat";
import type { RouteType } from "@/types";

/**
 * Map a backend RouteDecision ({destination, relevant}) to the frontend RouteType
 * union, so a streamed Message is shape-identical to a blocking one.
 */
function mapRoute(route: { destination: string; relevant?: boolean } | null): RouteType {
  if (!route) return "DIRECT";
  return route.destination === "web_search" ? "WEB" : "RAG";
}

export function useStreamingChat() {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendContent = useChatStore((s) => s.appendContent);
  const pushStep = useChatStore((s) => s.pushStep);
  const setStatus = useChatStore((s) => s.setStatus);
  const setSources = useChatStore((s) => s.setSources);
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
        timestamp: new Date(),
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
        timestamp: new Date(),
      });

      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);

      await streamChat(
        {
          message: text,
          session_id: getSessionId(),
          web_search_allowed: webSearchAllowed,
        },
        {
          signal: controller.signal,
          onStatus: (stage) => {
            // status stage → a live thinking step (feeds ThinkingSteps panel)
            pushStep(assistantId, { stage, at: Date.now() });
            setStatus(assistantId, stage);
          },
          onToken: (chunk) => {
            // token chunk → append to the streaming body (+ M4 caret rides this)
            appendContent(assistantId, chunk);
          },
          onDone: ({ answer, route }) => {
            // Canonical final body is done.answer (== concatenated tokens).
            // Idempotently set it so a non-streaming provider (single token) and a
            // streaming provider both end identical.
            setRoute(assistantId, mapRoute(route));
            finalize(assistantId, { content: answer });
          },
          onError: (error) => {
            pushStep(assistantId, { stage: "error", at: Date.now() });
            setRoute(assistantId, "ERROR");
            finalize(assistantId, {
              content:
                error.message ||
                "The AI service returned an error. Please try again later.",
            });
          },
        },
      );

      abortRef.current = null;
      setStreaming(false);
    },
    [
      addMessage,
      appendContent,
      pushStep,
      setStatus,
      setSources,
      setRoute,
      finalize,
      setStreaming,
    ],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort(); // AbortError → streamChat resolves cleanly
    abortRef.current = null;
    setStreaming(false);
  }, [setStreaming]);

  return { sendMessage, stop, isStreaming };
}
```

> `finalize(id, { content })` overwrites the streamed buffer with the canonical `done.answer` and
> flips `status` to `"done"`. Because the concatenated tokens already equal `done.answer` (§2.4),
> this is a no-op visually but guarantees correctness when a provider can't stream and emits the
> whole answer as one token. `setSources` is wired for M9 (the backend `done`/synthesis will carry
> source metadata); in M2 the streamed `sources` array stays `[]`, matching the no-source case.

**Acceptance.** Same `{ sendMessage, stop, isStreaming }` surface as `use-blocking-chat`. Drives
`streamChat`. `stop()` aborts the controller. Produces a `Message` of identical shape to blocking.

---

### Task 5 — `features/chat/hooks/use-chat.ts`: facade strategy switch

**Goal.** Read `flags.streaming` and delegate to the streaming or blocking strategy, exposing the
stable facade `{ messages, isStreaming, sendMessage, stop, retry }`. Both strategy hooks are
called unconditionally (Rules of Hooks); only the chosen one's actions are returned.

**Files.** `features/chat/hooks/use-chat.ts` (modify the M1 version).

```ts
// features/chat/hooks/use-chat.ts
"use client";

import { useCallback } from "react";

import { flags } from "@/lib/flags";
import { useChatStore } from "@/features/chat/store/chat.store";
import { useBlockingChat } from "@/features/chat/hooks/use-blocking-chat";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";

export interface UseChatApi {
  messages: ReturnType<typeof useChatStore.getState>["messages"];
  isStreaming: boolean;
  sendMessage: (text: string, webSearchAllowed: boolean) => Promise<void> | void;
  stop: () => void;
  retry: () => void;
}

export function useChat(): UseChatApi {
  // Both hooks are called every render (Rules of Hooks); we SELECT one strategy.
  // Each is cheap and side-effect-free until its sendMessage is invoked.
  const blocking = useBlockingChat();
  const streaming = useStreamingChat();

  const strategy = flags.streaming ? streaming : blocking;

  const messages = useChatStore((s) => s.messages);
  const lastUserMessage = useChatStore((s) => s.lastUserMessage);

  const retry = useCallback(() => {
    const last = lastUserMessage();
    if (last) strategy.sendMessage(last.content, last.webSearchAllowed ?? false);
  }, [strategy, lastUserMessage]);

  return {
    messages,
    isStreaming: strategy.isStreaming,
    sendMessage: strategy.sendMessage,
    stop: strategy.stop,
    retry,
  };
}
```

> `flags.streaming` derives from `env.NEXT_PUBLIC_FEATURE_STREAMING` (Zod-coerced boolean,
> default `false`). With it `false`, `strategy === blocking` and the app behaves exactly as M1.
> `retry` re-sends the last user message through whichever strategy is active.

**Acceptance.** `flags.streaming === false` → every facade method is the blocking hook's; `=== true`
→ the streaming hook's. The facade's public surface is identical in both cases.

---

## 6. Store Action Contract

Both strategies write through the **same** `chat.store` (Zustand) actions defined in M1. M2 adds no
new store actions beyond what M1 created; it only adds a second **caller** (the streaming hook). The
action surface:

| Action | Signature | Blocking caller (M1) | Streaming caller (M2) |
| ------ | --------- | -------------------- | --------------------- |
| `addMessage` | `(msg: Message) => void` | push user msg; push final assistant msg | push user msg; push **empty** assistant msg |
| `appendContent` | `(id: string, chunk: string) => void` | — (not used) | per `onToken` chunk (O(1) concat) |
| `pushStep` | `(id: string, step: Step) => void` | once: synthesized `{stage:"done"}` step | per `onStatus` stage (live) |
| `setStatus` | `(id: string, status: string) => void` | `"done"` | per stage, then `"done"`/`"error"` |
| `setSources` | `(id: string, sources: Source[]) => void` | from `context_count` | `[]` in M2; backend metadata in M9 |
| `setRoute` | `(id: string, route: RouteType) => void` | from `response.route` | `mapRoute(done.route)` |
| `finalize` | `(id: string, patch?: Partial<Message>) => void` | flips `status:"done"` | overwrite `content = done.answer`, `status:"done"` |
| `setStreaming` | `(b: boolean) => void` | true on send, false on settle | true on send, false on settle/stop/error |

**Identical-shape proof.** The unified `Message` (from `types/index.ts`, extended in M1) is:

```ts
interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: RouteType;
  sources: Source[];          // [] when none
  steps: Step[];              // synthesized (blocking) or live (streaming)
  status: "streaming" | "done" | "error";
  timestamp: Date;
}
```

- **Blocking** produces, on success, exactly one assistant `Message`:
  `{ content: answer, route, sources: makeSources(context_count), steps: [{stage:"done"}], status:"done" }`.
- **Streaming** produces, after `onDone`, an assistant `Message` whose `content` was streamed then
  overwritten to `done.answer`, `route = mapRoute(done.route)`, `sources: []` (M2) / backend (M9),
  `steps: [{stage:"routing"}, …, {stage:"synthesizing"}]`, `status:"done"`.

Both are the **same TypeScript type with all required keys populated**. No component can observe
which strategy ran — that is the invariant M2 exists to guarantee, and it is asserted by the
end-to-end store test in §7.

---

## 7. Testing & Verification

Test runner: **Vitest** + React Testing Library (M5 stack; M2 introduces the SSE-specific suites).
Mock the network with an in-memory `ReadableStream` (parser) and a scripted `fetch`/MSW handler
(transport + hook). No real backend is contacted.

### 7.1 In-memory mock stream helper

```ts
// test/utils/mock-stream.ts
const encoder = new TextEncoder();

/** A ReadableStream that emits each provided string chunk as one Uint8Array read. */
export function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

/** A ReadableStream that emits raw bytes (to force multibyte splits across reads). */
export function streamFromByteChunks(byteChunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const bytes of byteChunks) controller.enqueue(bytes);
      controller.close();
    },
  });
}
```

### 7.2 `parseSSE` unit tests

```ts
// lib/sse/__tests__/parser.test.ts
import { describe, it, expect } from "vitest";
import { parseSSE } from "@/lib/sse/parser";
import { streamFromChunks, streamFromByteChunks } from "@/test/utils/mock-stream";

async function collect(stream: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const ev of parseSSE(stream)) out.push(ev);
  return out;
}

describe("parseSSE", () => {
  it("parses a simple status + token + done sequence", async () => {
    const events = await collect(
      streamFromChunks([
        'event: status\ndata: {"stage": "routing"}\n\n',
        'event: token\ndata: {"text": "Hi"}\n\n',
        'event: done\ndata: {"answer": "Hi", "route": null}\n\n',
      ]),
    );
    expect(events.map((e) => e.event)).toEqual(["status", "token", "done"]);
    expect(events[0].data).toBe('{"stage": "routing"}');
  });

  it("joins multiple data: lines with \\n", async () => {
    const events = await collect(
      streamFromChunks(["event: token\ndata: line1\ndata: line2\n\n"]),
    );
    expect(events[0].data).toBe("line1\nline2");
  });

  it("reassembles a single frame split across two reads (partial buffer)", async () => {
    const events = await collect(
      streamFromChunks(['event: tok', 'en\ndata: {"text": "x"}', "\n\n"]),
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "x"}' }]);
  });

  it("stops cleanly on the [DONE] sentinel and ignores anything after", async () => {
    const events = await collect(
      streamFromChunks([
        'event: token\ndata: {"text": "a"}\n\n',
        "data: [DONE]\n\n",
        'event: token\ndata: {"text": "b"}\n\n', // must NOT be yielded
      ]),
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "a"}' }]);
  });

  it("ignores keep-alive comment lines", async () => {
    const events = await collect(
      streamFromChunks([": keep-alive\n\n", 'event: token\ndata: {"text": "y"}\n\n']),
    );
    expect(events.map((e) => e.event)).toEqual(["token"]);
  });

  it("tolerates a malformed line without a colon", async () => {
    const events = await collect(
      streamFromChunks(["garbage-no-colon\nevent: token\ndata: ok\n\n"]),
    );
    expect(events).toEqual([{ event: "token", data: "ok" }]);
  });

  it("flushes a trailing frame with no terminating blank line", async () => {
    const events = await collect(streamFromChunks(['event: token\ndata: {"text": "z"}']));
    expect(events).toEqual([{ event: "token", data: '{"text": "z"}' }]);
  });

  it("normalises CRLF line endings", async () => {
    const events = await collect(
      streamFromChunks(['event: token\r\ndata: {"text": "w"}\r\n\r\n']),
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "w"}' }]);
  });

  it("does not corrupt a multibyte codepoint split across byte reads", async () => {
    // "🚀" (U+1F680) is 4 UTF-8 bytes: F0 9F 9A 80. Split it across two reads.
    const full = new TextEncoder().encode('event: token\ndata: {"text": "🚀"}\n\n');
    const splitAt = full.indexOf(0x80); // mid-emoji byte boundary
    const events = await collect(
      streamFromByteChunks([full.slice(0, splitAt), full.slice(splitAt)]),
    );
    expect(JSON.parse(events[0].data).text).toBe("🚀");
  });
});
```

### 7.3 `streamChat` transport tests

```ts
// lib/sse/__tests__/stream-chat.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { streamChat } from "@/lib/sse/stream-chat";
import { streamFromChunks } from "@/test/utils/mock-stream";

function mockFetchOnce(body: ReadableStream<Uint8Array> | null, ok = true, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok, status, body, json: async () => ({}) }) as unknown as Response),
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("streamChat", () => {
  it("dispatches status, token, and done callbacks in order", async () => {
    mockFetchOnce(
      streamFromChunks([
        'event: status\ndata: {"stage": "routing"}\n\n',
        'event: token\ndata: {"text": "Grounded "}\n\n',
        'event: token\ndata: {"text": "answer."}\n\n',
        'event: done\ndata: {"answer": "Grounded answer.", "route": {"destination": "vectorstore"}}\n\n',
      ]),
    );
    const stages: string[] = [];
    let body = "";
    let done: { answer: string } | null = null;

    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onStatus: (s) => stages.push(s), onToken: (t) => (body += t), onDone: (d) => (done = d) },
    );

    expect(stages).toEqual(["routing"]);
    expect(body).toBe("Grounded answer.");
    expect(done!.answer).toBe("Grounded answer."); // == concatenated tokens
  });

  it("reports a typed error event via onError and stops", async () => {
    mockFetchOnce(streamFromChunks(['event: error\ndata: {"detail": "boom"}\n\n']));
    const onError = vi.fn();
    await streamChat({ message: "q", session_id: "s", web_search_allowed: false }, { onError });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "boom" }));
  });

  it("surfaces a non-ok HTTP response (auth/rate-limit before stream) via onError", async () => {
    mockFetchOnce(null, false, 429);
    const onError = vi.fn();
    await streamChat({ message: "q", session_id: "s", web_search_allowed: false }, { onError });
    expect(onError).toHaveBeenCalled();
  });

  it("swallows AbortError as a clean stop (no onError)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    const onError = vi.fn();
    await streamChat({ message: "q", session_id: "s", web_search_allowed: false }, { onError });
    expect(onError).not.toHaveBeenCalled();
  });
});
```

### 7.4 Facade strategy-switch test

```ts
// features/chat/hooks/__tests__/use-chat.facade.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const blockingSend = vi.fn();
const streamingSend = vi.fn();

vi.mock("@/features/chat/hooks/use-blocking-chat", () => ({
  useBlockingChat: () => ({ sendMessage: blockingSend, stop: vi.fn(), isStreaming: false }),
}));
vi.mock("@/features/chat/hooks/use-streaming-chat", () => ({
  useStreamingChat: () => ({ sendMessage: streamingSend, stop: vi.fn(), isStreaming: false }),
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
```

### 7.5 End-to-end mock-SSE → store test

Drives the real `useStreamingChat` against a mocked `streamChat` that replays a scripted event
sequence, then asserts the store ends in the correct `Message` shape (proving §6's invariant).

```ts
// features/chat/hooks/__tests__/use-streaming-chat.test.tsx
import { describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

// Replay a scripted SSE sequence by invoking the handlers streamChat would call.
vi.mock("@/lib/sse/stream-chat", () => ({
  streamChat: vi.fn(async (_payload, h) => {
    h.onStatus?.("routing");
    h.onStatus?.("retrieving");
    h.onStatus?.("synthesizing");
    h.onToken?.("Grounded ");
    h.onToken?.("answer.");
    h.onDone?.({ answer: "Grounded answer.", route: { destination: "vectorstore" } });
  }),
}));

import { useChatStore } from "@/features/chat/store/chat.store";
import { useStreamingChat } from "@/features/chat/hooks/use-streaming-chat";

describe("useStreamingChat end-to-end", () => {
  it("ends with a finalized assistant Message of the canonical shape", async () => {
    useChatStore.setState({ messages: [] });
    const { result } = renderHook(() => useStreamingChat());

    await act(async () => {
      await result.current.sendMessage("what is X?", false);
    });

    const msgs = useChatStore.getState().messages;
    const assistant = msgs.find((m) => m.role === "assistant")!;
    expect(assistant.content).toBe("Grounded answer.");
    expect(assistant.route).toBe("RAG");
    expect(assistant.status).toBe("done");
    expect(assistant.steps.map((s) => s.stage)).toEqual([
      "routing",
      "retrieving",
      "synthesizing",
    ]);
    expect(assistant.sources).toEqual([]);
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});
```

### 7.6 MSW SSE handler (for M5 integration / Playwright)

```ts
// test/msw/handlers.ts (M2 addition)
import { http, HttpResponse } from "msw";
import { env } from "@/lib/env";

const SSE_SCRIPT =
  'event: status\ndata: {"stage": "routing"}\n\n' +
  'event: status\ndata: {"stage": "retrieving"}\n\n' +
  'event: status\ndata: {"stage": "synthesizing"}\n\n' +
  'event: token\ndata: {"text": "Grounded "}\n\n' +
  'event: token\ndata: {"text": "answer."}\n\n' +
  'event: done\ndata: {"answer": "Grounded answer.", "route": {"destination": "vectorstore"}}\n\n';

export const sseChatHandler = http.post(`${env.NEXT_PUBLIC_API_URL}/chat`, ({ request }) => {
  if (request.headers.get("accept") !== "text/event-stream") return; // fall through to blocking handler
  return new HttpResponse(SSE_SCRIPT, {
    headers: { "Content-Type": "text/event-stream" },
  });
});
```

### 7.7 Flag-off parity check

A test (or M5 Playwright run) with `NEXT_PUBLIC_FEATURE_STREAMING` unset/false asserts the app
behaves exactly as M1: `useChat` calls the blocking mutation, no `EventSource`/streaming fetch is
issued, and a sent message produces the same blocking `Message`. This is the gate that M2 is truly
dark.

---

## 8. Risks & Gotchas

- **Partial UTF-8 multibyte split across chunks.** A 4-byte emoji (e.g. `🚀` = `F0 9F 9A 80`) can
  land with its bytes split across two network reads. Decoding each `Uint8Array` independently with
  a fresh `TextDecoder` corrupts it (`�`). **Resolution:** `parseSSE` pipes through
  `TextDecoderStream`, a *stateful* streaming decoder that buffers an incomplete codepoint and emits
  it only once the continuation bytes arrive. Covered by the byte-split test in §7.2.
- **Frame boundaries split across reads.** A frame's `\n\n` terminator may not have arrived yet on a
  given read. **Resolution:** we accumulate into `buffer` and only emit frames up to the last
  complete `\n\n`; the trailing partial persists to the next read. Covered by the partial-buffer
  test.
- **Backpressure / cancellation via `AbortController`.** The Stop button calls `controller.abort()`,
  which rejects the in-flight `reader.read()` with `AbortError`. **Resolution:** `streamChat`
  swallows `AbortError` as a clean stop (no `onError`), and `parseSSE`'s `finally` releases the
  reader lock so the underlying stream is cancelled and GC'd. The backend independently honours
  `request.is_disconnected()` (backend Appendix C line 243) to stop burning tokens.
- **`[DONE]` sentinel vs. typed stream end.** Two valid terminators exist: the typed `event: done`
  frame (backend's canonical signal) and the generic `data: [DONE]` sentinel (frontend plan mandate,
  line 86). **Resolution:** the parser stops on `[DONE]`; `streamChat` stops on `event: done`/`error`.
  Whichever arrives first ends the stream. Both are tested.
- **Not leaking readers.** A `ReadableStreamDefaultReader` holds a lock on the stream; failing to
  release it leaks the stream and prevents cancellation. **Resolution:** `parseSSE` wraps its loop in
  `try { … } finally { reader.releaseLock(); }`.
- **Next.js client-side fetch streaming.** `useStreamingChat` is a `"use client"` hook; the fetch
  runs in the browser, where `Response.body` is a real `ReadableStream`. **Gotcha:** never call
  `streamChat` from a Server Component or during SSR — there is no streaming body there. The hook's
  `"use client"` directive and its invocation only from event handlers (not render) prevent this.
- **Keep-alive comment lines.** SSE servers/proxies emit `:` comment lines (and `: keep-alive`) to
  hold the connection open. **Resolution:** `parseFrame` skips any line starting with `:`, and a
  comment-only frame (no `data:` lines) yields `null` and is dropped. Tested.
- **Reconnection / retry on drop = out of scope.** Unlike `EventSource`, `fetch`+`ReadableStream`
  does not auto-reconnect. M2 surfaces a dropped stream as an `error` step and stops; auto-reconnect
  with `Last-Event-ID` is a deliberate non-goal (noted for a future milestone).
- **`token` payload shape mismatch in the prose.** The milestone prose says `token` carries a
  `"chunk"` string; the authoritative backend doc (Appendix C, line 201; test line 387) defines it
  as `{"text": ...}`. We follow the doc and parse `data.text`. Flagged here so no one "fixes" the
  schema back to a bare string.

---

## 9. Exit Criteria (checkable)

- [ ] `lib/sse/parser.ts` exists; `async function* parseSSE` handles multi-line `data:`, partial
      frames across reads, `[DONE]`, keep-alive comments, malformed lines, missing final terminator,
      and CRLF normalisation.
- [ ] All §7.2 `parseSSE` unit tests pass, **including the multibyte-split test**.
- [ ] `lib/sse/stream-chat.ts` POSTs with `Accept: text/event-stream`, guards `!res.ok`/`!res.body`,
      dispatches typed Zod-validated callbacks, and swallows `AbortError` (all §7.3 tests pass).
- [ ] `features/chat/api/chat.schemas.ts` exports `SseStatusSchema`/`SseTokenSchema`/`SseDoneSchema`/
      `SseErrorSchema`; each parses its §2.4 sample and rejects a missing-key payload.
- [ ] `features/chat/hooks/use-streaming-chat.ts` exposes `{ sendMessage, stop, isStreaming }`
      identical to `use-blocking-chat`, writes through the §6 store actions, and the §7.5 end-to-end
      test shows a finalized assistant `Message` of the canonical shape.
- [ ] `features/chat/hooks/use-chat.ts` reads `flags.streaming`; the §7.4 strategy-switch test passes
      for both flag states.
- [ ] **Flag-off parity:** with `NEXT_PUBLIC_FEATURE_STREAMING=false`, the app behaves exactly as M1
      (blocking path only; no streaming fetch issued) — §7.7 check green.
- [ ] No visible UX change in either theme; no component file modified for behaviour.
- [ ] `npm run lint`, `tsc --noEmit`, `vitest run` all pass; no new `any` introduced.

---

## 10. Commit Plan

Milestone-sized, conventional commits on the milestone branch (one concern each):

1. `feat(sse): add parseSSE async-generator frame parser + unit tests`
   — `lib/sse/parser.ts`, `lib/sse/__tests__/parser.test.ts`, `test/utils/mock-stream.ts`.
2. `feat(chat): add SSE payload Zod schemas (status/token/done/error)`
   — `features/chat/api/chat.schemas.ts`.
3. `feat(sse): add streamChat POST+ReadableStream transport + tests`
   — `lib/sse/stream-chat.ts`, `lib/sse/__tests__/stream-chat.test.ts`.
4. `feat(chat): add useStreamingChat strategy writing the unified Message shape`
   — `features/chat/hooks/use-streaming-chat.ts`, `__tests__/use-streaming-chat.test.tsx`.
5. `feat(chat): switch useChat facade on flags.streaming (dark, default off)`
   — `features/chat/hooks/use-chat.ts`, `__tests__/use-chat.facade.test.tsx`.
6. `test(sse): add MSW SSE chat handler for integration/E2E`
   — `test/msw/handlers.ts`.

> Each commit message ends with the session trailer per repo convention. The whole milestone lands
> behind `NEXT_PUBLIC_FEATURE_STREAMING=false`; no commit flips the flag (that is M9).
