import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

// Timeout behavior is orthogonal to auth; keep the interceptor dormant for these tests.
vi.mock("@/lib/flags", () => ({ flags: { auth: false, streaming: false } }));

import { request, DEFAULT_TIMEOUT_MS } from "@/lib/api/http-client";
import { ApiError } from "@/lib/api/api-error";

const okSchema = z.unknown();

/**
 * A fetch that NEVER resolves on its own — it only settles by rejecting with an AbortError when
 * its `signal` aborts, exactly like the real fetch does when the connection is torn down. This
 * models a hung backend that accepted the socket but never responds.
 */
function installHangingFetch() {
  const calls: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init: RequestInit) => {
      const signal = init.signal as AbortSignal;
      calls.push(signal);
      return new Promise<Response>((_resolve, reject) => {
        if (signal.aborted) {
          reject(new DOMException("aborted", "AbortError"));
          return;
        }
        signal.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true }
        );
      });
    })
  );
  return calls;
}

describe("http-client request timeout (R18 / H-F6)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("a never-resolving request rejects with a timeout error after the timeout elapses", async () => {
    installHangingFetch();

    const p = request("/chat", {
      method: "POST",
      schema: okSchema,
      timeoutMs: 5_000,
    });
    // Attach the rejection handler synchronously so there is no unhandled rejection while we
    // advance the clock; the promise is still pending until the timer fires.
    const settled = p.then(
      () => ({ ok: true as const }),
      (e: unknown) => ({ ok: false as const, err: e })
    );

    // Before the timeout: still pending (no resolution yet).
    await vi.advanceTimersByTimeAsync(4_999);
    // Cross the timeout boundary → the AbortController fires → fetch rejects → timeout ApiError.
    await vi.advanceTimersByTimeAsync(2);

    const outcome = await settled;
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.err).toBeInstanceOf(ApiError);
      expect((outcome.err as ApiError).kind).toBe("timeout");
      expect((outcome.err as ApiError).status).toBe(0);
    }
  });

  it("exposes a sane default timeout and disables the timeout when timeoutMs is 0", async () => {
    expect(DEFAULT_TIMEOUT_MS).toBeGreaterThan(0);

    installHangingFetch();
    let settled = false;
    const p = request("/chat", {
      method: "POST",
      schema: okSchema,
      timeoutMs: 0,
    }).then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      }
    );

    // With the timeout disabled, advancing well past the default must NOT settle the promise.
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS * 2);
    expect(settled).toBe(false);

    // Cleanup: nothing will ever resolve this; leave it dangling (the test process tears down).
    void p;
  });

  it("a caller-initiated abort propagates as an AbortError, not a timeout", async () => {
    installHangingFetch();
    const controller = new AbortController();

    const settled = request("/chat", {
      method: "POST",
      schema: okSchema,
      signal: controller.signal,
      timeoutMs: 60_000,
    }).then(
      () => ({ aborted: false as const }),
      (e: unknown) => ({ aborted: true as const, err: e })
    );

    // User presses Stop well before the timeout.
    controller.abort();
    await vi.advanceTimersByTimeAsync(1);

    const outcome = await settled;
    expect(outcome.aborted).toBe(true);
    if (outcome.aborted) {
      // Re-thrown as the original AbortError DOMException (callers ignore these), NOT an ApiError.
      expect(outcome.err).toBeInstanceOf(DOMException);
      expect((outcome.err as DOMException).name).toBe("AbortError");
    }
  });
});
