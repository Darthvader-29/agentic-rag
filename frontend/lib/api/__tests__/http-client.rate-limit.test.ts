import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

// Auth off → no refresh dance; keeps these focused on the 429 envelope handling (R27).
vi.mock("@/lib/flags", () => ({ flags: { auth: false, streaming: false } }));

import { request } from "@/lib/api/http-client";
import {
  ApiError,
  RATE_LIMITED_MESSAGE,
  isApiError,
} from "@/lib/api/api-error";

const okSchema = z.unknown();

function stub429(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 429,
      json: async () => body,
    })) as unknown as typeof fetch
  );
}

describe("http-client — 429 rate-limit handling (R27)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces the FE's friendly message for the backend {detail, code} envelope", async () => {
    stub429({ detail: "Too many requests. Please slow down.", code: "rate_limited" });

    const err = await request("/chat", {
      method: "POST",
      schema: okSchema,
    }).catch((e) => e);

    expect(isApiError(err)).toBe(true);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).kind).toBe("rate_limited");
    expect((err as ApiError).isRateLimited).toBe(true);
    // Always the friendly copy — not "Backend error: 429".
    expect((err as ApiError).userMessage).toBe(RATE_LIMITED_MESSAGE);
    expect((err as ApiError).userMessage).not.toMatch(/backend error/i);
  });

  it("also handles slowapi's raw {error} envelope (un-updated backend)", async () => {
    stub429({ error: "Rate limit exceeded: 30 per 1 minute" });

    const err = await request("/chat", {
      method: "POST",
      schema: okSchema,
    }).catch((e) => e);

    expect(isApiError(err)).toBe(true);
    expect((err as ApiError).kind).toBe("rate_limited");
    expect((err as ApiError).userMessage).toBe(RATE_LIMITED_MESSAGE);
  });
});
