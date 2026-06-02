import { z } from "zod";

const FeatureFlag = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");

// Streaming is ON by default as of M9 — the real P6 SSE backend is live. An operator can
// still keep it env-gated by setting NEXT_PUBLIC_FEATURE_STREAMING=false to fall back to the
// blocking path (the useChat facade switches strategies on this flag).
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
  NEXT_PUBLIC_FEATURE_RICH_COMPONENTS: FeatureFlag,

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
