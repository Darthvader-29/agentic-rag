// features/upload/api/upload.api.ts
//
// Network layer for the M8 presigned-upload flow. Backend calls go through the typed http-client
// and attach the bearer exactly like the rest of the app (only when flags.auth is live — matching
// the legacy uploadFile path, NOT the M8 doc's hardcoded `auth: true`). The S3 PUT is the ONE
// exception: a raw XMLHttpRequest, because fetch() has no upload-progress API and because an
// Authorization header sent to S3 would break the presigned signature.
import { request } from "@/lib/api/http-client";
import { flags } from "@/lib/flags";
import { getSessionId } from "@/features/chat/api/chat.api";
import {
  ConfirmRequestSchema,
  ConfirmResponseSchema,
  DocumentRecordSchema,
  PresignRequestSchema,
  PresignResponseSchema,
  toDocumentRecord,
  type ConfirmResponse,
  type DocumentRecord,
  type PresignResponse,
} from "./upload.schemas";

/** Step 1: ask the backend for a presigned PUT URL (attaches the active session id). */
export async function getPresignedUrl(
  filename: string,
  contentType?: string,
  signal?: AbortSignal
): Promise<PresignResponse> {
  const body = PresignRequestSchema.parse({
    filename,
    content_type: contentType,
    session_id: getSessionId(),
  });
  return request<PresignResponse>("/upload", {
    method: "POST",
    body,
    schema: PresignResponseSchema,
    auth: flags.auth,
    signal,
  });
}

export interface PutToS3Options {
  onProgress?: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

/**
 * Step 2: PUT raw bytes DIRECTLY to S3, reporting progress. Uses XMLHttpRequest because fetch() has
 * no upload-progress API. NEVER attach Authorization here — the signature is in the URL and an auth
 * header breaks it. The File is streamed (a disk-backed blob), never read into memory.
 */
export function putToS3(
  uploadUrl: string,
  file: File,
  opts: PutToS3Options = {}
): Promise<void> {
  const { onProgress, signal } = opts;
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", uploadUrl, true);
    xhr.setRequestHeader(
      "Content-Type",
      file.type || "application/octet-stream"
    );

    xhr.upload.onprogress = (e: ProgressEvent) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Storage upload failed (${xhr.status})`));
    };
    xhr.onerror = () =>
      reject(new Error("Storage upload network error (check bucket CORS)"));
    xhr.onabort = () =>
      reject(new DOMException("Upload aborted", "AbortError"));

    if (signal) {
      if (signal.aborted) {
        xhr.abort();
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(file);
  });
}

/** Step 3: confirm the object landed; the backend head_object-verifies and enqueues ingestion. */
export async function confirmIngestion(
  documentId: string,
  signal?: AbortSignal
): Promise<ConfirmResponse> {
  const body = ConfirmRequestSchema.parse({ document_id: documentId });
  return request<ConfirmResponse>("/upload/confirm", {
    method: "POST",
    body,
    schema: ConfirmResponseSchema,
    auth: flags.auth,
    signal,
  });
}

/** Step 4: read a document's current ingestion status (normalized for the UI). */
export async function getDocumentStatus(
  documentId: string,
  signal?: AbortSignal
): Promise<DocumentRecord> {
  const raw = await request("/documents/" + encodeURIComponent(documentId), {
    method: "GET",
    schema: DocumentRecordSchema,
    auth: flags.auth,
    signal,
  });
  return toDocumentRecord(raw);
}
