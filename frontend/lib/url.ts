// lib/url.ts
//
// URL protocol allowlist for any model- or web-authored URL that reaches an `href`/`src`.
// `z.string().url()` validates SYNTAX, not protocol — `new URL("javascript:alert(1)")` succeeds,
// so a `javascript:`/`data:`/`blob:` URL passes `.url()` and, rendered as an anchor `href`, executes
// (XSS). Pass every such URL through here before rendering.

/** True only for an absolute http(s) URL. Rejects javascript:/data:/blob:/mailto:/relative/garbage. */
export function isSafeHttpUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const { protocol } = new URL(url);
    return protocol === "https:" || protocol === "http:";
  } catch {
    return false; // not an absolute URL (relative / protocol-relative / malformed)
  }
}

/** The URL when it's http(s), else `undefined` — for disarming an unsafe href/src to a no-op. */
export function safeHttpUrl(
  url: string | null | undefined
): string | undefined {
  return isSafeHttpUrl(url) ? (url as string) : undefined;
}
