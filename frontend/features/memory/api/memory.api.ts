// features/memory/api/memory.api.ts
//
// Typed call for the Phase-7 conversation-memory endpoint (BACKEND CONTRACT):
//   GET /api/sessions/{sessionId}/memory -> 200 { session_id, content (markdown), updated_at (ISO) }
//
// The router is mounted under /api and NEXT_PUBLIC_API_URL already ends in "/api", so the relative
// path resolves to "<base>/sessions/<id>/memory" via the http-client's normal base-prepend.
//
// 404 is a FIRST-CLASS, non-error outcome: "this session has no memory yet". We translate it to the
// canonical EMPTY_MEMORY value so the hook/panel render the empty state instead of an error. Every
// other failure (network, 5xx, 401, parse) propagates as an ApiError for the hook's error state.
//
// Auth follows the existing session-scoped pattern (chat.api): `auth: flags.auth`. When the auth flag
// is on, the interceptor attaches the Bearer token + runs the 401→refresh dance; when it's off this is
// byte-for-byte today's anonymous request (the backend binds the session_id to the user).
import { request } from "@/lib/api/http-client";
import { flags } from "@/lib/flags";
import { isApiError } from "@/lib/api/api-error";
import {
  SessionMemorySchema,
  EMPTY_MEMORY,
  type SessionMemory,
} from "./memory.schemas";

/**
 * Fetches the conversation memory for a session.
 *
 * @returns the parsed `{ content, updated_at }` on 200, or `EMPTY_MEMORY` ({content:'', updated_at:null})
 *          on 404 (no memory yet). Any other failure throws an ApiError.
 */
export async function fetchSessionMemory(
  sessionId: string,
  signal?: AbortSignal
): Promise<SessionMemory> {
  try {
    return await request<SessionMemory>(
      `/sessions/${encodeURIComponent(sessionId)}/memory`,
      {
        method: "GET",
        schema: SessionMemorySchema,
        auth: flags.auth,
        signal,
      }
    );
  } catch (e) {
    // 404 ⇒ no memory yet. Degrade to the empty value (legacy-/dark-launch-safe); rethrow the rest.
    if (isApiError(e) && e.status === 404) return EMPTY_MEMORY;
    throw e;
  }
}

export const memoryApi = {
  fetch: fetchSessionMemory,
} as const;
