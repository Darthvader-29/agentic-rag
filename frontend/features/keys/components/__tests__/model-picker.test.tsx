import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Toggle BYOK / auth per test (R24: the picker is hidden unless BOTH are on).
let byok = true;
let auth = true;
vi.mock("@/lib/flags", () => ({
  get flags() {
    return { byok, auth, streaming: false };
  },
  isByokEnabled: () => byok && auth,
}));

// Control which providers have a stored key (R25 drives off this list).
let keys: { id: string; provider: string }[] = [];
vi.mock("@/features/keys/hooks/use-api-keys", () => ({
  useApiKeys: () => ({ keys }),
}));

import { ModelPicker } from "@/features/keys/components/model-picker";
import { useProviderStore } from "@/features/keys/store/provider.store";

// Radix DropdownMenu needs a few DOM APIs jsdom doesn't implement.
beforeEach(() => {
  byok = true;
  auth = true;
  keys = [];
  useProviderStore.setState({ provider: null, model: null });
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

describe("ModelPicker — R24 (hidden when auth off)", () => {
  it("renders nothing when BYOK is on but auth is off", () => {
    auth = false;
    const { container } = render(<ModelPicker />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when BYOK is off", () => {
    byok = false;
    const { container } = render(<ModelPicker />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the trigger when BYOK + auth are both on", () => {
    render(<ModelPicker />);
    expect(
      screen.getByRole("button", { name: /select model/i })
    ).toBeInTheDocument();
  });
});

describe("ModelPicker — R25 (unowned providers disabled)", () => {
  it("disables models for providers with no stored key and offers Add key", async () => {
    const user = userEvent.setup();
    keys = [{ id: "1", provider: "gemini" }]; // owns gemini only
    render(<ModelPicker />);

    await user.click(screen.getByRole("button", { name: /select model/i }));
    const menu = await screen.findByRole("menu");

    // Owned (gemini) → enabled menu item.
    const geminiItem = within(menu).getByRole("menuitem", {
      name: /gemini 2\.5 flash/i,
    });
    expect(geminiItem).not.toHaveAttribute("data-disabled");

    // Unowned (openai) → disabled menu item + an "Add key" affordance.
    const openaiItem = within(menu).getByRole("menuitem", {
      name: /add an? openai key to use/i,
    });
    expect(openaiItem).toHaveAttribute("data-disabled");

    const addKeyLinks = within(menu).getAllByRole("link", { name: /add key/i });
    expect(addKeyLinks.length).toBeGreaterThan(0);
    expect(addKeyLinks[0]).toHaveAttribute("href", "/settings");
  });

  it("falls back to Auto when the selected provider's key is gone", () => {
    // Pre-seed a selection for a provider the user does NOT own.
    useProviderStore.setState({ provider: "openai", model: "gpt-4o" });
    keys = []; // no keys at all
    render(<ModelPicker />);
    // The reset effect clears the stale selection so the next turn can't break.
    expect(useProviderStore.getState().provider).toBeNull();
  });
});
