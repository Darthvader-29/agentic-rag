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
export function streamFromByteChunks(
  byteChunks: Uint8Array[]
): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const bytes of byteChunks) controller.enqueue(bytes);
      controller.close();
    },
  });
}
