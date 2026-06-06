import { z } from "zod";

/**
 * The message of a ZodError's first issue, or a fallback when there is none.
 * Mirrors the `error.issues[0]?.message ?? fallback` pattern used by the forms.
 */
export function firstIssueMessage(error: z.ZodError, fallback: string): string {
  return error.issues[0]?.message ?? fallback;
}
