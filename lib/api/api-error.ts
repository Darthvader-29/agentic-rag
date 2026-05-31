export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly payload?: unknown;

  constructor(args: {
    message: string;
    status: number;
    detail?: string;
    payload?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.status = args.status;
    this.detail = args.detail;
    this.payload = args.payload;
    Object.setPrototypeOf(this, ApiError.prototype);
  }

  get userMessage(): string {
    return this.detail ?? this.message;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
