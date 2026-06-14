// features/upload/components/upload-status.tsx
//
// Inline status for the single active presigned upload, rendered above the chat composer (flag ON
// only). Shows a progress bar during the S3 PUT, then a processing spinner, then a terminal
// ready/failed label. Fed by the `active` state from useUpload (one hook instance owns it).
"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ActiveUpload } from "@/features/upload/hooks/use-upload";

const PHASE_LABEL: Record<ActiveUpload["phase"], string> = {
  requesting: "Preparing upload…",
  uploading: "Uploading",
  ingesting: "Processing…",
  ready: "Ready",
  failed: "Failed",
};

export function UploadStatus({ active }: { active: ActiveUpload }) {
  const { filename, phase, progress, error } = active;
  const inFlight =
    phase === "requesting" || phase === "uploading" || phase === "ingesting";

  return (
    <div
      className="text-muted-foreground flex items-center gap-2 px-2 text-xs"
      role="status"
      aria-live="polite"
    >
      {inFlight && <Loader2 className="h-3 w-3 shrink-0 animate-spin" />}
      <span className="max-w-[40%] truncate font-medium">{filename}</span>
      {phase === "uploading" ? (
        <span className="flex flex-1 items-center gap-2">
          <span className="bg-muted h-1 flex-1 overflow-hidden rounded-full">
            <span
              className="bg-primary block h-full transition-[width] motion-reduce:transition-none"
              style={{ width: `${progress}%` }}
            />
          </span>
          <span className="w-9 text-right tabular-nums">{progress}%</span>
        </span>
      ) : (
        <span
          className={cn(
            phase === "ready" && "text-emerald-600 dark:text-emerald-400",
            phase === "failed" && "text-destructive"
          )}
        >
          {PHASE_LABEL[phase]}
          {phase === "failed" && error ? `: ${error}` : ""}
        </span>
      )}
    </div>
  );
}
