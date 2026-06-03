import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

// Knowledge-graph flag must be LIVE for the query to enable; auth OFF keeps requests anonymous
// (no Bearer, no auth store needed) so we exercise the data path in isolation.
vi.mock("@/lib/flags", () => ({
  flags: { knowledgeGraph: true, auth: false },
}));

import { env } from "@/lib/env";
import { useKnowledgeGraph } from "@/features/knowledge-graph/hooks/use-knowledge-graph";

const GRAPH_URL = `${env.NEXT_PUBLIC_API_URL}/sessions/:sessionId/graph`;

// A representative networkx node-link payload (with the wrapper fields we ignore, numeric-coercible
// ids, and one edge missing optional metadata) to prove the schema is tolerant.
const NODE_LINK_BODY = {
  directed: true,
  multigraph: false,
  graph: {},
  nodes: [{ id: "Ada Lovelace" }, { id: "Analytical Engine" }, { id: 42 }],
  links: [
    {
      source: "Ada Lovelace",
      target: "Analytical Engine",
      relation: "worked_on",
      doc_id: "doc_1",
    },
    // edge missing relation/doc_id — must still parse, and references a numeric-coerced node id.
    { source: "Analytical Engine", target: 42 },
  ],
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

describe("useKnowledgeGraph", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches and parses networkx node-link JSON into { nodes, links }", async () => {
    server.use(http.get(GRAPH_URL, () => HttpResponse.json(NODE_LINK_BODY)));

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useKnowledgeGraph("sess-1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.graph.nodes).toHaveLength(3));

    // Node ids coerced to string (incl. the numeric 42 → "42").
    expect(result.current.graph.nodes.map((n) => n.id)).toEqual([
      "Ada Lovelace",
      "Analytical Engine",
      "42",
    ]);
    // Links parsed; relation preserved on the first, absent (undefined) on the second.
    expect(result.current.graph.links).toHaveLength(2);
    expect(result.current.graph.links[0]).toMatchObject({
      source: "Ada Lovelace",
      target: "Analytical Engine",
      relation: "worked_on",
      doc_id: "doc_1",
    });
    expect(result.current.graph.links[1]).toMatchObject({
      source: "Analytical Engine",
      target: "42",
    });
    expect(result.current.graph.links[1].relation).toBeUndefined();
    expect(result.current.isError).toBe(false);
  });

  it("treats a 404 as an empty graph (no graph yet), not an error", async () => {
    server.use(
      http.get(GRAPH_URL, () =>
        HttpResponse.json({ detail: "no graph" }, { status: 404 })
      )
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useKnowledgeGraph("sess-1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isFetching).toBe(false));
    expect(result.current.graph).toEqual({ nodes: [], links: [] });
    expect(result.current.isError).toBe(false);
  });

  it("surfaces a 500 as the error state", async () => {
    server.use(
      http.get(GRAPH_URL, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })
      )
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useKnowledgeGraph("sess-1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    // Still exposes a safe empty graph for the renderer.
    expect(result.current.graph).toEqual({ nodes: [], links: [] });
  });

  it("is disabled with no network call when sessionId is empty", async () => {
    // onUnhandledRequest:"error" means any fetch here would fail the test.
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useKnowledgeGraph(""), { wrapper });

    expect(result.current.enabled).toBe(false);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.graph).toEqual({ nodes: [], links: [] });
  });

  it("empty body parses to an empty graph", async () => {
    server.use(http.get(GRAPH_URL, () => HttpResponse.json({})));

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useKnowledgeGraph("sess-2"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isFetching).toBe(false));
    expect(result.current.graph).toEqual({ nodes: [], links: [] });
    expect(result.current.isError).toBe(false);
  });
});
