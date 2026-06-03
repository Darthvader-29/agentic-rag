// features/memory/__tests__/memory-panel.test.tsx
//
// Render-state tests for the memory panel. The data hook is mocked so each branch (loading / empty /
// error / content) is driven deterministically; the markdown render + relative-time stamp + refresh
// affordance are asserted against the DOM. The formatRelativeTime helper is unit-tested directly.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Flag on (chat-screen only mounts the panel when flags.memory; the panel itself doesn't re-check,
// but the hook it calls does — we mock the hook below, so this just keeps the env sane).
vi.mock("@/lib/flags", () => ({
  flags: { memory: true, auth: false, streaming: false },
}));

// Mutable hook return so each test drives a different state.
type HookState = {
  content: string;
  updatedAt: string | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  enabled: boolean;
  refetch: ReturnType<typeof vi.fn>;
};

const refetch = vi.fn().mockResolvedValue({});
let hookState: HookState;

vi.mock("@/features/memory/hooks/use-session-memory", () => ({
  useSessionMemory: () => hookState,
}));

import MemoryPanel, {
  formatRelativeTime,
} from "@/features/memory/components/memory-panel";

function baseState(overrides: Partial<HookState> = {}): HookState {
  return {
    content: "",
    updatedAt: null,
    isLoading: false,
    isError: false,
    error: null,
    enabled: true,
    refetch,
    ...overrides,
  };
}

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-06-03T12:00:00.000Z");

  it("returns null for null/invalid input", () => {
    expect(formatRelativeTime(null, now)).toBeNull();
    expect(formatRelativeTime("not-a-date", now)).toBeNull();
  });

  it("formats recent times as 'just now'", () => {
    expect(formatRelativeTime("2026-06-03T11:59:50.000Z", now)).toBe(
      "just now"
    );
  });

  it("formats minutes / hours / days", () => {
    expect(formatRelativeTime("2026-06-03T11:30:00.000Z", now)).toBe("30m ago");
    expect(formatRelativeTime("2026-06-03T09:00:00.000Z", now)).toBe("3h ago");
    expect(formatRelativeTime("2026-06-01T12:00:00.000Z", now)).toBe("2d ago");
  });

  it("guards a future stamp (clock skew) as 'just now'", () => {
    expect(formatRelativeTime("2026-06-03T12:05:00.000Z", now)).toBe(
      "just now"
    );
  });
});

describe("MemoryPanel", () => {
  beforeEach(() => {
    refetch.mockClear();
    hookState = baseState();
  });

  it("renders a loading skeleton while the query is in flight", () => {
    hookState = baseState({ isLoading: true });
    const { container } = render(<MemoryPanel sessionId="s1" />);
    // Skeleton primitive marks itself with data-slot="skeleton".
    expect(
      container.querySelectorAll('[data-slot="skeleton"]').length
    ).toBeGreaterThan(0);
    expect(screen.queryByText("No memory yet")).toBeNull();
  });

  it("renders the empty state when there is no memory", () => {
    hookState = baseState({ content: "", updatedAt: null });
    render(<MemoryPanel sessionId="s1" />);
    expect(screen.getByText("No memory yet")).toBeInTheDocument();
  });

  it("treats whitespace-only content as empty", () => {
    hookState = baseState({ content: "   \n  " });
    render(<MemoryPanel sessionId="s1" />);
    expect(screen.getByText("No memory yet")).toBeInTheDocument();
  });

  it("renders markdown content and a relative updated stamp", () => {
    hookState = baseState({
      content: "# Heading\n\nA **bold** fact.",
      // far in the past so the stamp is stable regardless of when the test runs
      updatedAt: "2000-01-01T00:00:00.000Z",
    });
    render(<MemoryPanel sessionId="s1" />);

    // Markdown rendered to real HTML (heading + strong), not raw text.
    expect(
      screen.getByRole("heading", { name: "Heading" })
    ).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText(/Updated .*ago/)).toBeInTheDocument();
  });

  it("renders the error state with a Retry that calls refetch", async () => {
    hookState = baseState({ isError: true, error: new Error("x") });
    render(<MemoryPanel sessionId="s1" />);

    expect(screen.getByText(/Couldn't load memory/i)).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /retry/i });
    await userEvent.click(retry);
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
  });

  it("refresh button calls refetch", async () => {
    hookState = baseState({ content: "some memory" });
    render(<MemoryPanel sessionId="s1" />);

    const refresh = screen.getByRole("button", { name: /refresh memory/i });
    await userEvent.click(refresh);
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
  });

  it("omits the updated stamp when updatedAt is null but content exists", () => {
    hookState = baseState({ content: "memory body", updatedAt: null });
    render(<MemoryPanel sessionId="s1" />);
    expect(screen.getByText("memory body")).toBeInTheDocument();
    expect(screen.queryByText(/Updated/)).toBeNull();
  });
});
