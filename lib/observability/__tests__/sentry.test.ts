import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------------------------
// Mutable holders the per-test cases flip, then re-import the SUT (sentry.ts reads `env`/`flags`
// through these mocked modules). This lets one file exercise every DSN × flag combination.
// ---------------------------------------------------------------------------------------------
const envHolder = {
  NEXT_PUBLIC_SENTRY_DSN: undefined as string | undefined,
  NODE_ENV: "test" as "development" | "test" | "production",
};
const flagsHolder = { observability: false as boolean };

vi.mock("@/lib/env", () => ({
  get env() {
    return envHolder;
  },
}));
vi.mock("@/lib/flags", () => ({
  get flags() {
    return flagsHolder;
  },
}));

// Spy on the Sentry SDK surface our wrapper touches. The explicit generic on `vi.fn<...>()` gives
// `.mock.calls` the right argument tuples for the assertions below (without unused named params).
// captureException/captureMessage return an event-id string.
const initSpy = vi.fn<(options: Record<string, unknown>) => void>();
const captureExceptionSpy = vi
  .fn<(error: unknown, captureContext?: unknown) => string>()
  .mockReturnValue("evt-id");
const captureMessageSpy = vi
  .fn<(message: string, level?: unknown) => string>()
  .mockReturnValue("msg-id");
const setTagSpy = vi.fn<(key: string, value: unknown) => void>();
vi.mock("@sentry/nextjs", () => ({
  init: initSpy,
  captureException: captureExceptionSpy,
  captureMessage: captureMessageSpy,
  setTag: setTagSpy,
}));

// Re-import a fresh copy of the SUT so it observes the current holder values.
async function loadSentry() {
  vi.resetModules();
  return import("@/lib/observability/sentry");
}

beforeEach(() => {
  vi.clearAllMocks();
  envHolder.NEXT_PUBLIC_SENTRY_DSN = undefined;
  envHolder.NODE_ENV = "test";
  flagsHolder.observability = false;
});

// ---- the explicit FE-3 requirements ----------------------------------------------------------

describe("traceparent format (FE-3 contract)", () => {
  const TRACEPARENT_RE = /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/;

  it("newTraceparent() matches /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/", async () => {
    const { newTraceparent } = await import("@/lib/observability/trace");
    expect(newTraceparent()).toMatch(TRACEPARENT_RE);
  });
});

describe("Sentry init is skipped without a DSN", () => {
  it("does NOT init when the DSN is absent, even with observability ON", async () => {
    flagsHolder.observability = true;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = undefined;
    const { initSentry, isSentryEnabled } = await loadSentry();

    expect(isSentryEnabled()).toBe(false);
    expect(initSentry()).toBe(false);
    expect(initSpy).not.toHaveBeenCalled();
  });

  it("does NOT init when the DSN is an empty string", async () => {
    flagsHolder.observability = true;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = "";
    const { initSentry } = await loadSentry();

    expect(initSentry()).toBe(false);
    expect(initSpy).not.toHaveBeenCalled();
  });
});

describe("Sentry init is skipped when the flag is OFF", () => {
  it("does NOT init when observability is false, even with a DSN present", async () => {
    flagsHolder.observability = false;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    const { initSentry, isSentryEnabled } = await loadSentry();

    expect(isSentryEnabled()).toBe(false);
    expect(initSentry()).toBe(false);
    expect(initSpy).not.toHaveBeenCalled();
  });
});

describe("Sentry init runs only when BOTH a DSN and the flag are present", () => {
  it("inits with the DSN, environment, and a numeric tracesSampleRate", async () => {
    flagsHolder.observability = true;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    envHolder.NODE_ENV = "production";
    const { initSentry, isSentryEnabled } = await loadSentry();

    expect(isSentryEnabled()).toBe(true);
    expect(initSentry({ integrations: [] })).toBe(true);
    expect(initSpy).toHaveBeenCalledTimes(1);

    const opts = initSpy.mock.calls[0][0] as Record<string, unknown>;
    expect(opts.dsn).toBe("https://abc@o1.ingest.sentry.io/1");
    expect(opts.environment).toBe("production");
    expect(typeof opts.tracesSampleRate).toBe("number");
    // extraOptions are merged through.
    expect(opts.integrations).toEqual([]);
  });

  it("samples 100% of traces outside production", async () => {
    flagsHolder.observability = true;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
    envHolder.NODE_ENV = "development";
    const { baseSentryInitOptions } = await loadSentry();
    expect(baseSentryInitOptions().tracesSampleRate).toBe(1.0);
  });
});

// ---- helper no-op behavior -------------------------------------------------------------------

describe("capture* helpers are inert when Sentry is disabled", () => {
  it("captureError returns undefined and never calls the SDK when disabled", async () => {
    // disabled: DSN absent
    const { captureError } = await loadSentry();
    expect(captureError(new Error("boom"))).toBeUndefined();
    expect(captureExceptionSpy).not.toHaveBeenCalled();
  });

  it("captureMessage returns undefined and never calls the SDK when disabled", async () => {
    const { captureMessage } = await loadSentry();
    expect(captureMessage("hi")).toBeUndefined();
    expect(captureMessageSpy).not.toHaveBeenCalled();
  });

  it("setTraceTag is a no-op when disabled", async () => {
    const { setTraceTag } = await loadSentry();
    setTraceTag("a".repeat(32));
    expect(setTagSpy).not.toHaveBeenCalled();
  });
});

describe("capture* helpers forward to the SDK when enabled", () => {
  beforeEach(() => {
    flagsHolder.observability = true;
    envHolder.NEXT_PUBLIC_SENTRY_DSN = "https://abc@o1.ingest.sentry.io/1";
  });

  it("captureError forwards the error and returns the event id", async () => {
    const { captureError } = await loadSentry();
    const err = new Error("kaboom");
    expect(captureError(err)).toBe("evt-id");
    expect(captureExceptionSpy).toHaveBeenCalledTimes(1);
    expect(captureExceptionSpy.mock.calls[0][0]).toBe(err);
  });

  it("captureError stamps the last trace id as a scope tag", async () => {
    const { captureError } = await loadSentry();
    const { setLastTraceId } = await import("@/lib/observability/trace");
    const traceId = "b".repeat(32);
    setLastTraceId(traceId);

    captureError(new Error("with-trace"));
    // second arg is the scope-callback; run it against a fake scope and assert the tag.
    type FakeScope = {
      setTag: (k: string, v: unknown) => unknown;
      setContext: (k: string, v: unknown) => unknown;
    };
    const scopeCb = captureExceptionSpy.mock.calls[0][1] as unknown as (
      s: FakeScope
    ) => unknown;
    const setTag = vi.fn();
    const setContext = vi.fn();
    scopeCb({ setTag, setContext });
    expect(setTag).toHaveBeenCalledWith("trace_id", traceId);

    setLastTraceId(null);
  });

  it("setTraceTag stamps an explicit trace id", async () => {
    const { setTraceTag } = await loadSentry();
    const id = "c".repeat(32);
    setTraceTag(id);
    expect(setTagSpy).toHaveBeenCalledWith("trace_id", id);
  });
});
