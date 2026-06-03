// features/memory/api/memory.schemas.ts
//
// Zod contract for the Phase-7 conversation-memory endpoint (BACKEND CONTRACT, authoritative):
//
//   GET /api/sessions/{sessionId}/memory
//     200 -> { session_id: string, content: string (markdown), updated_at: string ISO8601 }
//     404 -> "no memory yet" (handled in memory.api.ts, NOT here — a 404 never reaches parse)
//
// The schema is the FRONTEND GUARDIAN of the memory contract: a well-formed 200 body that drifts
// (missing `content`, non-string `updated_at`) fails `safeParse` in the http-client and surfaces as
// a parse ApiError rather than rendering garbage. It is intentionally tolerant only where the
// contract is genuinely optional:
//   - `updated_at` is nullable so the synthesized "empty memory" object (`{content:'', updated_at:null}`)
//     the api layer returns on a 404 conforms to the SAME type the UI consumes — one shape everywhere.
//   - `session_id` is optional: it's echo metadata the panel never needs (it already knows the id it
//     asked for), so a leaner backend response still validates.
import { z } from "zod";

export const SessionMemorySchema = z.object({
  // Echo of the requested session id. Optional — the caller already holds the id; absence is harmless.
  session_id: z.string().optional(),
  // Markdown body. Always a string on the wire; the 404 path synthesizes "" so callers never branch
  // on undefined.
  content: z.string(),
  // ISO-8601 timestamp of the last memory write, or null when there is no memory yet (404 synthesis).
  updated_at: z.string().nullable(),
});

export type SessionMemory = z.infer<typeof SessionMemorySchema>;

/**
 * The canonical "no memory yet" value. Returned by the api layer on a 404 and reused as the query's
 * empty baseline so the panel renders its empty state without special-casing `undefined`.
 */
export const EMPTY_MEMORY: SessionMemory = {
  content: "",
  updated_at: null,
};
