// lib/zod.ts
//
// Shared Zod schema helpers — single-sourced building blocks reused across feature schemas
// so a coercion/validation rule is spelled exactly once.
import { z } from "zod";

/**
 * Coerce a wire id that may arrive as a string OR a number into a stable string. networkx and
 * some backend rows emit numeric ids, but the UI keys on strings (force-graph nodes, key store
 * rows). Reused by keys.schemas (KeyMeta.id, SaveKeyResponse.id) and graph.schemas
 * (GraphNode.id, GraphLink source/target) so the rule lives in one place.
 */
export const coercedId = z.union([z.string(), z.number()]).transform(String);
