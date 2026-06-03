// instrumentation.ts
//
// Next.js 16 server-side instrumentation hook (Phase 7, FE-3 — client-observability lane).
//
// `register()` runs ONCE per server runtime as the process boots (Node.js for the main server,
// "edge" for middleware / edge routes). We initialize Sentry for whichever runtime is starting.
// `onRequestError` is Next's hook for uncaught errors in server components / route handlers — we
// forward those to Sentry via the SDK's purpose-built capture.
//
// HARD NO-OP: every path here is gated behind initSentry()/isSentryEnabled(), which require BOTH a
// configured DSN (NEXT_PUBLIC_SENTRY_DSN) AND flags.observability. With either missing, register()
// does nothing and onRequestError never touches the SDK — so a build with no Sentry secrets is
// completely inert.

import { initSentry, isSentryEnabled } from "@/lib/observability/sentry";

/**
 * Next.js calls this once per server runtime at startup. We branch on NEXT_RUNTIME so the Node and
 * Edge runtimes each get a Sentry.init (the @sentry/nextjs import already resolves to the correct
 * per-runtime build via conditional package exports).
 */
export async function register(): Promise<void> {
  // Gate first: skip all work (and the @sentry/nextjs init) when Sentry is disabled.
  if (!isSentryEnabled()) return;

  const runtime = process.env.NEXT_RUNTIME;
  if (runtime === "nodejs" || runtime === "edge") {
    // Same base options for both server runtimes; no browser-only integrations here.
    initSentry();
  }
}

/**
 * Next.js `onRequestError` instrumentation hook: forward server-side request errors to Sentry.
 *
 * We lazy-import `@sentry/nextjs` INSIDE the guard so the SDK's request-error capture is only
 * loaded/invoked when Sentry is actually enabled — keeping the disabled path a true no-op. The
 * signature is intentionally permissive (Next's exact param types vary across minor versions);
 * `captureRequestError` accepts these three positional args.
 */
export async function onRequestError(
  ...args: Parameters<typeof import("@sentry/nextjs").captureRequestError>
): Promise<void> {
  if (!isSentryEnabled()) return;
  const Sentry = await import("@sentry/nextjs");
  Sentry.captureRequestError(...args);
}
