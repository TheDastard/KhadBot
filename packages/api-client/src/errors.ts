// packages/api-client/src/errors.ts
//
// ApiError: every failed API call, as one typed error. The API answers errors as `{code, message}` (ErrorBody), and
// a budget wait adds `wait_s` (BudgetWaitBody); a call that never reached the API gets the code `api_unreachable`.
// An error response's `X-Request-ID` header, when valid, is kept as `requestId` so the app can show it as a reference.

import type { components } from "./schema";

export type ErrorBody = components["schemas"]["ErrorBody"];
export type BudgetWaitBody = components["schemas"]["BudgetWaitBody"];

/** The code given to a call that got no HTTP answer at all (network down, API not running, CORS refusal). */
export const API_UNREACHABLE = "api_unreachable";

/** The code given to an error response whose body is not the API's `{code, message}` shape (a proxy page). */
export const UNEXPECTED_RESPONSE = "unexpected_response";

export class ApiError extends Error {
  /** HTTP status, or 0 when the API could not be reached. */
  readonly status: number;
  /** The API's stable error code (`report_not_found`, `budget_exhausted`, ...), or one of the two codes above. */
  readonly code: string;
  /** Seconds to wait before trying again: the body's `wait_s`, else the `Retry-After` header, else null. */
  readonly retryAfterS: number | null;
  /** The parsed error body, when there was one. */
  readonly body: unknown;
  /**
   * The response's `X-Request-ID`, the reference that finds this request in the API's logs. Absent when the call got
   * no answer, the API sent none (or CORS hid it), or the value wasn't a valid ID.
   */
  readonly requestId?: string;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    retryAfterS?: number | null;
    body?: unknown;
    requestId?: string;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.retryAfterS = init.retryAfterS ?? null;
    this.body = init.body;
    this.requestId = init.requestId;
  }
}

/** The response header carrying the API's request ID. */
export const REQUEST_ID_HEADER = "X-Request-ID";

const REQUEST_ID = /^[A-Za-z0-9-]{8,64}$/;

/** A request ID from a header value: 8 to 64 of `[A-Za-z0-9-]`; anything else (or nothing) is undefined. */
export function parseRequestId(header: string | null): string | undefined {
  if (header === null) return undefined;
  const value = header.trim();
  return REQUEST_ID.test(value) ? value : undefined;
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).code === "string" &&
    typeof (value as Record<string, unknown>).message === "string"
  );
}

/** Seconds from a `Retry-After` header: delta-seconds or an HTTP date; null when absent or unreadable. */
export function parseRetryAfter(header: string | null, now: number = Date.now()): number | null {
  if (header === null || header.trim() === "") return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);
  const at = Date.parse(header);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/** The ApiError for an error response and its parsed body. */
export function errorFromResponse(response: Response, body: unknown): ApiError {
  const headerWait = parseRetryAfter(response.headers.get("Retry-After"));
  const requestId = parseRequestId(response.headers.get(REQUEST_ID_HEADER));
  if (isErrorBody(body)) {
    const wait = (body as Partial<BudgetWaitBody>).wait_s;
    return new ApiError({
      status: response.status,
      code: body.code,
      message: body.message,
      retryAfterS: typeof wait === "number" ? wait : headerWait,
      body,
      requestId,
    });
  }
  return new ApiError({
    status: response.status,
    code: UNEXPECTED_RESPONSE,
    message: `The API answered ${response.status} without an error body.`,
    retryAfterS: headerWait,
    body,
    requestId,
  });
}

/** The ApiError for a call that threw before any response (fetch rejects with a TypeError then). */
export function unreachable(cause: unknown): ApiError {
  const err = new ApiError({ status: 0, code: API_UNREACHABLE, message: "KhadBot isn't responding." });
  Object.defineProperty(err, "cause", { value: cause });
  return err;
}
