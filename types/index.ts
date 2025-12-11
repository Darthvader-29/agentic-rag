// types/index.ts

export type RouteType =
  | "RAG"
  | "WEB"
  | "DIRECT"
  | "WEB+RAG"
  | "DIRECT+WEB"
  | "DIRECT+RAG"
  | "ERROR";

export interface ChatRequest {
  message: string;
  session_id: string;
  web_search_allowed: boolean;
}

export interface ChatResponse {
  answer: string;
  route: RouteType;
  context_count: number;
  session_id: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: RouteType;      // Only assistant messages have this
  sourcesCount?: number;  // Only RAG messages have this
  timestamp: Date;
}

