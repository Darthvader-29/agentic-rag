import { z } from "zod";

const FeatureFlag = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");

// Default-ON flags (M9 streaming, M10 rich components): the matching backend P6 surface is
// live, so these capabilities ship enabled. An operator can still keep one env-gated by
// setting it to "false" (streaming → blocking path; rich components → raw-JSON fallback).
const FeatureFlagDefaultOn = z
  .enum(["true", "false"])
  .default("true")
  .transform((v) => v === "true");

const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url().default("http://localhost:8000/api"),

  NEXT_PUBLIC_FEATURE_STREAMING: FeatureFlagDefaultOn,
  NEXT_PUBLIC_FEATURE_AUTH: FeatureFlag,
  NEXT_PUBLIC_FEATURE_BYOK: FeatureFlag,
  NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD: FeatureFlag,

  // Rich component rendering (table/chart/citation/code/callout/media) is ON by default as of
  // M10 — the P6 `component` SSE event is live. Set NEXT_PUBLIC_FEATURE_RICH_COMPONENTS=false to
  // keep it dark (each component degrades to a collapsed raw-JSON block inside the M3 code-block).
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: FeatureFlagDefaultOn,

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
