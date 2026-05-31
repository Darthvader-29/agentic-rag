"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface UseCopyToClipboardOptions {
  /** ms before `copied` resets to false. Default 2000. */
  timeout?: number;
  /** show a sonner toast on success. Default true. */
  showToast?: boolean;
}

interface UseCopyToClipboardReturn {
  copied: boolean;
  copy: (text: string) => Promise<boolean>;
}

export function useCopyToClipboard({
  timeout = 2000,
  showToast = true,
}: UseCopyToClipboardOptions = {}): UseCopyToClipboardReturn {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      if (!text) return false;
      if (typeof navigator === "undefined" || !navigator.clipboard) {
        if (showToast) toast.error("Clipboard not available");
        return false;
      }
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        if (showToast) toast.success("Copied to clipboard");
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), timeout);
        return true;
      } catch {
        if (showToast) toast.error("Failed to copy");
        return false;
      }
    },
    [timeout, showToast]
  );

  return { copied, copy };
}
