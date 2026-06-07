import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProvenanceBadge } from "@/features/chat/components/rich/provenance-badge";
import type { RetrievalLayer } from "@/types";

describe("ProvenanceBadge", () => {
  it.each<[RetrievalLayer, string]>([
    ["vector", "Vector"],
    ["graph", "Graph"],
    ["web", "Web"],
    ["memory", "Memory"],
  ])("renders the correct label for layer=%s", (layer, label) => {
    render(<ProvenanceBadge layer={layer} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("exposes an accessible aria-label naming the source layer", () => {
    render(<ProvenanceBadge layer="graph" />);
    // aria-label is "Source layer: Graph. <description>"
    expect(screen.getByLabelText(/source layer: graph/i)).toBeInTheDocument();
  });

  it("renders nothing when layer is undefined (legacy-safe)", () => {
    const { container } = render(<ProvenanceBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an unknown/future layer value (never crashes)", () => {
    const { container } = render(
      // Cast: simulate a forward-compat value the union doesn't know yet.
      <ProvenanceBadge layer={"satellite" as RetrievalLayer} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
