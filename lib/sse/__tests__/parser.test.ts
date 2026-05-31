import { describe, it, expect } from "vitest";
import { parseSSE } from "@/lib/sse/parser";
import {
  streamFromChunks,
  streamFromByteChunks,
} from "@/test/utils/mock-stream";

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
      ])
    );
    expect(events.map((e) => e.event)).toEqual(["status", "token", "done"]);
    expect(events[0].data).toBe('{"stage": "routing"}');
  });

  it("passes a component frame through verbatim (event-name-agnostic)", async () => {
    const events = await collect(
      streamFromChunks([
        'event: token\ndata: {"text": "Hi"}\n\n',
        'event: component\ndata: {"type": "citation", "items": [{"label": "doc.pdf · p.4"}]}\n\n',
        'event: done\ndata: {"answer": "Hi", "route": "RAG"}\n\n',
      ])
    );
    expect(events.map((e) => e.event)).toEqual(["token", "component", "done"]);
    expect(JSON.parse(events[1].data).type).toBe("citation");
  });

  it("joins multiple data: lines with \\n", async () => {
    const events = await collect(
      streamFromChunks(["event: token\ndata: line1\ndata: line2\n\n"])
    );
    expect(events[0].data).toBe("line1\nline2");
  });

  it("reassembles a single frame split across two reads (partial buffer)", async () => {
    const events = await collect(
      streamFromChunks(["event: tok", 'en\ndata: {"text": "x"}', "\n\n"])
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "x"}' }]);
  });

  it("stops cleanly on the [DONE] sentinel and ignores anything after", async () => {
    const events = await collect(
      streamFromChunks([
        'event: token\ndata: {"text": "a"}\n\n',
        "data: [DONE]\n\n",
        'event: token\ndata: {"text": "b"}\n\n', // must NOT be yielded
      ])
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "a"}' }]);
  });

  it("ignores keep-alive comment lines", async () => {
    const events = await collect(
      streamFromChunks([
        ": keep-alive\n\n",
        'event: token\ndata: {"text": "y"}\n\n',
      ])
    );
    expect(events.map((e) => e.event)).toEqual(["token"]);
  });

  it("tolerates a malformed line without a colon", async () => {
    const events = await collect(
      streamFromChunks(["garbage-no-colon\nevent: token\ndata: ok\n\n"])
    );
    expect(events).toEqual([{ event: "token", data: "ok" }]);
  });

  it("flushes a trailing frame with no terminating blank line", async () => {
    const events = await collect(
      streamFromChunks(['event: token\ndata: {"text": "z"}'])
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "z"}' }]);
  });

  it("normalises CRLF line endings", async () => {
    const events = await collect(
      streamFromChunks(['event: token\r\ndata: {"text": "w"}\r\n\r\n'])
    );
    expect(events).toEqual([{ event: "token", data: '{"text": "w"}' }]);
  });

  it("does not corrupt a multibyte codepoint split across byte reads", async () => {
    // "🚀" (U+1F680) is 4 UTF-8 bytes: F0 9F 9A 80. Split it across two reads.
    const full = new TextEncoder().encode(
      'event: token\ndata: {"text": "🚀"}\n\n'
    );
    const splitAt = full.indexOf(0x80); // mid-emoji byte boundary
    const events = await collect(
      streamFromByteChunks([full.slice(0, splitAt), full.slice(splitAt)])
    );
    expect(JSON.parse(events[0].data).text).toBe("🚀");
  });
});
