// features/upload/api/upload.schemas.ts
//
// Zod contracts for the M8 presigned-upload flow, reconciled against the ACTUAL backend
// (backend/app.py), NOT the M8 plan doc (which drifted pre-security-fix):
//   - POST /api/upload (json branch) -> { document_id, upload_url, s3_key, session_id }
//   - POST /api/upload/confirm takes { document_id } ONLY — the server derives the s3_key from the
//     OWNED document row (tenant-isolation fix); a client-supplied key is never trusted.
//   - GET /api/documents/{id} -> { id, filename, status, s3_key, session_id, error? }
// DocumentStatus of record is pending|processing|ready|failed; we also accept "complete" and
// normalize it to "ready" to be robust to backend-doc drift.
import { z } from "zod";

export const RawDocumentStatusSchema = z.enum([
  "pending",
  "processing",
  "ready",
  "complete", // tolerated backend-doc drift; normalized to "ready" below
  "failed",
]);
export type RawDocumentStatus = z.infer<typeof RawDocumentStatusSchema>;

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export function normalizeStatus(raw: RawDocumentStatus): DocumentStatus {
  return raw === "complete" ? "ready" : raw;
}

const TERMINAL: ReadonlySet<DocumentStatus> = new Set(["ready", "failed"]);
/** Terminal statuses stop the status poll. */
export const isTerminalStatus = (s: DocumentStatus): boolean => TERMINAL.has(s);

// Step 1 — POST /api/upload (json branch). session_id attaches the document to the active chat
// session so RAG retrieval can see it (mirrors the legacy multipart path's session_id form field).
export const PresignRequestSchema = z.object({
  filename: z.string().min(1),
  content_type: z.string().min(1).optional(),
  session_id: z.string().min(1).optional(),
});
export type PresignRequest = z.infer<typeof PresignRequestSchema>;

export const PresignResponseSchema = z.object({
  document_id: z.string().min(1),
  upload_url: z.string().url(),
  s3_key: z.string().min(1),
  session_id: z.string().min(1),
});
export type PresignResponse = z.infer<typeof PresignResponseSchema>;

// Step 3 — POST /api/upload/confirm. Body is { document_id } ONLY; the backend derives the key.
export const ConfirmRequestSchema = z.object({
  document_id: z.string().min(1),
});
export type ConfirmRequest = z.infer<typeof ConfirmRequestSchema>;

export const ConfirmResponseSchema = z.object({
  document_id: z.string().min(1),
  status: z.string(), // "queued"; not load-bearing client-side
});
export type ConfirmResponse = z.infer<typeof ConfirmResponseSchema>;

// Step 4 — GET /api/documents/{id}. Unknown keys are ignored; `error` is optional/nullable.
export const DocumentRecordSchema = z.object({
  id: z.string().min(1),
  filename: z.string().min(1),
  status: RawDocumentStatusSchema,
  s3_key: z.string().optional(),
  session_id: z.string().optional(),
  error: z.string().nullish(),
});
export type DocumentRecordRaw = z.infer<typeof DocumentRecordSchema>;

/** Normalized record the UI consumes. */
export interface DocumentRecord {
  id: string;
  filename: string;
  status: DocumentStatus;
  error: string | null;
}

export function toDocumentRecord(raw: DocumentRecordRaw): DocumentRecord {
  return {
    id: raw.id,
    filename: raw.filename,
    status: normalizeStatus(raw.status),
    error: raw.error ?? null,
  };
}
