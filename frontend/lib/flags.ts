import { env } from "@/lib/env";

/**
 * Feature flags gate forward-compatible surfaces so unfinished backend phases
 * ship dark. Each flag is consumed by exactly one later milestone:
 *
 *   streaming        -> M2 (seam); M9 flips ON by default (backend P6 SSE is live)
 *   auth             -> M6 (backend P3 JWT auth + login/register)
 *   byok             -> M7 flips true (backend P4 multi-provider BYOK + model picker)
 *   presignedUpload  -> M8 (backend P5 presigned S3 uploads + status polling)
 *   richComponents   -> M10 (backend P6 rich-output component event); flips ON by default
 *   memory           -> Phase 7 (conversation-memory panel); DEFAULT OFF
 *   knowledgeGraph   -> Phase 7 (knowledge-graph panel, lazy react-force-graph); DEFAULT OFF
 *   observability    -> Phase 7 (per-turn stats, traceparent, stats panel, analytics); DEFAULT OFF
 */
export const flags = {
  streaming: env.NEXT_PUBLIC_FEATURE_STREAMING,
  auth: env.NEXT_PUBLIC_FEATURE_AUTH,
  byok: env.NEXT_PUBLIC_FEATURE_BYOK,
  presignedUpload: env.NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD,
  richComponents: env.NEXT_PUBLIC_FEATURE_RICH_COMPONENTS,
  memory: env.NEXT_PUBLIC_FEATURE_MEMORY,
  knowledgeGraph: env.NEXT_PUBLIC_FEATURE_KNOWLEDGE_GRAPH,
  observability: env.NEXT_PUBLIC_FEATURE_OBSERVABILITY,
} as const;

export type Flags = typeof flags;

/**
 * R24 — BYOK depends on auth. Saving/listing keys is Bearer-guarded, so with `auth` OFF the
 * BYOK surface (model picker, free-tier banner + exhausted dialog, the Settings key form, and
 * the sidebar "API Keys" link) can't deliver a usable capability — Settings dead-ends at "Sign
 * in to add keys" and `/login` mints nothing. Every BYOK surface gates on this derived flag so
 * the default config (`auth=false`, `byok=true`) advertises nothing it can't provide.
 *
 * Exposed as a function (not a const) so it re-reads `flags` after a test mock swaps the module.
 */
export function isByokEnabled(): boolean {
  return flags.byok && flags.auth;
}
