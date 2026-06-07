// features/knowledge-graph/api/graph.api.ts
//
// Typed call for the Phase-7 knowledge-graph endpoint (forward-compat; gated by
// flags.knowledgeGraph at the hook layer):
//   GET /api/sessions/{sessionId}/graph -> networkx node-link JSON -> { nodes, links }
//
// The endpoint is being built in parallel and is session-scoped, so:
//   - 404 is NOT an error — it means "no graph for this session yet". We swallow it and return
//     the empty graph so the panel renders an empty state, never an error state.
//   - other failures (network / 5xx / parse) propagate as ApiError for TanStack Query to surface
//     as the error state.
// Bearer is attached via the http-client interceptor when auth is live (auth: flags.auth) —
// byte-for-byte today's anonymous request when the auth flag is off.
import { request } from "@/lib/api/http-client";
import { flags } from "@/lib/flags";
import { isApiError } from "@/lib/api/api-error";
import {
  GraphResponseSchema,
  EMPTY_GRAPH,
  type GraphData,
} from "./graph.schemas";

/**
 * Fetches and parses the session's knowledge graph into `{ nodes, links }`.
 *
 * @param sessionId  The session whose graph to load. Empty ⇒ resolves to the empty graph
 *                   without hitting the network (no session, nothing to fetch).
 * @param signal     Optional AbortSignal (TanStack Query cancellation).
 * @returns          The parsed graph; `EMPTY_GRAPH` on a 404 (no graph yet).
 * @throws ApiError  On network/5xx/parse failures (surfaced as the panel's error state).
 */
export async function fetchGraph(
  sessionId: string,
  signal?: AbortSignal
): Promise<GraphData> {
  if (!sessionId) return EMPTY_GRAPH;

  try {
    const data = await request<GraphData>(
      `/sessions/${encodeURIComponent(sessionId)}/graph`,
      {
        method: "GET",
        schema: GraphResponseSchema,
        signal,
        auth: flags.auth,
      }
    );
    // Normalize to the consumer contract — drop the ignored networkx wrapper fields so the
    // force-graph only ever sees `{ nodes, links }`.
    return { nodes: data.nodes, links: data.links };
  } catch (e) {
    // 404 ⇒ no graph for this session yet ⇒ empty state, not an error.
    if (isApiError(e) && e.status === 404) return EMPTY_GRAPH;
    throw e;
  }
}

export const graphApi = {
  fetchGraph,
} as const;
