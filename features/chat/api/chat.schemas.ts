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
