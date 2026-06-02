import { z } from "zod";

export const routeTypeSchema = z.enum([
  "RAG",
  "WEB",
  "DIRECT",
  "WEB+RAG",
  "DIRECT+WEB",
  "DIRECT+RAG",
  "ERROR",
]);

export const chatRequestSchema = z.object({
  message: z.string(),
  session_id: z.string(),
  web_search_allowed: z.boolean(),
  // M7: optional per-conversation BYOK provider/model. Omitted when the picker is untouched
  // ⇒ the backend uses its own default (free Gemini tier). The picker constrains `provider`
  // to the catalog; keep the request schema tolerant (string) so the contract stays loose.
  provider: z.enum(["gemini", "openai", "anthropic"]).optional(),
  model: z.string().optional(),
});

export const chatResponseSchema = z.object({
  answer: z.string(),
  route: routeTypeSchema,
  context_count: z.number().int().nonnegative(),
  session_id: z.string().optional(),
});

export const uploadResponseSchema = z
  .object({
    status: z.string().optional(),
    s3_key: z.string().optional(),
  })
  .passthrough();

export const cleanupResponseSchema = z
  .object({ status: z.string().optional() })
  .passthrough();

export type RouteType = z.infer<typeof routeTypeSchema>;
export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type UploadResponse = z.infer<typeof uploadResponseSchema>;
export type CleanupResponse = z.infer<typeof cleanupResponseSchema>;

// ---- M2: SSE event payload schemas ----

/** event: status  →  data: {"stage": "routing" | ...} */
export const SseStatusSchema = z.object({
  stage: z.enum([
    "routing",
    "retrieving",
    "searching web",
    "synthesizing",
    "done",
    "error",
  ]),
});
export type SseStatus = z.infer<typeof SseStatusSchema>;

/** event: token  →  data: {"text": "..."} */
export const SseTokenSchema = z.object({
  text: z.string(),
});
export type SseToken = z.infer<typeof SseTokenSchema>;

/**
 * done.route — flat enum from 09 Appendix A (authoritative); legacy 07 object tolerated
 * defensively while the backend ships (reconciled in M9).
 */
export const SseFlatRouteSchema = z.enum(["RAG", "WEB", "BOTH", "DIRECT"]);
export const SseLegacyRouteSchema = z.object({
  destination: z.string(),
  relevant: z.boolean().optional(),
});
export const SseRouteSchema = z.union([
  SseFlatRouteSchema,
  SseLegacyRouteSchema,
]);
export type SseRoute = z.infer<typeof SseRouteSchema>;

/** event: done  →  data: {"answer": "...", "route": "RAG"|"WEB"|"BOTH"|"DIRECT"} */
export const SseDoneSchema = z.object({
  answer: z.string(),
  route: SseRouteSchema.nullable().optional(),
});
export type SseDone = z.infer<typeof SseDoneSchema>;

/**
 * event: component  →  data: {"type": "table"|"chart"|"citation"|"code"|"callout"|"media", ...}
 *
 * M2 validates the catalog `type` only and passes extra fields through (.passthrough()).
 * The strict per-type discriminated union lives in M10.
 * An unknown/invalid type is dropped, never thrown.
 */
export const SseComponentSchema = z
  .object({
    type: z.enum(["table", "chart", "citation", "code", "callout", "media"]),
  })
  .passthrough();
export type SseComponent = z.infer<typeof SseComponentSchema>;

/**
 * The machine-readable error code carried by the freemium guard (docs/09 §3 / contract
 * Appendix C). The contract names `free_tier_exhausted`; the field is a tolerant string so
 * an unknown future code still parses (the UI branches only on this known value to show
 * the BYOK upsell). M7 introduces the code; M9 owns the richer streaming-error surface.
 */
export const FREE_TIER_EXHAUSTED = "free_tier_exhausted" as const;

/**
 * event: error  →  data: {"detail": "...", "code"?: "free_tier_exhausted"}
 *
 * `code` is part of the contract: it distinguishes the free-tier-exhausted BYOK-upsell
 * case from a generic error. The SAME shape is reused for the pre-stream HTTP 4xx JSON
 * body so both delivery paths funnel through one schema.
 */
export const SseErrorSchema = z.object({
  detail: z.string(),
  code: z.string().optional(),
});
export type SseError = z.infer<typeof SseErrorSchema>;
