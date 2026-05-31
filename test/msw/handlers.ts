import { http, HttpResponse } from "msw";
import { env } from "@/lib/env";

const SSE_SCRIPT =
  'event: status\ndata: {"stage": "routing"}\n\n' +
  'event: status\ndata: {"stage": "retrieving"}\n\n' +
  'event: status\ndata: {"stage": "synthesizing"}\n\n' +
  'event: token\ndata: {"text": "Grounded "}\n\n' +
  'event: token\ndata: {"text": "answer."}\n\n' +
  'event: component\ndata: {"type": "citation", "items": [{"label": "doc.pdf · p.4", "source_id": "chunk_8c1f"}]}\n\n' +
  'event: done\ndata: {"answer": "Grounded answer.", "route": "RAG"}\n\n'; // flat enum (09)

export const sseChatHandler = http.post(
  `${env.NEXT_PUBLIC_API_URL}/chat`,
  ({ request }: { request: Request }) => {
    if (request.headers.get("accept") !== "text/event-stream") return; // fall through to blocking handler
    return new HttpResponse(SSE_SCRIPT, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }
);
