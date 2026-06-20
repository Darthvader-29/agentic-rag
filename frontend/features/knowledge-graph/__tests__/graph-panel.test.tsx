import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import type { ComponentType } from "react";

// Flag live so the panel doesn't early-return null.
vi.mock("@/lib/flags", () => ({
  flags: { knowledgeGraph: true, auth: false },
}));

// jsdom + canvas: stub the force-graph default export so the real (canvas-backed) module never
// loads. The stub reflects the data it receives so we can assert the panel handed it the graph.
// Declared via vi.hoisted so both the react-force-graph-2d and next/dynamic mock factories (which
// are hoisted above the imports) can close over the same spy + stub component.
const { forceGraphSpy, ForceGraphStub } = vi.hoisted(() => {
  const spy = vi.fn();
  const Stub = (props: {
    graphData?: { nodes: unknown[]; links: unknown[] };
  }) => {
    spy(props);
    const n = props.graphData?.nodes?.length ?? 0;
    const l = props.graphData?.links?.length ?? 0;
    return <div data-testid="force-graph" data-nodes={n} data-links={l} />;
  };
  return { forceGraphSpy: spy, ForceGraphStub: Stub };
});

vi.mock("react-force-graph-2d", () => ({
  __esModule: true,
  default: ForceGraphStub,
}));

// next/dynamic: render the stub synchronously (no Next runtime / Suspense in jsdom). Our panel only
// ever lazy-imports react-force-graph-2d, so resolving every dynamic() to the stub is sufficient.
vi.mock("next/dynamic", () => ({
  __esModule: true,
  default: (): ComponentType<unknown> =>
    ForceGraphStub as ComponentType<unknown>,
}));

// Reduced motion off by default (overridable per test).
const reducedMotion = { value: false };
vi.mock("@/hooks/use-reduced-motion", () => ({
  useReducedMotion: () => reducedMotion.value,
}));

// Controlled hook so we can drive each render state deterministically (the fetch/parse path is
// covered by use-knowledge-graph.test.tsx with MSW).
const hookState = vi.fn();
vi.mock("@/features/knowledge-graph/hooks/use-knowledge-graph", () => ({
  useKnowledgeGraph: () => hookState(),
}));

import GraphPanel from "@/features/knowledge-graph/components/graph-panel";

const EMPTY = { nodes: [], links: [] };

function baseState(over: Record<string, unknown> = {}) {
  return {
    graph: EMPTY,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    enabled: true,
    refetch: vi.fn(),
    ...over,
  };
}

beforeEach(() => {
  reducedMotion.value = false;
  forceGraphSpy.mockClear();
  // jsdom lacks ResizeObserver; provide one that immediately reports a non-zero width so the
  // panel mounts the force-graph.
  class RO {
    constructor(private cb: ResizeObserverCallback) {}
    observe(el: Element) {
      this.cb(
        [
          {
            contentRect: { width: 600, height: 320 },
            target: el,
          } as unknown as ResizeObserverEntry,
        ],
        this as unknown as ResizeObserver
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO as unknown as typeof ResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("GraphPanel", () => {
  it("renders nothing when disabled (flag off / no session)", () => {
    hookState.mockReturnValue(baseState({ enabled: false }));
    const { container } = render(<GraphPanel sessionId="" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a loading state while fetching", () => {
    hookState.mockReturnValue(baseState({ isLoading: true }));
    render(<GraphPanel sessionId="s1" />);
    expect(screen.getByLabelText("Knowledge graph")).toHaveAttribute(
      "aria-busy",
      "true"
    );
  });

  it("renders an empty state with a refresh action when there are no nodes", () => {
    const refetch = vi.fn();
    hookState.mockReturnValue(baseState({ graph: EMPTY, refetch }));
    render(<GraphPanel sessionId="s1" />);

    expect(screen.getByText(/No knowledge graph yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId("force-graph")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("renders an error state with a retry action", () => {
    const refetch = vi.fn();
    hookState.mockReturnValue(
      baseState({ isError: true, error: new Error("x"), refetch })
    );
    render(<GraphPanel sessionId="s1" />);

    expect(
      screen.getByText(/Couldn.?t load the knowledge graph/i)
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("renders the force graph with the fetched nodes/links", () => {
    const graph = {
      nodes: [{ id: "A" }, { id: "B" }, { id: "C" }],
      links: [
        { source: "A", target: "B", relation: "rel" },
        { source: "B", target: "C" },
      ],
    };
    hookState.mockReturnValue(baseState({ graph }));
    render(<GraphPanel sessionId="s1" />);

    const fg = screen.getByTestId("force-graph");
    expect(fg).toHaveAttribute("data-nodes", "3");
    expect(fg).toHaveAttribute("data-links", "2");
    // Summary line reflects counts.
    expect(screen.getByText(/3 entities/)).toBeInTheDocument();
    expect(screen.getByText(/2 relations/)).toBeInTheDocument();
  });

  it("caps a large graph (top-N by degree) and shows a 'showing N of M' note (R26)", () => {
    // 400 nodes, ~600 links — well past the MAX_NODES=150 / MAX_EDGES=300 caps.
    const nodes = Array.from({ length: 400 }, (_, i) => ({ id: `n${i}` }));
    // Make n0 a hub (high degree → must survive the cap), plus a long chain.
    const links: { source: string; target: string }[] = [];
    for (let i = 1; i < 200; i++) links.push({ source: "n0", target: `n${i}` });
    for (let i = 0; i < 399; i++)
      links.push({ source: `n${i}`, target: `n${i + 1}` });
    hookState.mockReturnValue(baseState({ graph: { nodes, links } }));
    render(<GraphPanel sessionId="s1" />);

    const fg = screen.getByTestId("force-graph");
    const renderedNodes = Number(fg.getAttribute("data-nodes"));
    const renderedLinks = Number(fg.getAttribute("data-links"));
    expect(renderedNodes).toBeLessThanOrEqual(150);
    expect(renderedLinks).toBeLessThanOrEqual(300);
    // The hub (highest degree) is kept.
    const passed = forceGraphSpy.mock.calls.at(-1)![0] as {
      graphData: { nodes: { id: string }[] };
    };
    expect(passed.graphData.nodes.some((n) => n.id === "n0")).toBe(true);
    // "showing N of M" note surfaces the truncation.
    expect(screen.getByText(/Showing .* of 400 entities/i)).toBeInTheDocument();
  });

  it("does NOT show a 'showing' note for a small graph (within caps)", () => {
    const graph = {
      nodes: [{ id: "A" }, { id: "B" }],
      links: [{ source: "A", target: "B" }],
    };
    hookState.mockReturnValue(baseState({ graph }));
    render(<GraphPanel sessionId="s1" />);
    expect(screen.queryByText(/Showing .* of/i)).not.toBeInTheDocument();
    expect(screen.getByText(/2 entities/)).toBeInTheDocument();
  });

  it("passes accessor props (nodeLabel=id, linkLabel=relation) and disables drag under reduced motion", () => {
    reducedMotion.value = true;
    const graph = {
      nodes: [{ id: "Entity-1" }, { id: "Entity-2" }],
      links: [{ source: "Entity-1", target: "Entity-2", relation: "links_to" }],
    };
    hookState.mockReturnValue(baseState({ graph }));
    render(<GraphPanel sessionId="s1" />);

    expect(forceGraphSpy).toHaveBeenCalled();
    const props = forceGraphSpy.mock.calls.at(-1)![0] as {
      nodeLabel: (n: { id: string }) => string;
      linkLabel: (l: { relation?: string }) => string;
      nodeVal: (n: { id: string }) => number;
      enableNodeDrag: boolean;
      cooldownTicks?: number;
    };

    expect(props.nodeLabel({ id: "Entity-1" })).toBe("Entity-1");
    expect(props.linkLabel({ relation: "links_to" })).toBe("links_to");
    expect(props.linkLabel({})).toBe(""); // missing relation ⇒ empty label
    // Degree-based size: Entity-1 has degree 1 ⇒ 1 + 1 = 2.
    expect(props.nodeVal({ id: "Entity-1" })).toBe(2);
    // Reduced motion ⇒ no drag, simulation settled (cooldownTicks 0).
    expect(props.enableNodeDrag).toBe(false);
    expect(props.cooldownTicks).toBe(0);
  });
});
