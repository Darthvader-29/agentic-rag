import { clearSessionId } from "@/features/chat/api/chat.api";
import { useChatStore } from "@/features/chat/store/chat.store";

/**
 * Cleanup to run whenever the ACTIVE identity changes to a different user — login, register, and
 * logout. (A guest→registered upgrade is excluded: it preserves the same `user_id`, so its session
 * and conversation stay valid — see `useUpgrade`.)
 *
 * Two things must reset together:
 *   1. The persisted `rag_session_id`. After the tenant-isolation fix the backend 403s a session
 *      owned by another user, so carrying the previous identity's id into a new one would brick
 *      every chat request. Dropping it locally makes the next request mint a fresh, owned session.
 *   2. In-memory chat state. Otherwise the previous user's messages stay on screen for the next
 *      identity on a shared device.
 */
export function resetIdentityState(): void {
  clearSessionId();
  useChatStore.getState().reset();
}
