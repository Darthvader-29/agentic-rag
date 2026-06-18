/**
 * Discriminates failures so callers (and the auth interceptor) can branch.
 *  - unauthorized → 401, refresh exhausted / no token (triggers login redirect)
 *  - forbidden    → 403, cross-user ownership (terminal — never retried/refreshed)
 *  - rate_limited → 429, throttled (slowapi); surfaced as a friendly back-off message
 *  - http         → other non-2xx
 *  - network      → fetch threw (offline, DNS, CORS preflight)
 *  - timeout      → the request exceeded its timeout and was aborted (hung backend)
 *  - parse        → response body failed Zod validation
 */
export type ApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "rate_limited"
  | "http"
  | "network"
  | "timeout"
  | "parse";

/** Machine-readable code the backend stamps on a throttled response ({detail, code}). */
export const RATE_LIMITED_CODE = "rate_limited";

/** User-facing copy for a 429 — friendly + actionable, never the raw "Backend error: 429". */
export const RATE_LIMITED_MESSAGE =
  "You're sending requests too quickly. Please wait a moment and try again.";

/**
 * Detect a rate-limit (429) response across BOTH envelope shapes:
 *  - the backend's normalized `{ detail, code: "rate_limited" }` (R27), and
 *  - slowapi's raw `{ error: "Rate limit exceeded: ..." }` (a backend not yet updated).
 * A bare 429 with any body is treated as rate-limited so the user never sees "Backend error: 429".
 */
export function isRateLimitPayload(status: number, payload: unknown): boolean {
  if (status === 429) return true;
  if (payload && typeof payload === "object") {
    const code = (payload as { code?: unknown }).code;
    if (code === RATE_LIMITED_CODE) return true;
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string" && /rate limit/i.test(error)) return true;
  }
  return false;
}

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ApiErrorKind;
  readonly detail?: string;
  readonly payload?: unknown;

  constructor(args: {
    message: string;
    status: number;
    kind?: ApiErrorKind;
    detail?: string;
    payload?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.status = args.status;
    this.kind = args.kind ?? "http";
    this.detail = args.detail;
    this.payload = args.payload;
    Object.setPrototypeOf(this, ApiError.prototype);
  }

  get userMessage(): string {
    // A throttle always reads as the friendly back-off copy, regardless of which envelope the
    // backend sent — slowapi's raw "{error: 'Rate limit exceeded: ...'}" would otherwise fall
    // through to a generic "Backend error: 429".
    if (this.isRateLimited) return RATE_LIMITED_MESSAGE;
    return this.detail ?? this.message;
  }

  get isForbidden(): boolean {
    return this.kind === "forbidden";
  }

  get isUnauthorized(): boolean {
    return this.kind === "unauthorized";
  }

  get isRateLimited(): boolean {
    return this.kind === "rate_limited" || this.status === 429;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
