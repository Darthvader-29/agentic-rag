import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// Toggle BYOK / auth per test (R24).
let byok = true;
let auth = true;
vi.mock("@/lib/flags", () => ({
  get flags() {
    return { byok, auth, streaming: false };
  },
  isByokEnabled: () => byok && auth,
}));

// Drive the auth state.
let authState = { isAuthenticated: false, isGuest: false };
vi.mock("@/features/auth/hooks/use-auth", () => ({
  useAuth: () => authState,
}));

// The key form pulls in the TanStack query layer — stub it; we only assert the gating chrome.
vi.mock("@/features/keys/components/api-keys-form", () => ({
  ApiKeysForm: () => <div data-testid="api-keys-form" />,
}));

import { SettingsScreen } from "@/features/keys/components/settings-screen";

beforeEach(() => {
  byok = true;
  auth = true;
  authState = { isAuthenticated: false, isGuest: false };
});

afterEach(() => cleanup());

describe("SettingsScreen — R24 gating", () => {
  it("shows the 'not available' notice (no Sign in CTA) when auth is off", () => {
    // Default config: byok on, auth off → key-saving is impossible, so don't dangle a
    // "Sign in to add keys" CTA that /login can't fulfil.
    auth = false;
    render(<SettingsScreen />);
    expect(
      screen.getByText(/key management isn.?t available yet/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /sign in/i })
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("api-keys-form")).not.toBeInTheDocument();
  });

  it("shows the Sign in CTA when auth is on but the user isn't authenticated", () => {
    auth = true;
    authState = { isAuthenticated: false, isGuest: false };
    render(<SettingsScreen />);
    const link = screen.getByRole("link", { name: /sign in/i });
    expect(link).toHaveAttribute("href", "/login?next=/settings");
  });

  it("shows the key form when authenticated", () => {
    authState = { isAuthenticated: true, isGuest: false };
    render(<SettingsScreen />);
    expect(screen.getByTestId("api-keys-form")).toBeInTheDocument();
  });

  it("shows the 'not available' notice when BYOK is off", () => {
    byok = false;
    render(<SettingsScreen />);
    expect(
      screen.getByText(/key management isn.?t available yet/i)
    ).toBeInTheDocument();
  });
});
