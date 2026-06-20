import { describe, it, expect } from "vitest";
import { buildCsp, securityHeaders } from "@/lib/security-headers";

describe("buildCsp", () => {
  it("allow-lists the API origin in connect-src (fetch + SSE)", () => {
    const csp = buildCsp({
      apiUrl: "https://api.example.com/api",
      isDev: false,
    });
    expect(csp).toContain("connect-src 'self' https://api.example.com");
  });

  it("locks down framing, objects, base-uri, and defaults to 'self'", () => {
    const csp = buildCsp({ isDev: false });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
  });

  it("relaxes for dev only ('unsafe-eval' + ws: for HMR)", () => {
    const dev = buildCsp({ isDev: true });
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).toContain("ws:");
    const prod = buildCsp({ isDev: false });
    expect(prod).not.toContain("'unsafe-eval'");
    expect(prod).not.toContain("ws:");
  });

  it("tolerates a missing or malformed API url", () => {
    expect(() => buildCsp({ isDev: false })).not.toThrow();
    expect(() => buildCsp({ apiUrl: "not a url", isDev: false })).not.toThrow();
    // 'self' is always present even when the origin can't be derived.
    expect(buildCsp({ apiUrl: "not a url", isDev: false })).toContain(
      "connect-src 'self'"
    );
  });
});

describe("securityHeaders", () => {
  it("includes the core headers and omits HSTS in dev", () => {
    const keys = securityHeaders({ isDev: true }).map((h) => h.key);
    expect(keys).toEqual(
      expect.arrayContaining([
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
      ])
    );
    expect(keys).not.toContain("Strict-Transport-Security");
  });

  it("adds HSTS in production", () => {
    const prod = securityHeaders({ isDev: false });
    expect(
      prod.find((h) => h.key === "Strict-Transport-Security")?.value
    ).toContain("max-age=");
  });

  it("sets X-Frame-Options DENY and nosniff", () => {
    const h = securityHeaders({ isDev: false });
    expect(h.find((x) => x.key === "X-Frame-Options")?.value).toBe("DENY");
    expect(h.find((x) => x.key === "X-Content-Type-Options")?.value).toBe(
      "nosniff"
    );
  });
});
