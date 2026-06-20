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
    // PII scrubbing (H-F9): default PII off + a beforeSend/beforeSendBreadcrumb scrubber wired in.
    expect(opts.sendDefaultPii).toBe(false);
    expect(typeof opts.beforeSend).toBe("function");
    expect(typeof opts.beforeSendBreadcrumb).toBe("function");
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

// ---- PII scrubbing (H-F9 / R11) --------------------------------------------------------------

describe("beforeSend scrubs PII from an outgoing event", () => {
  it("masks emails, drops prompt/session keys, strips request query, and minimizes user", async () => {
    const { scrubEvent } = await loadSentry();

    const event = {
      message: "failed for ada@example.com while sending",
      request: {
        url: "https://app.example/api/chat?next=/secret&session_id=abc123",
        query_string: "session_id=abc123",
        headers: { authorization: "Bearer secrettoken", "x-trace": "ok" },
        data: { message: "my private prompt", note: "ping bob@corp.io" },
      },
      user: { id: "user-1", email: "ada@example.com", ip_address: "1.2.3.4" },
      extra: {
        prompt: "another private prompt",
        rag_session_id: "sess-xyz",
        safe: "keep me",
      },
      contexts: { custom: { email: "carol@example.com" } },
    } as unknown as Parameters<typeof scrubEvent>[0];

    const out = scrubEvent(event);

    // email masked in the message
    expect(out.message).not.toContain("ada@example.com");
    expect(out.message).toContain("[redacted]");
    // request url query stripped (no ?next / session_id leakage), path kept
    expect(out.request?.url).toBe("https://app.example/api/chat");
    expect(out.request?.query_string).toBe("[redacted]");
    // authorization header redacted, benign header kept
    const headers = out.request?.headers as Record<string, string>;
    expect(headers.authorization).toBe("[redacted]");
    expect(headers["x-trace"]).toBe("ok");
    // prompt-bearing body key dropped; remaining string still email-masked
    const data = out.request?.data as Record<string, string>;
    expect(data.message).toBe("[redacted]");
    expect(data.note).not.toContain("bob@corp.io");
    // user minimized to opaque id only (no email / ip)
    expect(out.user).toEqual({ id: "user-1" });
    // extra: sensitive keys dropped, benign value preserved
    const extra = out.extra as Record<string, unknown>;
    expect(extra.prompt).toBe("[redacted]");
    expect(extra.rag_session_id).toBe("[redacted]");
    expect(extra.safe).toBe("keep me");
    // nested contexts scrubbed too
    expect(JSON.stringify(out.contexts)).not.toContain("carol@example.com");
  });
});

describe("beforeSendBreadcrumb scrubs PII from a breadcrumb", () => {
  it("strips the query (session ids) off a fetch URL and masks emails", async () => {
    const { scrubBreadcrumb } = await loadSentry();
    const crumb = {
      category: "fetch",
      message: "GET for ada@example.com",
      data: {
        url: "https://app.example/api/sessions/abc/memory?session_id=sess-1",
        method: "GET",
      },
    } as unknown as Parameters<typeof scrubBreadcrumb>[0];

    const out = scrubBreadcrumb(crumb);
    const data = out.data as Record<string, string>;
    expect(data.url).toBe("https://app.example/api/sessions/abc/memory");
    expect(data.url).not.toContain("session_id");
    expect(out.message).not.toContain("ada@example.com");
  });
});
