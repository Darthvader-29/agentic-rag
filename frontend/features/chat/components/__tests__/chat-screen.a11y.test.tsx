import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, domAnimation } from "framer-motion";
import type { ComponentType } from "react";

// Insights drawer is gated on a panel flag — turn knowledge-graph on so it renders (R28).
vi.mock("@/lib/flags", () => ({
  flags: {
    knowledgeGraph: true,
    memory: false,
    auth: false,
    byok: false,
    streaming: false,
  },
}));

// Stub the chat data layer + heavy children so the test targets only the drawer/toggle a11y.
vi.mock("@/features/chat/hooks/use-chat", () => ({
  useChat: () => ({ messages: [], sendMessage: vi.fn() }),
  resetSession: vi.fn(),
}));
vi.mock("@/features/chat/api/chat.api", () => ({ getSessionId: () => "s1" }));
vi.mock("@/components/chat/sidebar", () => ({
  Sidebar: ({ onToggle }: { onToggle?: () => void }) => (
    <button aria-label="Toggle sidebar" onClick={onToggle}>
      sidebar
    </button>
  ),
}));
vi.mock("@/components/chat/chat-input", () => ({
  ChatInput: () => <div data-testid="chat-input" />,
}));
vi.mock("@/components/chat/empty-state", () => ({
  EmptyState: () => <div data-testid="empty-state" />,
}));
vi.mock("@/features/keys/components/free-tier-banner", () => ({
  FreeTierBanner: () => null,
}));
vi.mock("@/features/keys/components/free-tier-exhausted-dialog", () => ({
  FreeTierExhaustedDialog: () => null,
}));
// next/dynamic → render a synchronous stub (no Next runtime in jsdom).
vi.mock("next/dynamic", () => ({
  __esModule: true,
  default: (): ComponentType<unknown> =>
    (() => <div data-testid="graph-panel" />) as ComponentType<unknown>,
}));
vi.mock("@/hooks/use-reduced-motion", () => ({
  useReducedMotion: () => true,
}));

import { ChatScreen } from "@/features/chat/components/chat-screen";

function renderScreen() {
  return render(
    <LazyMotion features={domAnimation}>
      <ChatScreen />
    </LazyMotion>
  );
}

beforeEach(() => {
  // Radix Dialog needs these DOM APIs jsdom lacks.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  Element.prototype.releasePointerCapture = vi.fn();
  class RO {
    observe() {}
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

describe("ChatScreen — insights drawer a11y (R28)", () => {
  it("opens the insights drawer as a focus-trapped dialog and closes on Escape", async () => {
    const user = userEvent.setup();
    renderScreen();

    // Closed: no dialog yet.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /open insights/i }));

    // Open: a proper dialog labelled "Insights" (radix provides role + aria-modal + focus trap).
    const dialog = await screen.findByRole("dialog", { name: /insights/i });
    expect(dialog).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /close insights/i })
    ).toBeInTheDocument();

    // Escape closes it (radix focus-trap behavior).
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("moves focus to the 'Open sidebar' button when the sidebar is collapsed (WCAG 2.4.3)", () => {
    renderScreen();
    // Collapsing removes the in-sidebar toggle; focus must land on the floating opener.
    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    const opener = screen.getByRole("button", { name: /open sidebar/i });
    expect(opener).toHaveFocus();
  });
});
