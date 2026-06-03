import { z } from "zod";

const FeatureFlag = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");

// Default-ON flags (M9 streaming, M7 BYOK, M10 rich components): the matching backend phase
// is live, so these capabilities ship enabled. An operator can still keep one env-gated by
// setting it to "false": STREAMING=false falls back to the blocking path (the useChat facade
// switches strategies on the flag); BYOK=false hides the Settings route, picker, disclaimer,
// and upsell (chat falls back to the free tier); RICH_COMPONENTS=false degrades each block to
// a collapsed raw-JSON fallback.
const FeatureFlagDefaultOn = z
  .enum(["true", "false"])
  .default("true")
  .transform((v) => v === "true");

const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url().default("http://localhost:8000/api"),

  NEXT_PUBLIC_FEATURE_STREAMING: FeatureFlagDefaultOn,
  NEXT_PUBLIC_FEATURE_AUTH: FeatureFlag,
  NEXT_PUBLIC_FEATURE_BYOK: FeatureFlagDefaultOn,
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: FeatureFlag,

  // Rich component rendering (table/chart/citation/code/callout/media) is ON by default as of
  // M10 — the P6 `component` SSE event is live. Set NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false to
  // keep it dark (each component degrades to a collapsed raw-JSON block inside the M3 code-block).
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: FeatureFlagDefaultOn,

  // ---- Phase 7 (memory & advanced observability) — DEFAULT OFF -------------------------------
  // Each gates a forward-compat surface whose backend endpoint is still being built, so they
  // ship dark and degrade cleanly (render nothing / empty state) on a 404/error.
  //   memory          -> GET /api/sessions/{id}/memory  (conversation memory panel)
  //   knowledgeGraph  -> GET /api/sessions/{id}/graph   (lazy react-force-graph panel, ssr:false)
  //   observability   -> per-turn MessageStats + traceparent header + stats panel + analytics
  NEXT_PUBLIC_FEATURE_MEMORY: FeatureFlag,
  NEXT_PUBLIC_FEATURE_KNOWLEDGE_GRAPH: FeatureFlag,
  NEXT_PUBLIC_FEATURE_OBSERVABILITY: FeatureFlag,

  // Optional Sentry DSN. Absent ⇒ Sentry is a no-op (withSentryConfig passthrough, no client
  // init), so local/dev builds never fail for a missing DSN or auth token.
  NEXT_PUBLIC_SENTRY_DSN: z.string().optional(),

  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
});

const parsed = envSchema.safeParse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_FEATURE_STREAMING: process.env.NEXT_PUBLIC_FEATURE_STREAMING,
  NEXT_PUBLIC_FEATURE_AUTH: process.env.NEXT_PUBLIC_FEATURE_AUTH,
  NEXT_PUBLIC_FEATURE_BYOK: process.env.NEXT_PUBLIC_FEATURE_BYOK,
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD:
    process.env.NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD,
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS:
    process.env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS,
  NEXT_PUBLIC_FEATURE_MEMORY: process.env.NEXT_PUBLIC_FEATURE_MEMORY,
  NEXT_PUBLIC_FEATURE_KNOWLEDGE_GRAPH:
    process.env.NEXT_PUBLIC_FEATURE_KNOWLEDGE_GRAPH,
  NEXT_PUBLIC_FEATURE_OBSERVABILITY:
    process.env.NEXT_PUBLIC_FEATURE_OBSERVABILITY,
  NEXT_PUBLIC_SENTRY_DSN: process.env.NEXT_PUBLIC_SENTRY_DSN,
  NODE_ENV: process.env.NODE_ENV,
});

if (!parsed.success) {
  console.error(
    "❌ Invalid environment variables:",
    parsed.error.flatten().fieldErrors
  );
  throw new Error("Invalid environment variables. See logs above.");
}

export const env = parsed.data;
export type Env = typeof env;
