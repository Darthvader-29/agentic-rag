// instrumentation-client.ts
//
// Next.js 16 CLIENT-side instrumentation (Phase 7, FE-3 — client-observability lane). Next loads
// this module in the browser before hydration, which is where Sentry's browser SDK must init.
//
// HARD NO-OP: initSentry() only runs when BOTH a DSN (NEXT_PUBLIC_SENTRY_DSN) AND
// flags.observability are present; otherwise nothing initializes and no router-transition hook is
// exported with any effect. So a build without Sentry secrets ships zero browser instrumentation.

import * as Sentry from "@sentry/nextjs";

import { env } from "@/lib/env";
import { initSentry, isSentryEnabled } from "@/lib/observability/sentry";

// Build the browser-only integration set. Performance tracing is always useful; Session Replay is
// added at a sampled rate ONLY in production (it's heavy and unnecessary for local debugging).
function browserIntegrations() {
  const integrations: unknown[] = [Sentry.browserTracingIntegration()];
  if (env.NODE_ENV === "production") {
    integrations.push(
      Sentry.replayIntegration({
        // Don't capture text/inputs by default — privacy-safe replay for a public demo.
        maskAllText: true,
        blockAllMedia: true,
      })
    );
  }
  return integrations;
}

// Initialize Sentry in the browser. We gate on isSentryEnabled() FIRST so that — when Sentry is
// off — we never even construct the integration objects (no SDK work happens at all on the
// disabled path). When enabled, we add tracing + sampled replay on top of the shared base options.
if (isSentryEnabled()) {
  initSentry({
    integrations: browserIntegrations(),
    // Sample a small fraction of normal sessions and all errored sessions for replay (prod only;
    // these are ignored when replayIntegration isn't registered).
    replaysSessionSampleRate: env.NODE_ENV === "production" ? 0.05 : 0,
    replaysOnErrorSampleRate: env.NODE_ENV === "production" ? 1.0 : 0,
  });
}

// App Router navigation instrumentation. Sentry 10 exposes captureRouterTransitionStart to record
// client-side route changes as spans; Next calls this exported hook on each navigation. We export
// a guarded wrapper so it is a true no-op when Sentry is disabled (and never references an
// uninitialized SDK).
export function onRouterTransitionStart(
  ...args: Parameters<typeof Sentry.captureRouterTransitionStart>
): void {
  if (!isSentryEnabled()) return;
  Sentry.captureRouterTransitionStart(...args);
}
