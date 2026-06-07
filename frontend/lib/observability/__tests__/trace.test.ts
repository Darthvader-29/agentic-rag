import { describe, it, expect, beforeEach } from "vitest";
import {
  newTraceparent,
  traceIdFromTraceparent,
  getLastTraceId,
  setLastTraceId,
} from "@/lib/observability/trace";

const TRACEPARENT_RE = /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/;

describe("newTraceparent", () => {
  it("emits a well-formed W3C version-00 traceparent", () => {
    const tp = newTraceparent();
    expect(tp).toMatch(TRACEPARENT_RE);
  });

  it("produces unique trace ids across calls (CSPRNG, not constant)", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 50; i++) seen.add(newTraceparent());
    expect(seen.size).toBe(50);
  });

  it("trace-id is 32 hex chars and span-id is 16 hex chars", () => {
    const [, traceId, spanId] = newTraceparent().split("-");
    expect(traceId).toHaveLength(32);
    expect(spanId).toHaveLength(16);
    expect(traceId).toMatch(/^[0-9a-f]+$/);
    expect(spanId).toMatch(/^[0-9a-f]+$/);
  });
});

describe("traceIdFromTraceparent", () => {
  it("extracts the trace-id segment from a valid traceparent", () => {
    const tp = newTraceparent();
    const traceId = tp.split("-")[1];
    expect(traceIdFromTraceparent(tp)).toBe(traceId);
  });

  it("returns null for a malformed traceparent", () => {
    expect(traceIdFromTraceparent("not-a-traceparent")).toBeNull();
    expect(traceIdFromTraceparent("00-xyz-abc-01")).toBeNull();
    expect(traceIdFromTraceparent("")).toBeNull();
  });
});

describe("last-trace-id memory", () => {
  beforeEach(() => setLastTraceId(null));

  it("starts null", () => {
    expect(getLastTraceId()).toBeNull();
  });

  it("stores only the trace-id segment when given a full traceparent", () => {
    const tp = newTraceparent();
    const traceId = tp.split("-")[1];
    setLastTraceId(tp);
    expect(getLastTraceId()).toBe(traceId);
  });

  it("stores a bare 32-hex id verbatim", () => {
    const id = "a".repeat(32);
    setLastTraceId(id);
    expect(getLastTraceId()).toBe(id);
  });

  it("clears back to null", () => {
    setLastTraceId(newTraceparent());
    setLastTraceId(null);
    expect(getLastTraceId()).toBeNull();
  });
});
