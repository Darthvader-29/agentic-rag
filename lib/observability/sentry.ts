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

import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { getLastTraceId } from "@/lib/observability/trace";

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
} {
  return {
    dsn: env.NEXT_PUBLIC_SENTRY_DSN ?? "",
    environment: sentryEnvironment(),
    // 100% in non-prod (local debugging); 10% in prod to stay within quota on demo traffic.
    tracesSampleRate: env.NODE_ENV === "production" ? 0.1 : 1.0,
    // Belt-and-suspenders: even if init were somehow reached without a DSN, `enabled:false`
    // makes the SDK inert. The real gate is the isSentryEnabled() check at every call site.
    enabled: Boolean(env.NEXT_PUBLIC_SENTRY_DSN),
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
