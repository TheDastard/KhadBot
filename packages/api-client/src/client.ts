// packages/api-client/src/client.ts
//
// The typed fetch wrapper over openapi-fetch. Requests send the session cookie (`credentials: "include"`) unless the
// client is made without credentials; every state-changing request (POST, PUT, PATCH, DELETE) also sends the CSRF
// header the API requires (`X-KhadBot-CSRF: 1`, src/khadbot/auth/routes.py). `unwrap` turns a response into its data
// or throws an ApiError.

import createClient, { type Client, type ClientPathsWithMethod, type MethodResponse } from "openapi-fetch";

import { errorFromResponse, unreachable } from "./errors";
import type { paths } from "./schema";

export const CSRF_HEADER = "X-KhadBot-CSRF";
export const CSRF_VALUE = "1";

const STATE_CHANGING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export type ApiClient = Client<paths>;

/**
 * The data a GET route answers, as the client types it: `ApiGet<"/v1/reports/{code}/pulls/{fight}/digest">`. Use it
 * rather than `Schema<"PullDigest">` for response payloads: openapi-fetch reads a few shapes (tuples) more loosely
 * than the schema, so a Schema type may not accept what a call returns.
 */
export type ApiGet<Path extends ClientPathsWithMethod<ApiClient, "get">> = MethodResponse<ApiClient, "get", Path>;

export interface ApiClientOptions {
  /** The API's origin, from NEXT_PUBLIC_API_BASE or the app's own setting. No default: the caller always says. */
  baseUrl: string;
  /**
   * Send the session cookie (`credentials: "include"`, the default). A server without sign-in (`main.py serve`)
   * answers CORS without credentials, so a client for it, or for `/v1/health` before the app knows, omits them.
   */
  withCredentials?: boolean;
  /** A fetch to use instead of the global one (tests). */
  fetch?: (input: Request) => Promise<Response>;
}

/** A client for the KhadBot API at `baseUrl`. */
export function createApiClient(options: ApiClientOptions): ApiClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl.replace(/\/+$/, ""),
    credentials: options.withCredentials === false ? "omit" : "include",
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
  client.use({
    onRequest({ request }) {
      if (STATE_CHANGING.has(request.method.toUpperCase())) {
        request.headers.set(CSRF_HEADER, CSRF_VALUE);
      }
      return request;
    },
  });
  return client;
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/**
 * The data of a call, or an ApiError: the API's `{code, message}` for an error response, `api_unreachable` when the
 * request never got an answer. A 204 resolves to `undefined`.
 *
 *     const health = await unwrap(api.GET("/v1/health"));
 */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await call;
  } catch (cause) {
    throw unreachable(cause);
  }
  if (!result.response.ok) {
    throw errorFromResponse(result.response, result.error);
  }
  return result.data as T;
}

/** The absolute URL of an API path, for what openapi-fetch doesn't cover (an EventSource, a sign-in link). */
export function apiUrl(baseUrl: string, path: string, query?: Record<string, string | number | undefined>): string {
  const url = new URL(baseUrl.replace(/\/+$/, "") + path);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

/** A server-sent event stream from the API, with the session cookie (job progress: `/v1/jobs/{id}/events`). */
export function openEventStream(baseUrl: string, path: string): EventSource {
  return new EventSource(apiUrl(baseUrl, path), { withCredentials: true });
}
