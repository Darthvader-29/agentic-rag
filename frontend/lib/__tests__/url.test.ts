import { describe, it, expect } from "vitest";
import {
  isSafeHttpUrl,
  safeHttpUrl,
  isSafeNextPath,
  safeNextPath,
} from "@/lib/url";

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

describe("isSafeNextPath (open-redirect defense for ?next)", () => {
  it("accepts same-origin relative paths", () => {
    for (const ok of [
      "/",
      "/chat",
      "/settings?tab=keys",
      "/a/b/c#section",
      "/path%20with%20encoding",
    ]) {
      expect(isSafeNextPath(ok)).toBe(true);
    }
  });

  it("rejects absolute, protocol-relative, and off-origin targets", () => {
    for (const bad of [
      "https://evil.example/phish", // absolute http(s)
      "http://evil.example",
      "//evil.example", // protocol-relative → another origin
      "/\\evil.example", // backslash variant browsers normalize to //
      "\\/evil.example", // leading backslash isn't a path
      "\\\\evil.example",
      "javascript:alert(1)", // not even path-shaped
      "data:text/html,x",
      "mailto:a@b.com",
      "relative/no-leading-slash",
      "", // empty
      null,
      undefined,
    ]) {
      expect(isSafeNextPath(bad as string)).toBe(false);
    }
  });
});

describe("safeNextPath", () => {
  it("returns the path when safe, else the fallback (default '/')", () => {
    expect(safeNextPath("/chat")).toBe("/chat");
    expect(safeNextPath("https://evil.example")).toBe("/");
    expect(safeNextPath("//evil.example")).toBe("/");
    expect(safeNextPath(null)).toBe("/");
    expect(safeNextPath("https://evil.example", "/home")).toBe("/home");
  });
});
