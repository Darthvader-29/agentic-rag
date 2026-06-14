// Gating + orchestration tests for the M8 presigned-upload hook. Repo convention (see
// features/memory/__tests__): mock flags + the api modules, drive the hook through a throwaway
// QueryClient. The real fetch/XHR path is exercised manually / by the schema tests.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Toggle the presigned flag per test via a mutable getter (matches use-session-memory.test.tsx).
let presignedUpload = true;
vi.mock("@/lib/flags", () => ({
  get flags() {
    return { presignedUpload, auth: false, streaming: false };
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (m: string) => toastSuccess(m),
    error: (m: string) => toastError(m),
  },
}));

// Legacy multipart fallback (flag OFF).
const uploadFile = vi.fn();
vi.mock("@/features/chat/api/chat.api", () => ({
  uploadFile: (f: File) => uploadFile(f),
  getSessionId: () => "s1",
}));

// Presigned network layer (flag ON).
const getPresignedUrl = vi.fn();
const putToS3 = vi.fn();
const confirmIngestion = vi.fn();
const getDocumentStatus = vi.fn();
vi.mock("@/features/upload/api/upload.api", () => ({
  getPresignedUrl: (...a: unknown[]) => getPresignedUrl(...a),
  putToS3: (...a: unknown[]) => putToS3(...a),
  confirmIngestion: (...a: unknown[]) => confirmIngestion(...a),
  getDocumentStatus: (...a: unknown[]) => getDocumentStatus(...a),
}));

import { useUpload } from "@/features/upload/hooks/use-upload";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { wrapper };
}

const file = new File(["data"], "report.pdf", { type: "application/pdf" });

beforeEach(() => {
  presignedUpload = true;
  uploadFile.mockReset();
  getPresignedUrl.mockReset();
  putToS3.mockReset();
  confirmIngestion.mockReset();
  getDocumentStatus.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe("useUpload — flag OFF (legacy fallback parity)", () => {
  it("uses the multipart uploadFile and never touches the presigned flow", async () => {
    presignedUpload = false;
    uploadFile.mockResolvedValue(undefined);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpload(), { wrapper });

    await act(async () => {
      await result.current.upload(file);
    });

    expect(uploadFile).toHaveBeenCalledWith(file);
    expect(getPresignedUrl).not.toHaveBeenCalled();
    expect(putToS3).not.toHaveBeenCalled();
    expect(confirmIngestion).not.toHaveBeenCalled();
    expect(toastSuccess).toHaveBeenCalledWith("report.pdf uploaded");
    expect(result.current.active).toBeNull();
    expect(result.current.presignedEnabled).toBe(false);
  });

  it("toasts an error and rejects when the legacy upload fails", async () => {
    presignedUpload = false;
    uploadFile.mockRejectedValue(new Error("boom"));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpload(), { wrapper });

    await expect(
      act(async () => {
        await result.current.upload(file);
      })
    ).rejects.toThrow();
    expect(toastError).toHaveBeenCalledWith("Upload failed");
  });
});

describe("useUpload — flag ON (presigned flow)", () => {
  it("runs presign -> PUT(progress) -> confirm, then polls to ready", async () => {
    getPresignedUrl.mockResolvedValue({
      document_id: "doc1",
      upload_url: "https://s3.test/put/doc1",
      s3_key: "uploads/u/doc1.pdf",
      session_id: "s1",
    });
    putToS3.mockImplementation(
      (
        _url: string,
        _file: File,
        opts?: { onProgress?: (l: number, t: number) => void }
      ) => {
        opts?.onProgress?.(50, 100);
        opts?.onProgress?.(100, 100);
        return Promise.resolve();
      }
    );
    confirmIngestion.mockResolvedValue({
      document_id: "doc1",
      status: "queued",
    });
    getDocumentStatus.mockResolvedValue({
      id: "doc1",
      filename: "report.pdf",
      status: "ready",
      error: null,
    });

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpload(), { wrapper });

    await act(async () => {
      await result.current.upload(file);
    });

    // Orchestration ran in order; the legacy path did not.
    expect(getPresignedUrl).toHaveBeenCalledTimes(1);
    expect(putToS3).toHaveBeenCalledTimes(1);
    expect(confirmIngestion).toHaveBeenCalledWith("doc1");
    expect(uploadFile).not.toHaveBeenCalled();
    expect(result.current.active?.documentId).toBe("doc1");

    // Polling resolves the terminal status + fires one success toast.
    await waitFor(() => expect(result.current.active?.phase).toBe("ready"));
    expect(getDocumentStatus).toHaveBeenCalledWith("doc1", expect.anything());
    expect(toastSuccess).toHaveBeenCalledWith("report.pdf ready");
  });

  it("marks the upload failed (and never confirms) when the S3 PUT throws", async () => {
    getPresignedUrl.mockResolvedValue({
      document_id: "doc2",
      upload_url: "https://s3.test/put/doc2",
      s3_key: "uploads/u/doc2.pdf",
      session_id: "s1",
    });
    putToS3.mockRejectedValue(new Error("Storage upload failed (403)"));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpload(), { wrapper });

    // Catch INSIDE act so it resolves and React commits the post-throw "failed" state.
    let threw = false;
    await act(async () => {
      try {
        await result.current.upload(file);
      } catch {
        threw = true;
      }
    });
    expect(threw).toBe(true);
    expect(confirmIngestion).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.active?.phase).toBe("failed"));
    expect(toastError).toHaveBeenCalled();
  });

  it("surfaces a failed ingestion status with a terminal error toast", async () => {
    getPresignedUrl.mockResolvedValue({
      document_id: "doc3",
      upload_url: "https://s3.test/put/doc3",
      s3_key: "uploads/u/doc3.pdf",
      session_id: "s1",
    });
    putToS3.mockResolvedValue(undefined);
    confirmIngestion.mockResolvedValue({
      document_id: "doc3",
      status: "queued",
    });
    getDocumentStatus.mockResolvedValue({
      id: "doc3",
      filename: "report.pdf",
      status: "failed",
      error: "unreadable pdf",
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpload(), { wrapper });

    await act(async () => {
      await result.current.upload(file);
    });
    await waitFor(() => expect(result.current.active?.phase).toBe("failed"));
    expect(toastError).toHaveBeenCalledWith(
      "report.pdf failed to ingest: unreadable pdf"
    );
  });
});
