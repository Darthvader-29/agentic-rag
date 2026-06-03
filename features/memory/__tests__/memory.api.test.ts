// features/memory/__tests__/memory.api.test.ts
//
// Exercises the REAL http-client + Zod parse path for fetchSessionMemory against MSW-mocked HTTP.
// A self-contained setupServer (msw/node) lives in this file — the repo's shared MSW server harness
// (test/msw/server.ts) isn't wired yet and HARD RULE forbids touching shared files, so this lane
// stands up its own server. MSW v2 intercepts at the network layer, so memory.api → http-client →
// Zod runs end-to-end for real.
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

// Auth flag OFF for these tests ⇒ the request is the plain anonymous GET (no Bearer, no refresh).
import { env } from "@/lib/env";
import { fetchSessionMemory } from "@/features/memory/api/memory.api";
import { EMPTY_MEMORY } from "@/features/memory/api/memory.schemas";

const SESSION_ID = "sess-123";
const MEMORY_URL = `${env.NEXT_PUBLIC_API_URL}/sessions/:sessionId/memory`;

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("fetchSessionMemory", () => {
  it("fetches and parses a 200 memory body", async () => {
    const body = {
      session_id: SESSION_ID,
      content: "## Summary\n\n- user likes **cats**",
      updated_at: "2026-06-03T10:00:00.000Z",
    };
    server.use(
      http.get(MEMORY_URL, ({ params }) => {
        expect(params.sessionId).toBe(SESSION_ID);
        return HttpResponse.json(body);
      })
    );

    const result = await fetchSessionMemory(SESSION_ID);
    expect(result).toEqual(body);
  });

  it("tolerates a missing session_id and a null updated_at (lean/empty backend body)", async () => {
    server.use(
      http.get(MEMORY_URL, () =>
        HttpResponse.json({ content: "", updated_at: null })
      )
    );

    const result = await fetchSessionMemory(SESSION_ID);
    expect(result.content).toBe("");
    expect(result.updated_at).toBeNull();
    expect(result.session_id).toBeUndefined();
  });

  it("treats 404 as 'no memory yet' → EMPTY_MEMORY (no throw)", async () => {
    server.use(
      http.get(MEMORY_URL, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 })
      )
    );

    const result = await fetchSessionMemory(SESSION_ID);
    expect(result).toEqual(EMPTY_MEMORY);
    expect(result).toEqual({ content: "", updated_at: null });
  });

  it("propagates a 500 as an ApiError (NOT swallowed like 404)", async () => {
    server.use(
      http.get(MEMORY_URL, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })
      )
    );

    await expect(fetchSessionMemory(SESSION_ID)).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    });
  });

  it("rejects a contract-drifting 200 body (missing `content`) as a parse error", async () => {
    server.use(
      http.get(MEMORY_URL, () =>
        HttpResponse.json({ updated_at: "2026-06-03T10:00:00.000Z" })
      )
    );

    await expect(fetchSessionMemory(SESSION_ID)).rejects.toMatchObject({
      name: "ApiError",
      kind: "parse",
    });
  });

  it("URL-encodes the session id into the path", async () => {
    let captured = "";
    server.use(
      http.get(
        `${env.NEXT_PUBLIC_API_URL}/sessions/:sessionId/memory`,
        ({ request }) => {
          captured = new URL(request.url).pathname;
          return HttpResponse.json({ content: "x", updated_at: null });
        }
      )
    );

    await fetchSessionMemory("a/b c");
    // "a/b c" → "a%2Fb%20c" (slash and space escaped) so the segment can't break the route.
    expect(captured).toContain("a%2Fb%20c");
  });
});
