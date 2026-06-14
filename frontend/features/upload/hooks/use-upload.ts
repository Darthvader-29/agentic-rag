// features/upload/hooks/use-upload.ts
//
// Orchestrates the M8 presigned upload + ingestion-status polling for a single active upload, gated
// by flags.presignedUpload:
//   - Flag OFF -> the legacy multipart `uploadFile` (today's exact behavior, fire-and-forget).
//   - Flag ON  -> presign -> PUT direct-to-S3 (with progress) -> confirm, then poll
//     GET /api/documents/{id} via TanStack Query until a terminal status (ready|failed), firing one
//     terminal toast. The chat composer reads `active` to render inline progress.
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { flags } from "@/lib/flags";
import { uploadFile } from "@/features/chat/api/chat.api";
import {
  confirmIngestion,
  getDocumentStatus,
  getPresignedUrl,
  putToS3,
} from "@/features/upload/api/upload.api";
import {
  isTerminalStatus,
  type DocumentStatus,
} from "@/features/upload/api/upload.schemas";

const POLL_INTERVAL_MS = 2000;

export type UploadPhase =
  | "requesting"
  | "uploading"
  | "ingesting"
  | "ready"
  | "failed";

export interface ActiveUpload {
  filename: string;
  phase: UploadPhase;
  progress: number; // 0..100, meaningful during "uploading"
  documentId: string | null;
  status: DocumentStatus | null;
  error: string | null;
}

export interface UseUploadResult {
  /** Run an upload. Resolves once the object is enqueued (flag ON) / uploaded (flag OFF); rejects on failure. */
  upload: (file: File) => Promise<void>;
  /** The synchronous phase (presign -> PUT -> confirm, or the legacy upload) is in flight. */
  busy: boolean;
  presignedEnabled: boolean;
  /** The current/last presigned upload; always null on the flag-off path. */
  active: ActiveUpload | null;
}

function phaseForStatus(status: DocumentStatus): UploadPhase {
  if (status === "ready") return "ready";
  if (status === "failed") return "failed";
  return "ingesting";
}

export function useUpload(): UseUploadResult {
  const presignedEnabled = flags.presignedUpload;
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState<ActiveUpload | null>(null);
  const toastedFor = useRef<string | null>(null);

  const documentId = active?.documentId ?? null;
  // Poll only while a confirmed upload is still ingesting.
  const polling =
    presignedEnabled && active?.phase === "ingesting" && !!documentId;

  const statusQuery = useQuery({
    queryKey: ["document-status", documentId],
    queryFn: ({ signal }) => getDocumentStatus(documentId as string, signal),
    enabled: polling,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && isTerminalStatus(data.status)) return false; // stop on terminal
      return POLL_INTERVAL_MS;
    },
    refetchIntervalInBackground: false,
    staleTime: 0,
  });

  // Mirror the polled status into `active` and fire exactly one terminal toast.
  useEffect(() => {
    const data = statusQuery.data;
    if (!data) return;
    setActive((prev) =>
      prev && prev.documentId === data.id
        ? {
            ...prev,
            status: data.status,
            phase: phaseForStatus(data.status),
            error: data.error,
          }
        : prev
    );
    if (isTerminalStatus(data.status) && toastedFor.current !== data.id) {
      toastedFor.current = data.id;
      if (data.status === "ready") {
        toast.success(`${data.filename} ready`);
      } else {
        toast.error(
          `${data.filename} failed to ingest${data.error ? `: ${data.error}` : ""}`
        );
      }
    }
  }, [statusQuery.data]);

  const upload = useCallback(
    async (file: File): Promise<void> => {
      // ---- Flag OFF: today's behavior, byte-for-byte. ----
      if (!presignedEnabled) {
        setBusy(true);
        try {
          await uploadFile(file);
          toast.success(`${file.name} uploaded`);
        } catch (err) {
          toast.error("Upload failed");
          throw err instanceof Error ? err : new Error("Upload failed");
        } finally {
          setBusy(false);
        }
        return;
      }

      // ---- Flag ON: presign -> PUT(progress) -> confirm; polling continues after. ----
      setBusy(true);
      toastedFor.current = null;
      setActive({
        filename: file.name,
        phase: "requesting",
        progress: 0,
        documentId: null,
        status: null,
        error: null,
      });
      try {
        const presign = await getPresignedUrl(
          file.name,
          file.type || undefined
        );
        setActive((p) =>
          p ? { ...p, documentId: presign.document_id, phase: "uploading" } : p
        );
        await putToS3(presign.upload_url, file, {
          onProgress: (loaded, total) =>
            setActive((p) =>
              p
                ? {
                    ...p,
                    phase: "uploading",
                    progress: total
                      ? Math.round((loaded / total) * 100)
                      : p.progress,
                  }
                : p
            ),
        });
        await confirmIngestion(presign.document_id);
        setActive((p) => (p ? { ...p, phase: "ingesting", progress: 100 } : p));
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Upload failed";
        setActive((p) => (p ? { ...p, phase: "failed", error: msg } : p));
        toast.error(`${file.name}: ${msg}`);
        throw err instanceof Error ? err : new Error(msg);
      } finally {
        setBusy(false);
      }
    },
    [presignedEnabled]
  );

  return { upload, busy, presignedEnabled, active };
}
