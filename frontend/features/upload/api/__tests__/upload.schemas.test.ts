import { describe, it, expect } from "vitest";
import {
  DocumentRecordSchema,
  PresignResponseSchema,
  isTerminalStatus,
  normalizeStatus,
  toDocumentRecord,
} from "@/features/upload/api/upload.schemas";

describe("upload.schemas", () => {
  it("normalizes the backend-doc 'complete' drift to 'ready'", () => {
    expect(normalizeStatus("complete")).toBe("ready");
    expect(normalizeStatus("processing")).toBe("processing");
    expect(normalizeStatus("failed")).toBe("failed");
  });

  it("treats only ready/failed as terminal", () => {
    expect(isTerminalStatus("ready")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("processing")).toBe(false);
    expect(isTerminalStatus("pending")).toBe(false);
  });

  it("parses a presign response including the session_id the backend returns", () => {
    const r = PresignResponseSchema.parse({
      document_id: "d1",
      upload_url: "https://s3.test/put/d1",
      s3_key: "uploads/u/d1.pdf",
      session_id: "s1",
    });
    expect(r.document_id).toBe("d1");
    expect(r.session_id).toBe("s1");
  });

  it("normalizes a document record (complete -> ready) and tolerates a missing error", () => {
    const rec = toDocumentRecord(
      DocumentRecordSchema.parse({
        id: "d1",
        filename: "report.pdf",
        status: "complete",
        s3_key: "uploads/u/d1.pdf",
        session_id: "s1",
      })
    );
    expect(rec).toEqual({
      id: "d1",
      filename: "report.pdf",
      status: "ready",
      error: null,
    });
  });

  it("keeps a failure error string on the normalized record", () => {
    const rec = toDocumentRecord(
      DocumentRecordSchema.parse({
        id: "d2",
        filename: "bad.pdf",
        status: "failed",
        error: "unreadable pdf",
      })
    );
    expect(rec.status).toBe("failed");
    expect(rec.error).toBe("unreadable pdf");
  });
});
