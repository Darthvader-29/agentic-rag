// lib/observability/trace.ts
//
// Shared W3C Trace Context helpers (Phase 7, FE-3 builds on this). A `traceparent` lets the
// backend correlate one chat turn across systems (logs / Sentry / APM). We attach it to the
// chat fetch ONLY when `flags.observability` is on (see lib/sse/stream-chat.ts).
//
// SECURITY: trace/span ids MUST be generated from the Web Crypto API
// (`crypto.getRandomValues`) — never `Math.random()` or any non-cryptographic PRNG. Predictable
// trace ids could let an attacker guess/forge correlation ids or probe internal request flows.
//
// traceparent wire format (W3C Trace Context, version 00):
//   00-<32 lowercase hex: trace-id>-<16 lowercase hex: parent/span-id>-01
//   │   │                          │                                    └─ trace-flags (01 = sampled)
//   │   │                          └─ 8-byte span id
//   │   └─ 16-byte trace id
//   └─ version

/** version byte (only "00" is defined today) */
const VERSION = "00";
/** trace-flags: 01 = "sampled" (we always sample our own demo traffic) */
const FLAGS = "01";

/**
 * Fill a Uint8Array with cryptographically-strong random bytes. Throws if Web Crypto is
 * unavailable (SSR without the global, ancient runtime) — callers gate on flags.observability,
 * and our runtimes (modern browsers, Node ≥ 18 via globalThis.crypto) always provide it.
 */
function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  // globalThis.crypto is the Web Crypto API in browsers AND Node ≥ 18; getRandomValues is CSPRNG.
  const webcrypto = globalThis.crypto;
  if (!webcrypto || typeof webcrypto.getRandomValues !== "function") {
    throw new Error(
      "Web Crypto API unavailable: cannot generate a secure traceparent."
    );
  }
  webcrypto.getRandomValues(bytes);
  return bytes;
}

/** Lowercase hex encoding of `length` CSPRNG bytes ⇒ a 2*length-char hex string. */
function randomHex(length: number): string {
  const bytes = randomBytes(length);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * Generate a fresh W3C `traceparent`: "00-<32 hex>-<16 hex>-01".
 * The trace id (16 bytes ⇒ 32 hex) and span id (8 bytes ⇒ 16 hex) are CSPRNG-derived.
 */
export function newTraceparent(): string {
  const traceId = randomHex(16); // 16 bytes → 32 hex chars
  const spanId = randomHex(8); // 8 bytes  → 16 hex chars
  return `${VERSION}-${traceId}-${spanId}-${FLAGS}`;
}

/**
 * Extract the 32-hex trace-id segment from a traceparent string, or null if it isn't a
 * well-formed version-00 traceparent. Used to stamp `MessageStats.traceId`.
 */
export function traceIdFromTraceparent(traceparent: string): string | null {
  const parts = traceparent.split("-");
  // version, trace-id, span-id, flags
  if (parts.length !== 4) return null;
  const [, traceId] = parts;
  if (!/^[0-9a-f]{32}$/.test(traceId)) return null;
  return traceId;
}

// ---- last-trace-id memory --------------------------------------------------------------------
// A tiny module-scope cell remembering the most recent trace id we generated. FE-3 can read it
// for ad-hoc cross-referencing (e.g. a "copy trace id" affordance) without threading it through
// props. Not persisted — it resets on reload, which is fine for a correlation aid.

let lastTraceId: string | null = null;

/** Remember the last trace context. Accepts a full traceparent OR a bare 32-hex trace id. */
export function setLastTraceId(traceparentOrId: string | null): void {
  if (traceparentOrId === null) {
    lastTraceId = null;
    return;
  }
  // Tolerate being handed a full traceparent: store only the trace-id segment.
  lastTraceId = traceparentOrId.includes("-")
    ? traceIdFromTraceparent(traceparentOrId)
    : traceparentOrId;
}

/** Read the last trace id (32-hex), or null if none has been generated this session. */
export function getLastTraceId(): string | null {
  return lastTraceId;
}
