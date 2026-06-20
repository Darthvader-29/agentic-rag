import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the http-client so we assert how authApi shapes the request (not the network).
const request = vi.fn();
vi.mock("@/lib/api/http-client", () => ({
  request: (...a: unknown[]) => request(...a),
}));

import { authApi } from "@/features/auth/api/auth.api";

beforeEach(() => request.mockReset());

describe("authApi.logout (R03)", () => {
  it("POSTs the refresh token to /auth/logout as a public call (no bearer)", async () => {
    request.mockResolvedValue(undefined);
    await authApi.logout({ refresh_token: "rt-123" });
    expect(request).toHaveBeenCalledWith(
      "/auth/logout",
      expect.objectContaining({
        method: "POST",
        body: { refresh_token: "rt-123" },
        auth: false,
      })
    );
  });
});
