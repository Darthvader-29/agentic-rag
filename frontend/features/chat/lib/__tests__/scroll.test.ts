import { describe, it, expect } from "vitest";
import { isNearBottom } from "../scroll";

describe("isNearBottom (B24 auto-scroll gate)", () => {
  it("is true when the viewport is at the bottom", () => {
    // scrollHeight - scrollTop - clientHeight === 0
    expect(
      isNearBottom({ scrollHeight: 1000, scrollTop: 920, clientHeight: 80 })
    ).toBe(true);
  });

  it("is true within the threshold of the bottom", () => {
    // 50px from the bottom (< default 80)
    expect(
      isNearBottom({ scrollHeight: 1000, scrollTop: 870, clientHeight: 80 })
    ).toBe(true);
  });

  it("is false when the user has scrolled up to read", () => {
    // 820px from the bottom → streaming must NOT scroll them back down
    expect(
      isNearBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 80 })
    ).toBe(false);
  });

  it("respects a custom threshold", () => {
    expect(
      isNearBottom({ scrollHeight: 1000, scrollTop: 800, clientHeight: 80 }, 200)
    ).toBe(true);
  });
});
