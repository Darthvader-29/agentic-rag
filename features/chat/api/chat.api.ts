import { v4 as uuidv4 } from "uuid";
import { request } from "@/lib/api/http-client";
import {
  chatRequestSchema,
  chatResponseSchema,
  uploadResponseSchema,
  type ChatResponse,
  type UploadResponse,
} from "./chat.schemas";

const SESSION_KEY = "rag_session_id";

export function getSessionId(): string {
  if (typeof window === "undefined") return "";
  let sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = uuidv4();
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  return sessionId;
}

function persistSessionId(id: string): void {
  if (typeof window !== "undefined") localStorage.setItem(SESSION_KEY, id);
}

export async function sendMessage(
  message: string,
  webSearchAllowed: boolean,
  signal?: AbortSignal
): Promise<ChatResponse> {
  const payload = chatRequestSchema.parse({
    message,
    session_id: getSessionId(),
    web_search_allowed: webSearchAllowed,
  });

  const data = await request("/chat", {
    method: "POST",
    body: payload,
    schema: chatResponseSchema,
    signal,
  });

  if (data.session_id) persistSessionId(data.session_id);
  return data;
}

export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", getSessionId());

  return request("/upload", {
    method: "POST",
    body: formData,
    schema: uploadResponseSchema,
  });
}

export async function cleanupSession(): Promise<void> {
  if (typeof window === "undefined") return;
  const sessionId = localStorage.getItem(SESSION_KEY);
  if (sessionId) {
    try {
      await request("/cleanup", {
        method: "POST",
        body: { session_id: sessionId, file_keys: [] as string[] },
      });
    } catch (e) {
      console.error("Cleanup failed", e);
    }
  }
  localStorage.removeItem(SESSION_KEY);
  localStorage.setItem(SESSION_KEY, uuidv4());
}

export const api = {
  getSessionId,
  sendMessage,
  uploadFile,
  cleanupSession,
  clearSession: cleanupSession,
} as const;
