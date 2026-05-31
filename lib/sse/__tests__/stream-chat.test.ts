import { describe, it, expect, vi, afterEach } from "vitest";
import { streamChat } from "@/lib/sse/stream-chat";
import { streamFromChunks } from "@/test/utils/mock-stream";

function mockFetchOnce(
  body: ReadableStream<Uint8Array> | null,
  ok = true,
  status = 200
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({ ok, status, body, json: async () => ({}) }) as unknown as Response
    )
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
      ])
    );
    const stages: string[] = [];
    let body = "";
    let done: { answer: string } | null = null;

    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      {
        onStatus: (s) => stages.push(s),
        onToken: (t) => (body += t),
        onDone: (d) => (done = d),
      }
    );

    expect(stages).toEqual(["routing"]);
    expect(body).toBe("Grounded answer.");
    expect(done!.answer).toBe("Grounded answer.");
  });

  it("fires onComponent for a component event and maps the flat done.route", async () => {
    mockFetchOnce(
      streamFromChunks([
        'event: token\ndata: {"text": "Hi"}\n\n',
        'event: component\ndata: {"type": "citation", "items": [{"label": "doc.pdf · p.4"}]}\n\n',
        'event: done\ndata: {"answer": "Hi", "route": "BOTH"}\n\n',
      ])
    );
    const components: Array<{ type: string }> = [];
    let done: { answer: string; route: unknown } | null = null;

    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onComponent: (c) => components.push(c), onDone: (d) => (done = d) }
    );

    expect(components).toHaveLength(1);
    expect(components[0].type).toBe("citation");
    expect(done!.route).toBe("BOTH"); // flat enum surfaced raw; mapRoute → "WEB+RAG" in the hook
  });

  it("drops an invalid component block without throwing (degrades to prose-only)", async () => {
    mockFetchOnce(
      streamFromChunks([
        'event: component\ndata: {"type": "definitely-not-a-catalog-type"}\n\n',
        'event: done\ndata: {"answer": "Hi", "route": "RAG"}\n\n',
      ])
    );
    const onComponent = vi.fn();
    let done: { answer: string } | null = null;
    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onComponent, onDone: (d) => (done = d) }
    );
    expect(onComponent).not.toHaveBeenCalled(); // invalid type dropped, never thrown
    expect(done!.answer).toBe("Hi"); // stream still completes cleanly
  });

  it("reports a typed error event via onError and stops", async () => {
    mockFetchOnce(
      streamFromChunks(['event: error\ndata: {"detail": "boom"}\n\n'])
    );
    const onError = vi.fn();
    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onError }
    );
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ message: "boom" })
    );
  });

  it("surfaces a non-ok HTTP response (auth/rate-limit before stream) via onError", async () => {
    mockFetchOnce(null, false, 429);
    const onError = vi.fn();
    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onError }
    );
    expect(onError).toHaveBeenCalled();
  });

  it("swallows AbortError as a clean stop (no onError)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      })
    );
    const onError = vi.fn();
    await streamChat(
      { message: "q", session_id: "s", web_search_allowed: false },
      { onError }
    );
    expect(onError).not.toHaveBeenCalled();
  });
});
