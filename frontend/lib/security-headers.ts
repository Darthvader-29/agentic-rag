// lib/security-headers.ts
//
// Security response headers for the Next.js app, applied via next.config.ts `headers()` (R07).
// Pure + dependency-free so it can be unit-tested and imported by the config without side effects.
//
// CSP keeps 'unsafe-inline' for script/style: Next injects inline bootstrap + hydration scripts and
// inline styles, which—without per-request nonces—require it. Tightening to a nonce-based policy
// (via middleware) is a documented follow-up. Everything else is locked down (no framing, no
// objects, no cross-origin base/form, an explicit connect-src allow-list).

export interface SecurityHeaderOptions {
  /** NEXT_PUBLIC_API_URL — its origin is allow-listed in connect-src (fetch + SSE go there). */
  apiUrl?: string;
  /** Dev relaxes script-src ('unsafe-eval' for HMR) + connect-src (HMR websocket); HSTS is prod-only. */
  isDev: boolean;
}

function apiOrigin(apiUrl?: string): string {
  try {
    return apiUrl ? new URL(apiUrl).origin : "";
  } catch {
    return ""; // unset / malformed → just omit it (connect-src still has 'self')
  }
}

export function buildCsp({ apiUrl, isDev }: SecurityHeaderOptions): string {
  const connectSrc = [
    "'self'",
    apiOrigin(apiUrl), // API fetch + SSE stream
    "https://*.sentry.io", // Sentry ingest (only used when observability is on)
    "https://vitals.vercel-insights.com", // Vercel Analytics beacons
    ...(isDev ? ["ws:", "wss:"] : []), // Next dev HMR websocket
  ]
    .filter(Boolean)
    .join(" ");

  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    `connect-src ${connectSrc}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ].join("; ");
}

export function securityHeaders(
  opts: SecurityHeaderOptions
): { key: string; value: string }[] {
  return [
    { key: "Content-Security-Policy", value: buildCsp(opts) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=()",
    },
    // HSTS only in production (served over HTTPS) — never on plain-http dev.
    ...(opts.isDev
      ? []
      : [
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains",
          },
        ]),
  ];
}
