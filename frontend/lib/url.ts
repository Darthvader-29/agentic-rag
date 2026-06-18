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

// ---------------------------------------------------------------------------------------------
// Same-origin redirect-target allowlist (open-redirect defense).
//
// A login flow that redirects to a caller-supplied `?next` is an open-redirect sink: an attacker
// crafts `/login?next=https://evil.example/phish` (or the protocol-relative `//evil.example`) and
// the post-login `router.replace(next)` walks the victim straight off-origin. We only ever want to
// resume an in-app location, so `next` MUST be a *relative path on our own origin*. Reject anything
// that could navigate cross-origin: absolute URLs (any scheme, incl. `javascript:`), protocol-
// relative `//host`, and the backslash variants (`/\`, `\/`) that browsers normalize to `//`.

/**
 * True only for a same-origin RELATIVE path safe to `router.replace()` after login — it must start
 * with a single `/` and not be a protocol-relative or absolute URL. Rejects `//host`, `/\host`,
 * `https://…`, `javascript:…`, bare paths without a leading slash, and empty/nullish input.
 */
export function isSafeNextPath(next: string | null | undefined): boolean {
  if (!next) return false;
  // Must be an absolute-path reference: one leading slash, and the next char (if any) must not turn
  // it into a network-path reference. `//x` and `/\x` (and `\/x`, since a leading backslash isn't a
  // path) all resolve to another origin in a browser.
  if (next[0] !== "/") return false;
  if (next[1] === "/" || next[1] === "\\") return false;
  // Defense in depth: if it still parses as an absolute URL against ANY base, it isn't relative.
  // A genuine relative path throws here (no base provided), which is exactly what we want.
  try {
    // eslint-disable-next-line no-new
    new URL(next);
    return false; // parsed as absolute ⇒ reject
  } catch {
    return true; // not absolute ⇒ a real relative path
  }
}

/** The `next` path when it's a safe same-origin relative path, else the `fallback` (default "/"). */
export function safeNextPath(
  next: string | null | undefined,
  fallback = "/"
): string {
  return isSafeNextPath(next) ? (next as string) : fallback;
}
