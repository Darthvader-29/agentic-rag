// services/api.ts
import { ChatRequest, ChatResponse } from "@/types";
import { v4 as uuidv4 } from "uuid";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://python-agentic-rag-backend.onrender.com/api";

export const api = {
  getSessionId: (): string => {
    if (typeof window === "undefined") return "";
    let sessionId = localStorage.getItem("rag_session_id");
    if (!sessionId) {
      sessionId = uuidv4();
      localStorage.setItem("rag_session_id", sessionId);
    }
    return sessionId;
  },

  clearSession: async () => {
    const sessionId = localStorage.getItem("rag_session_id");
    if (sessionId) {
      try {
        // backend cleanup expects: { session_id, file_keys }
        await fetch(`${API_BASE_URL}/cleanup`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            file_keys: [],
          }),
        });
      } catch (e) {
        console.error("Cleanup failed", e);
      }
    }
    localStorage.removeItem("rag_session_id");
    localStorage.setItem("rag_session_id", uuidv4());
  },

  sendMessage: async (
    message: string,
    webSearchAllowed: boolean
  ): Promise<ChatResponse> => {
    const sessionId = api.getSessionId();

    const payload: ChatRequest = {
      message,
      session_id: sessionId,
      web_search_allowed: webSearchAllowed,
    };

    const res = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      let msg = `Backend error: ${res.status}`;
      try {
        const data = await res.json();
        if (data?.detail) {
          // AppException.detail from backend (includes Gemini 403/429/etc)
          msg = data.detail;
        }
      } catch {
        // ignore JSON parse errors
      }
      throw new Error(msg);
    }

    const data = (await res.json()) as ChatResponse;
    // backend may return a new session_id – persist it
    if (data.session_id) {
      localStorage.setItem("rag_session_id", data.session_id);
    }
    return data;
  },

  uploadFile: async (file: File): Promise<unknown> => {
    const sessionId = api.getSessionId();
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", sessionId);

    const res = await fetch(`${API_BASE_URL}/upload`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      throw new Error(`Upload failed: ${res.status}`);
    }
    return res.json();
  },
};
