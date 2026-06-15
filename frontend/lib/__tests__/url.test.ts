import { describe, it, expect } from "vitest";
import { isSafeHttpUrl, safeHttpUrl } from "@/lib/url";

describe("isSafeHttpUrl", () => {
  it("accepts absolute http and https URLs", () => {
    expect(isSafeHttpUrl("https://example.com/x?y=1")).toBe(true);
    expect(isSafeHttpUrl("http://example.com")).toBe(true);
  });

  it("rejects dangerous protocols, relative URLs, and garbage", () => {
    for (const bad of [
      "javascript:alert(1)",
      "JavaScript:alert(1)", // protocol is case-insensitive
      "  javascript:alert(1)", // URL() trims leading control/space
      "data:text/html,<script>1</script>",
      "blob:https://evil/x",
      "mailto:a@b.com",
      "/relative/path",
      "//protocol-relative.com",
      "not a url",
      "",
      null,
      undefined,
    ]) {
      expect(isSafeHttpUrl(bad as string)).toBe(false);
    }
  });
});

describe("safeHttpUrl", () => {
  it("returns the URL when http(s), else undefined", () => {
    expect(safeHttpUrl("https://x.com")).toBe("https://x.com");
    expect(safeHttpUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeHttpUrl(null)).toBeUndefined();
  });
});
