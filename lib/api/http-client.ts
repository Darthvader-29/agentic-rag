import type { ZodType } from "zod";
import { env } from "@/lib/env";
import { ApiError } from "./api-error";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions<T> {
  method?: HttpMethod;
  body?: unknown;
  schema?: ZodType<T>;
  /** Dormant in M1. When true (M6), attaches Bearer + 401-refresh-retry. */
  auth?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

const BASE_URL = env.NEXT_PUBLIC_API_URL;

// Dormant auth interceptor seam — wired in M6.
async function applyAuth(
  headers: Headers,
  _auth: boolean | undefined
): Promise<Headers> {
  // M6: if (_auth && flags.auth) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

export async function request<T = void>(
  path: string,
  opts: RequestOptions<T> = {}
): Promise<T> {
  const { method = "GET", body, schema, auth, signal, headers: extra } = opts;

  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const headers = new Headers(extra);
  if (!isForm && body !== undefined)
    headers.set("Content-Type", "application/json");
  await applyAuth(headers, auth);

  const url = `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body:
        body === undefined
          ? undefined
          : isForm
            ? (body as FormData)
            : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError({
      message: e instanceof Error ? e.message : "Network request failed",
      status: 0,
      payload: e,
    });
  }

  if (!res.ok) {
    let detail: string | undefined;
    let payload: unknown;
    try {
      payload = await res.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        const d = (payload as { detail?: unknown }).detail;
        if (typeof d === "string") detail = d;
      }
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError({
      message: detail ?? `Backend error: ${res.status}`,
      status: res.status,
      detail,
      payload,
    });
  }

  if (res.status === 204 || !schema) return undefined as T;

  let json: unknown;
  try {
    json = await res.json();
  } catch (e) {
    throw new ApiError({
      message: "Response was not valid JSON",
      status: res.status,
      payload: e,
    });
  }

  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new ApiError({
      message: "Response failed schema validation",
      status: res.status,
      detail: parsed.error.message,
      payload: json,
    });
  }
  return parsed.data;
}
