// lib/observability/sentry.ts
//
// Thin, dependency-light wrapper around @sentry/nextjs (Phase 7, FE-3 — client-observability lane).
//
// DESIGN — Sentry must be a HARD NO-OP unless BOTH are true:
//   1. a DSN is configured  (env.NEXT_PUBLIC_SENTRY_DSN is a non-empty string), AND
//   2. the observability feature flag is ON  (flags.observability).
// We never call Sentry.init when either is missing, so:
//   - local/dev/test builds never need a DSN or upload token,
//   - capture* helpers become inert (they early-return before touching the SDK), and
//   - a stray import of this module can't accidentally start the SDK.
//
// This module is runtime-agnostic: @sentry/nextjs resolves to the browser / node / edge build via
// its conditional package exports, so the same `import * as Sentry from "@sentry/nextjs"` works in
// instrumentation.ts (server + edge `register()`) and instrumentation-client.ts (browser).

import * as Sentry from "@sentry/nextjs";
import type {
  ErrorEvent as SentryErrorEvent,
  Breadcrumb,
} from "@sentry/nextjs";

import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { getLastTraceId } from "@/lib/observability/trace";

// ---------------------------------------------------------------------------------------------
// PII scrubbing (H-F9).
//
// Sentry's default breadcrumbs (fetch URLs that carry session ids, console output, DOM text) plus
// any caller-supplied context can ship a user's email, their chat prompts, and the `rag_session_id`
// to a third party. We run every outgoing event AND breadcrumb through a redactor that:
//   - masks email addresses anywhere in a string,
//   - strips the `?next`/query string off captured request URLs, and redacts a `session_id` /
//     `rag_session_id` query param,
//   - drops known prompt/PII-bearing keys ("prompt", "message", "content", "answer", "query",
//     "email", "session_id", "rag_session_id", "access_token", "refresh_token", "authorization").
// Combined with `sendDefaultPii:false`, this keeps prompts/emails/session-ids out of the payload.
// ---------------------------------------------------------------------------------------------

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

/** Keys whose VALUE is dropped wholesale (case-insensitive) — prompts, identifiers, credentials. */
const REDACT_KEYS = new Set(
  [
    "prompt",
    "message",
    "messages",
    "content",
    "answer",
    "query",
    "text",
    "email",
    "session_id",
    "rag_session_id",
    "sessionid",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
  ].map((k) => k.toLowerCase())
);

const REDACTED = "[redacted]";

/** Mask emails inside an arbitrary string. */
function scrubString(value: string): string {
  return value.replace(EMAIL_RE, REDACTED);
}

/** Strip the query string off a URL but redact session ids even if the parse fails. */
function scrubUrl(url: string): string {
  try {
    const u = new URL(url, "http://local.invalid");
    // Drop the whole query (it routinely carries `?next=`, ids, tokens) but keep the path.
    return scrubString(`${u.origin === "http://local.invalid" ? "" : u.origin}${u.pathname}`);
  } catch {
    // Not a parseable URL — at least cut everything past the first `?` and mask emails.
    return scrubString(url.split("?")[0]);
  }
}

/**
 * Deep-redact an arbitrary value: drop sensitive keys, mask emails in strings, recurse into
 * arrays/objects. Bounded by `depth` so a cyclic/huge structure can't blow the stack. Returns a
 * NEW value (never mutates the input).
 */
function scrubValue(value: unknown, depth = 0): unknown {
  if (depth > 6) return REDACTED;
  if (typeof value === "string") return scrubString(value);
  if (Array.isArray(value)) return value.map((v) => scrubValue(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = REDACT_KEYS.has(k.toLowerCase())
        ? REDACTED
        : scrubValue(v, depth + 1);
    }
    return out;
  }
  return value;
}

/**
 * `beforeSend` hook: redact PII from an outgoing error event before it leaves the browser.
 * Exported for unit testing. Returns the (mutated copy of the) event, never null — we still want
 * the error, just scrubbed.
 */
export function scrubEvent(event: SentryErrorEvent): SentryErrorEvent {
  if (event.message) event.message = scrubString(event.message);
  if (event.request?.url) event.request.url = scrubUrl(event.request.url);
  if (event.request?.query_string) event.request.query_string = REDACTED;
  if (event.request?.headers) {
    event.request.headers = scrubValue(event.request.headers) as Record<
      string,
      string
    >;
  }
  if (event.request?.data !== undefined) {
    event.request.data = scrubValue(event.request.data);
  }
  // Never ship user PII (email/username/ip). Keep an opaque id if present for grouping.
  if (event.user) {
    event.user = event.user.id ? { id: String(event.user.id) } : {};
  }
  if (event.extra) {
    event.extra = scrubValue(event.extra) as Record<string, unknown>;
  }
  if (event.contexts) {
    event.contexts = scrubValue(event.contexts) as typeof event.contexts;
  }
  return event;
}

/**
 * `beforeSendBreadcrumb` hook: redact PII (esp. fetch/xhr URLs carrying session ids) from each
 * breadcrumb. Exported for unit testing.
 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb {
  if (breadcrumb.message) breadcrumb.message = scrubString(breadcrumb.message);
  if (breadcrumb.data) {
    const data = { ...breadcrumb.data };
    if (typeof data.url === "string") data.url = scrubUrl(data.url);
    breadcrumb.data = scrubValue(data) as Record<string, unknown>;
  }
  return breadcrumb;
}

/**
 * The single source of truth for "should Sentry do anything at all?". Guard EVERY Sentry call on
 * this. Kept as a function (not a const) so it re-reads `flags`/`env` — important for tests that
 * mock those modules per-case.
 */
export function isSentryEnabled(): boolean {
  return Boolean(env.NEXT_PUBLIC_SENTRY_DSN) && flags.observability === true;
}

/**
 * Resolve a Sentry "environment" tag. We don't expose a dedicated env var for this — the standard
 * NODE_ENV ("development" | "test" | "production") is enough to segment events in the dashboard.
 */
function sentryEnvironment(): string {
  return env.NODE_ENV;
}

/**
 * Shared base options for every runtime's `Sentry.init`. The instrumentation files spread this and
 * then add runtime-specific bits (e.g. browser replay). Centralizing the DSN, environment, and
 * sample rate keeps the three init sites consistent.
 *
 * `tracesSampleRate` defaults to a low-but-nonzero rate so production traces are sampled without
 * flooding quota; dev/test sample at 100% so a local trace is never silently dropped.
 */
export function baseSentryInitOptions(): {
  dsn: string;
  environment: string;
  tracesSampleRate: number;
  enabled: boolean;
  sendDefaultPii: boolean;
  beforeSend: (event: SentryErrorEvent) => SentryErrorEvent;
  beforeSendBreadcrumb: (breadcrumb: Breadcrumb) => Breadcrumb;
} {
  return {
    dsn: env.NEXT_PUBLIC_SENTRY_DSN ?? "",
    environment: sentryEnvironment(),
    // 100% in non-prod (local debugging); 10% in prod to stay within quota on demo traffic.
    tracesSampleRate: env.NODE_ENV === "production" ? 0.1 : 1.0,
    // Belt-and-suspenders: even if init were somehow reached without a DSN, `enabled:false`
    // makes the SDK inert. The real gate is the isSentryEnabled() check at every call site.
    enabled: Boolean(env.NEXT_PUBLIC_SENTRY_DSN),
    // PII scrubbing (H-F9): never attach default PII (IP, cookies, request bodies/headers the SDK
    // would otherwise infer), and redact emails/prompts/session-ids from every event + breadcrumb.
    sendDefaultPii: false,
    beforeSend: scrubEvent,
    beforeSendBreadcrumb: scrubBreadcrumb,
  };
}

/**
 * Initialize Sentry for the current runtime, but ONLY when {@link isSentryEnabled} is true.
 * Returns `true` if init ran, `false` if it was skipped (the no-op path). Callers in the
 * instrumentation files can ignore the return; tests assert on it.
 *
 * `extraOptions` lets a runtime add integrations/options (e.g. the browser passes replay).
 */
export function initSentry(
  extraOptions: Record<string, unknown> = {}
): boolean {
  if (!isSentryEnabled()) return false;
  Sentry.init({ ...baseSentryInitOptions(), ...extraOptions });
  return true;
}

/**
 * Report an error to Sentry. No-op (returns `undefined`) when Sentry is disabled, so feature code
 * can call this unconditionally without leaking events in dev/test or when the flag is off.
 *
 * Stamps the current W3C trace id (from lib/observability/trace) as a `trace_id` tag when present,
 * so a captured exception can be cross-referenced with the chat turn's traceparent.
 */
export function captureError(
  error: unknown,
  context?: Record<string, unknown>
): string | undefined {
  if (!isSentryEnabled()) return undefined;
  return Sentry.captureException(error, (scope) => {
    const traceId = getLastTraceId();
    if (traceId) scope.setTag("trace_id", traceId);
    if (context) scope.setContext("extra", context);
    return scope;
  });
}

/**
 * Send a breadcrumb-style message event to Sentry. No-op when disabled.
 */
export function captureMessage(
  message: string,
  level: "info" | "warning" | "error" = "info"
): string | undefined {
  if (!isSentryEnabled()) return undefined;
  return Sentry.captureMessage(message, level);
}

/**
 * Stamp the current trace id (or an explicitly supplied one) as a Sentry `trace_id` tag on the
 * active scope, so subsequent events correlate to the in-flight chat turn. No-op when disabled.
 */
export function setTraceTag(traceId?: string | null): void {
  if (!isSentryEnabled()) return;
  const id = traceId ?? getLastTraceId();
  if (id) Sentry.setTag("trace_id", id);
}
