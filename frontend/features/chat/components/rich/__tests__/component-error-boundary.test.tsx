import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

// Rich components must be ON so ComponentBlock validates + dispatches (and thus hits the boundary).
vi.mock("@/lib/flags", () => ({ flags: { richComponents: true } }));

// Force the chart renderer to THROW on render — a schema-valid spec that still blows up at render
// time (the exact H-F5 hazard: a recharts edge case / failed lazy chunk). The boundary must catch
// it and degrade THIS block to the raw fallback, leaving siblings mounted.
vi.mock("@/features/chat/components/rich/chart", () => ({
  ChartComponent: () => {
    throw new Error("chart blew up on render");
  },
}));

// Sentry capture is wired in componentDidCatch; assert it's invoked (no-op-safe) but don't require it.
const captureErrorSpy = vi.fn();
vi.mock("@/lib/observability/sentry", () => ({
  captureError: (...args: unknown[]) => captureErrorSpy(...args),
}));

import { ComponentBlock } from "@/features/chat/components/rich/component-block";

const CHART = {
  type: "chart",
  chart: "bar",
  x: ["a"],
  series: [{ name: "s", y: [1] }],
};
const TABLE = {
  type: "table",
  columns: ["A", "B"],
  rows: [["1", "2"]],
};

describe("ComponentBlock error boundary (R17 / H-F5)", () => {
  beforeEach(() => {
    captureErrorSpy.mockClear();
    // React logs the caught error to console.error; silence it to keep the test output clean.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the collapsed raw fallback when a component throws on render", () => {
    render(<ComponentBlock spec={CHART} />);

    // The throwing chart degrades to the raw-JSON disclosure instead of crashing.
    expect(screen.getByText(/Rich component \(raw\)/i)).toBeInTheDocument();
    expect(screen.getByText(/"type": "chart"/)).toBeInTheDocument();
    // And the failure was reported (best-effort).
    expect(captureErrorSpy).toHaveBeenCalledTimes(1);
  });

  it("isolates the failure: a sibling component stays mounted when another throws", () => {
    render(
      <div>
        <div data-testid="bad">
          <ComponentBlock spec={CHART} />
        </div>
        <div data-testid="good">
          <ComponentBlock spec={TABLE} />
        </div>
      </div>
    );

    // The bad block fell back…
    const bad = screen.getByTestId("bad");
    expect(
      within(bad).getByText(/Rich component \(raw\)/i)
    ).toBeInTheDocument();

    // …while the sibling table rendered normally (both column headers present).
    const good = screen.getByTestId("good");
    expect(within(good).getAllByRole("columnheader")).toHaveLength(2);
  });
});
