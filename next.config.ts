import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  devIndicators: false,
};

// ---- Sentry (Phase 7, FE-3) ------------------------------------------------------------------
// withSentryConfig wires the build-time Sentry plugin (source-map upload + tunneling). It must be
// a NO-OP / passthrough locally: source-map upload needs SENTRY_AUTH_TOKEN (+ org/project), and a
// missing DSN means there's nothing to instrument. So we only wrap when the DSN is present AND an
// auth token exists; otherwise we export the plain config so a local/dev build NEVER fails for a
// missing Sentry secret. (NEXT_PUBLIC_SENTRY_DSN is the client DSN; SENTRY_AUTH_TOKEN/ORG/PROJECT
// are server-only deploy secrets, intentionally NOT NEXT_PUBLIC_.)
const sentryEnabled =
  Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN) &&
  Boolean(process.env.SENTRY_AUTH_TOKEN);

const config: NextConfig = sentryEnabled
  ? withSentryConfig(nextConfig, {
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      authToken: process.env.SENTRY_AUTH_TOKEN,
      // Quiet the plugin in CI; it logs nothing useful when uploads are skipped.
      silent: !process.env.CI,
      // Upload a wider set of source maps but hide them from the public bundle.
      widenClientFileUpload: true,
      // Avoid leaking source via the browser; delete maps after upload.
      sourcemaps: { deleteSourcemapsAfterUpload: true },
      // Tree-shake Sentry logger statements in production for a smaller client bundle.
      disableLogger: true,
    })
  : nextConfig;

export default config;
