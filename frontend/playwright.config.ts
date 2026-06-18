import { defineConfig, devices } from "@playwright/test";

// R21 / M5 E2E. Playwright builds the real Next app and serves the production artifact, then runs
// the chromium suite against it. The "backend" is stubbed at the network layer with Playwright's
// own `page.route()` inside each spec (see e2e/chat.e2e.ts) — no real server, no MSW worker to
// register, fully deterministic. Streaming is forced OFF for the default flow so the blocking
// `POST /chat` path is exercised (today's shipping behavior; matches the M5 milestone spec).

const PORT = 3000;
const baseURL = `http://localhost:${PORT}`;

// `NEXT_PUBLIC_*` vars are inlined at BUILD time, so they must be set on the webServer command (the
// build runs there), not just at run time. The API base is pinned so the spec's route globs have a
// stable origin to match (`**/api/chat`, `**/api/upload`, `**/api/cleanup`).
const buildEnv = {
  NEXT_PUBLIC_API_URL: `${baseURL}/api`,
  NEXT_PUBLIC_FEATURE_STREAMING: "false", // default flow is blocking
  NEXT_PUBLIC_FEATURE_AUTH: "false", // frictionless: no guest mint / login wall in E2E
  NEXT_PUBLIC_FEATURE_BYOK: "false", // hide the model picker (no provider keys in E2E)
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: "false", // legacy multipart upload (synthetic confirm msg)
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: "false",
  NEXT_TELEMETRY_DISABLED: "1",
};

export default defineConfig({
  testDir: "./e2e",
  // Match `*.e2e.ts` (NOT the default `*.spec.ts` / `*.test.ts`). The shared `vitest.config.ts`
  // collects every `**/*.{test,spec}.ts` from the repo root with no `e2e/**` exclude, so a
  // `chat.spec.ts` here would be swept into the unit run and fail (Playwright's `test`/`expect`
  // aren't Vitest's). The `.e2e.ts` suffix keeps the two runners cleanly partitioned without
  // editing the out-of-scope vitest config.
  testMatch: "**/*.e2e.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI, // no stray .only() in CI
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry", // trace-viewer artifact on flake
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Build once, then serve the production app. Reuse a locally-running server outside CI.
  webServer: {
    command: "npm run build && npm run start",
    url: baseURL,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
    env: buildEnv,
  },
});
