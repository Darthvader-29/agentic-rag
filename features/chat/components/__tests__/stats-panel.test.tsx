import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Silence the success/error toasts pulled in via useCopyToClipboard → sonner.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import StatsPanel from "@/features/chat/components/stats-panel";
import type { MessageStats } from "@/types";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("StatsPanel", () => {
  it("renders nothing when stats is undefined", () => {
    const { container } = render(<StatsPanel />);
    expect(container).toBeEmptyDOMElement();
  });

  it("computes per-stage durations as consecutive atMs deltas", async () => {
    const user = userEvent.setup();
    const stats: MessageStats = {
      startedAtMs: 0,
      // cumulative offsets → deltas: routing=100, retrieval=300 (400−100), synth=200 (600−400)
      stages: [
        { stage: "routing", atMs: 100 },
        { stage: "retrieval", atMs: 400 },
        { stage: "synthesis", atMs: 600 },
      ],
      totalMs: 650,
      route: "RAG",
    };
    render(<StatsPanel stats={stats} />);

    // Expand the collapsible to reveal the stage list.
    await user.click(
      screen.getByRole("button", { name: /toggle turn stats/i })
    );

    expect(screen.getByText("routing")).toBeInTheDocument();
    expect(screen.getByText("100 ms")).toBeInTheDocument(); // first stage from 0
    expect(screen.getByText("300 ms")).toBeInTheDocument(); // 400 − 100
    expect(screen.getByText("200 ms")).toBeInTheDocument(); // 600 − 400
  });

  it("shows total latency and the route badge in the trigger", () => {
    const stats: MessageStats = {
      startedAtMs: 0,
      stages: [{ stage: "routing", atMs: 50 }],
      totalMs: 1234,
      route: "WEB+RAG",
    };
    render(<StatsPanel stats={stats} />);
    // Total appears in the (always-visible) trigger.
    expect(screen.getByText(/1234 ms/)).toBeInTheDocument();
    expect(screen.getByText("WEB+RAG")).toBeInTheDocument();
  });

  it("renders token counts only when present", async () => {
    const user = userEvent.setup();
    const stats: MessageStats = {
      startedAtMs: 0,
      stages: [],
      totalMs: 10,
      tokens: { input: 42, output: 7 },
    };
    render(<StatsPanel stats={stats} />);
    await user.click(
      screen.getByRole("button", { name: /toggle turn stats/i })
    );
    expect(screen.getByText(/42 in \/ 7 out/)).toBeInTheDocument();
  });

  it("clamps a negative (out-of-order) stage delta to 0 ms", async () => {
    const user = userEvent.setup();
    const stats: MessageStats = {
      startedAtMs: 0,
      // second mark is EARLIER than the first → clamp to 0, never negative.
      stages: [
        { stage: "a", atMs: 200 },
        { stage: "b", atMs: 150 },
      ],
      totalMs: 200,
    };
    render(<StatsPanel stats={stats} />);
    await user.click(
      screen.getByRole("button", { name: /toggle turn stats/i })
    );
    expect(screen.getByText("0 ms")).toBeInTheDocument();
  });

  it("renders a copy-only trace chip when no Langfuse host is configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_LANGFUSE_HOST", "");
    const user = userEvent.setup();
    const traceId = "0123456789abcdef0123456789abcdef";
    const stats: MessageStats = {
      startedAtMs: 0,
      stages: [],
      totalMs: 5,
      traceId,
    };
    render(<StatsPanel stats={stats} />);
    await user.click(
      screen.getByRole("button", { name: /toggle turn stats/i })
    );

    expect(
      screen.getByRole("button", { name: /copy trace id/i })
    ).toBeInTheDocument();
    // No deep-link affordance without a configured host.
    expect(
      screen.queryByRole("link", { name: /view trace/i })
    ).not.toBeInTheDocument();
  });

  it("deep-links the trace chip to Langfuse when the host env is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_LANGFUSE_HOST", "https://lf.example.com/");
    const user = userEvent.setup();
    const traceId = "0123456789abcdef0123456789abcdef";
    const stats: MessageStats = {
      startedAtMs: 0,
      stages: [],
      totalMs: 5,
      traceId,
    };
    render(<StatsPanel stats={stats} />);
    await user.click(
      screen.getByRole("button", { name: /toggle turn stats/i })
    );

    const link = screen.getByRole("link", { name: /view trace in langfuse/i });
    // Trailing slash on the host is normalized.
    expect(link).toHaveAttribute(
      "href",
      `https://lf.example.com/trace/${traceId}`
    );
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
