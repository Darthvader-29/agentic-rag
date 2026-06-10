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
  stream: ReadableStream<Uint8Array>
): AsyncGenerator<ParsedSseEvent, void, unknown> {
  // Stateful streaming UTF-8 decode: a codepoint split across byte-chunks is
  // buffered internally and only emitted once complete.
  // Cast required: TypeScript's lib types for TextDecoderStream.writable are BufferSource
  // but ReadableStream<Uint8Array>.pipeThrough expects Uint8Array. At runtime these are
  // compatible; the cast avoids the lib mismatch without changing behaviour.
  const textStream = stream.pipeThrough(
    new TextDecoderStream() as unknown as TransformStream<Uint8Array, string>
  );
  const reader = textStream.getReader();

  let buffer = "";
  // True when the previous chunk ended with a bare "\r" — its paired "\n" (if any) may arrive at
  // the start of the next chunk. Tracked across reads so a CRLF straddling a chunk boundary isn't
  // mis-normalised into a spurious "\n\n" frame terminator (B23).
  let pendingCR = false;

  try {
    for (;;) {
      const { value, done } = await reader.read();

      if (value) {
        let chunk = value;
        // Drop a leading "\n" that pairs with the previous chunk's trailing "\r" (already turned
        // into "\n" below). Without this, a CRLF split across reads becomes "\n\n" and fabricates
        // a frame boundary mid-frame, dropping/mis-attributing the event.
        if (pendingCR && chunk.startsWith("\n")) chunk = chunk.slice(1);
        pendingCR = value.endsWith("\r");
        // Normalise CRLF / CR to LF so "\n\n" framing is uniform.
        buffer += chunk.replace(/\r\n?/g, "\n");

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
