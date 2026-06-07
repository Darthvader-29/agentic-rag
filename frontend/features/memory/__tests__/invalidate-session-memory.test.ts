import { describe, it, expect, vi, beforeEach } from "vitest";
import type { QueryClient } from "@tanstack/react-query";

// Drive the memory flag per branch.
const { flagsMock } = vi.hoisted(() => ({ flagsMock: { memory: true } }));
vi.mock("@/lib/flags", () => ({ flags: flagsMock }));

import {
  invalidateSessionMemory,
  sessionMemoryQueryKey,
} from "@/features/memory/hooks/use-session-memory";

const fakeQc = () => ({ invalidateQueries: vi.fn() }) as unknown as QueryClient;

beforeEach(() => {
  flagsMock.memory = true;
});

describe("invalidateSessionMemory", () => {
  it("invalidates the session-scoped memory query when the flag is on", () => {
    const qc = fakeQc();
    invalidateSessionMemory(qc, "s1");
    expect(qc.invalidateQueries).toHaveBeenCalledWith({
      queryKey: sessionMemoryQueryKey("s1"),
    });
  });

  it("is a no-op when the memory feature is dark", () => {
    flagsMock.memory = false;
    const qc = fakeQc();
    invalidateSessionMemory(qc, "s1");
    expect(qc.invalidateQueries).not.toHaveBeenCalled();
  });

  it("is a no-op when there is no session id", () => {
    const qc = fakeQc();
    invalidateSessionMemory(qc, "");
    expect(qc.invalidateQueries).not.toHaveBeenCalled();
  });
});
