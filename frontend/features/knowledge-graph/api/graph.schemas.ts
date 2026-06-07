// features/knowledge-graph/api/graph.schemas.ts
//
// Zod contracts for the Phase-7 knowledge-graph endpoint (forward-compat; DEFAULT OFF):
//   GET /api/sessions/{sessionId}/graph
//     -> networkx node-link JSON:
//        { directed, multigraph, graph, nodes: [{ id }...], links: [{ source, target, relation, doc_id }...] }
//     -> 404 ⇒ treated as empty by the api layer (no graph yet)
//
// react-force-graph-2d consumes `{ nodes, links }` directly, so we parse the wire shape and
// expose exactly that pair. Schemas are TOLERANT of contract drift on the wrapper fields
// (directed/multigraph/graph are ignored) and on optional edge metadata (relation/doc_id), so a
// leaner-or-richer backend response still validates rather than throwing — the panel degrades to
// an empty/partial render instead of an error. Node ids are coerced to string (force-graph keys
// nodes by id) and any extra per-node/per-link fields are passed through untouched.
import { z } from "zod";
import { coercedId } from "@/lib/zod";

/**
 * One graph node. `id` is the entity identifier (string label shown on the node). networkx may
 * emit numeric ids, so we coerce to string for stable force-graph keying. Unknown extra fields
 * (e.g. a future `type`/`label`) are preserved via passthrough so we never drop server data.
 */
export const GraphNodeSchema = z
  .object({
    id: coercedId,
  })
  .passthrough();
export type GraphNode = z.infer<typeof GraphNodeSchema>;

/**
 * One directed edge. `source`/`target` reference node ids (coerced to string to match nodes).
 * `relation` is the edge label (shown on hover); `doc_id` is the provenance document. Both edge
 * metadata fields are optional — an edge missing them still renders (no label / no provenance).
 */
export const GraphLinkSchema = z
  .object({
    source: coercedId,
    target: coercedId,
    relation: z.string().optional(),
    doc_id: z.string().optional(),
  })
  .passthrough();
export type GraphLink = z.infer<typeof GraphLinkSchema>;

/**
 * The raw networkx node-link envelope. We only require `nodes`/`links`; the wrapper fields
 * (`directed`, `multigraph`, `graph`) are accepted-and-ignored via passthrough. Both arrays
 * default to empty so a `{}` or partial body parses to an empty graph rather than failing.
 */
export const GraphResponseSchema = z
  .object({
    nodes: z.array(GraphNodeSchema).default([]),
    links: z.array(GraphLinkSchema).default([]),
  })
  .passthrough();
export type GraphResponse = z.infer<typeof GraphResponseSchema>;

/**
 * The shape react-force-graph-2d's `graphData` prop consumes directly. DERIVED from the parsed
 * response's `{ nodes, links }` (the `.default([])` outputs make both keys present + non-optional),
 * so it can't drift from the wire envelope; named separately so the hook/component depend on the
 * consumer contract, not the full envelope.
 */
export type GraphData = Pick<GraphResponse, "nodes" | "links">;

/** The canonical empty graph (404 / disabled / pre-fetch). */
export const EMPTY_GRAPH: GraphData = { nodes: [], links: [] };
